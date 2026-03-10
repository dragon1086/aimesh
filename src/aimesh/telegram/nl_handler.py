"""Natural language message handler for Telegram.

Intercepts non-command text messages, runs keyword pre-filter,
and forwards unmatched messages to PM as MessageType.CHAT.
"""

import asyncio
from datetime import datetime, timezone

import structlog

from aimesh.core.bus import AbstractMessageBus
from aimesh.core.message import MeshMessage, MessageType
from aimesh.nl.context import ContextEntry, ConversationContext
from aimesh.nl.prefilter import prefilter_message
from aimesh.telegram.handlers import CommandHandlers
from aimesh.tasks.tracker import TaskTracker

logger = structlog.get_logger("nl_handler")


class NaturalLanguageHandler:
    """Handles non-command text messages via keyword pre-filter + PM routing."""

    def __init__(
        self,
        bus: AbstractMessageBus,
        tracker: TaskTracker,
        context: ConversationContext,
        handlers: CommandHandlers,
        bot_username: str | None = None,
    ) -> None:
        self.bus = bus
        self.tracker = tracker
        self.context = context
        self.handlers = handlers
        self.bot_username = bot_username
        self._pending_responses: dict[str, asyncio.Future] = {}

    async def handle_message(self, update, tg_context) -> None:
        """Handle a non-command text message from Telegram."""
        if not update.message or not update.message.text or not update.effective_user:
            return

        text = update.message.text.strip()
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name or str(user_id)
        chat_id = update.effective_chat.id if update.effective_chat else 0

        # Multi-team @mention filtering
        if self.bot_username and f"@{self.bot_username}" not in text:
            # If bot_username is set (multi-team mode), only respond to @mentions
            # or unmentioned messages (handled by dedup layer above)
            if "@" in text:
                # Message mentions a different bot — ignore
                return

        # Strip @mention from text if present
        if self.bot_username:
            text = text.replace(f"@{self.bot_username}", "").strip()

        if not text:
            return

        # Add to conversation context
        self.context.add(chat_id, ContextEntry(
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            user_name=user_name,
            text=text,
        ))

        # Try keyword pre-filter first
        active_tasks = self.tracker.get_active_tasks()
        prefilter_result = prefilter_message(text, active_tasks)

        if prefilter_result is not None:
            response = await self._handle_prefilter(prefilter_result, user_id)
            await update.message.reply_text(response)
            self._add_bot_response(chat_id, response)
            return

        # Forward to PM as CHAT message
        response = await self._forward_to_pm(text, chat_id, user_id, user_name)
        if response:
            await update.message.reply_text(response)
            self._add_bot_response(chat_id, response)

    async def _handle_prefilter(self, result, user_id: int) -> str:
        """Execute a pre-filtered command directly via CommandHandlers."""
        if result.action == "approve" and result.target_task_id:
            return await self.handlers.handle_approve(user_id, result.target_task_id)
        elif result.action == "reject" and result.target_task_id:
            feedback = result.feedback or "Please revise."
            return await self.handlers.handle_reject(user_id, result.target_task_id, feedback)
        elif result.action == "status":
            return await self.handlers.handle_status(user_id)
        elif result.action == "cancel" and result.target_task_id:
            return await self.handlers.handle_cancel(user_id, result.target_task_id)
        elif result.action == "approve":
            return "No task to approve. Please specify which task."
        elif result.action == "reject":
            return "No task to reject. Please specify which task."
        elif result.action == "cancel":
            return "No task to cancel. Please specify which task."
        return "Command recognized but couldn't be executed."

    async def _forward_to_pm(
        self, text: str, chat_id: int, user_id: int, user_name: str
    ) -> str:
        """Forward message to PM as CHAT and wait for response."""
        conversation_context = self.context.format_for_pm(chat_id)

        msg = MeshMessage(
            sender="human",
            recipient="pm",
            msg_type=MessageType.CHAT,
            content=text,
            metadata={
                "conversation_context": conversation_context,
                "user_id": user_id,
                "user_name": user_name,
                "chat_id": chat_id,
            },
        )

        # Set up response future
        future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        self._pending_responses[msg.id] = future

        # Publish to bus
        await self.bus.publish(msg)

        # Wait for PM response (with timeout)
        try:
            response = await asyncio.wait_for(future, timeout=30.0)
            return response
        except asyncio.TimeoutError:
            logger.warning("pm_chat_response_timeout", message_id=msg.id)
            return "Sorry, I'm taking too long to process that. Please try again."
        finally:
            self._pending_responses.pop(msg.id, None)

    async def handle_pm_response(self, message: MeshMessage) -> None:
        """Handle PM's CHAT response — resolve pending future."""
        if message.parent_id and message.parent_id in self._pending_responses:
            future = self._pending_responses[message.parent_id]
            if not future.done():
                future.set_result(message.content)

    def _add_bot_response(self, chat_id: int, text: str) -> None:
        """Add bot response to conversation context."""
        self.context.add(chat_id, ContextEntry(
            timestamp=datetime.now(timezone.utc),
            user_id=0,
            user_name="bot",
            text=text,
            is_bot=True,
        ))
