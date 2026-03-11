"""Display layer: subscribes to message bus and posts agent activity to Telegram.

Implements rate limiting:
- STATUS_UPDATE debounced at 1 msg/agent/5sec
- RESULT and TASK_ASSIGN bypass debounce (immediate)
"""

import asyncio
import time

import structlog

from aimesh.core.bus import AbstractMessageBus
from aimesh.core.message import MeshMessage, MessageType
from aimesh.telegram.formatter import format_message

logger = structlog.get_logger("display")

# Message types that bypass debounce (always sent immediately)
PRIORITY_TYPES = {
    MessageType.RESULT, MessageType.TASK_ASSIGN, MessageType.REVIEW_REQUEST,
    MessageType.HAND_RAISE, MessageType.COLLAB_REQUEST,
    MessageType.COLLAB_ACCEPT, MessageType.COLLAB_RESULT,
}

DEBOUNCE_SECONDS = 5.0


class TelegramDisplay:
    """Posts agent activity to a Telegram group with rate limiting."""

    def __init__(
        self,
        bus: AbstractMessageBus,
        chat_id: int,
        send_fn=None,
        debounce_seconds: float = DEBOUNCE_SECONDS,
    ) -> None:
        self.bus = bus
        self.chat_id = chat_id
        self._send_fn = send_fn  # async callable(chat_id, text, parse_mode)
        self.debounce_seconds = debounce_seconds
        # Per-agent debounce tracking
        self._last_sent: dict[str, float] = {}
        self._pending: dict[str, MeshMessage] = {}
        self._flush_tasks: dict[str, asyncio.Task] = {}

    async def start(self) -> None:
        """Subscribe to the bus as a display listener."""
        await self.bus.subscribe("display", self._on_message)
        logger.info("display_started", chat_id=self.chat_id)

    async def stop(self) -> None:
        """Unsubscribe and cancel pending flush tasks."""
        await self.bus.unsubscribe("display")
        for task in self._flush_tasks.values():
            task.cancel()
        self._flush_tasks.clear()
        logger.info("display_stopped")

    async def _on_message(self, message: MeshMessage) -> None:
        """Handle a message from the bus. Apply rate limiting."""
        # Priority messages bypass debounce
        if message.msg_type in PRIORITY_TYPES:
            await self._send_to_telegram(message)
            return

        # Apply debounce for STATUS_UPDATE and other non-priority types
        agent_id = message.sender
        now = time.monotonic()
        last = self._last_sent.get(agent_id, 0)

        if now - last >= self.debounce_seconds:
            # Enough time passed, send immediately
            await self._send_to_telegram(message)
        else:
            # Debounce: store pending and schedule flush
            self._pending[agent_id] = message  # Keep only latest
            if agent_id not in self._flush_tasks or self._flush_tasks[agent_id].done():
                wait_time = self.debounce_seconds - (now - last)
                self._flush_tasks[agent_id] = asyncio.create_task(
                    self._flush_after(agent_id, wait_time)
                )

    async def _flush_after(self, agent_id: str, delay: float) -> None:
        """Flush the pending message for an agent after delay."""
        await asyncio.sleep(delay)
        msg = self._pending.pop(agent_id, None)
        if msg:
            await self._send_to_telegram(msg)

    async def _send_to_telegram(self, message: MeshMessage) -> None:
        """Send a formatted message to the Telegram group."""
        self._last_sent[message.sender] = time.monotonic()
        text = format_message(message)

        if self._send_fn:
            try:
                await self._send_fn(self.chat_id, text, "MarkdownV2")
            except Exception:
                logger.exception("telegram_send_error", message_id=message.id)
        else:
            # No send function configured (testing mode or not connected)
            logger.info("display_message", sender=message.sender,
                        msg_type=message.msg_type.value, text_length=len(text))
