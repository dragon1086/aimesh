"""Tests for NaturalLanguageHandler (Phase 2b)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from aimesh.core.bus import AsyncioMessageBus
from aimesh.core.message import MeshMessage, MessageType
from aimesh.core.task import Task, TaskState
from aimesh.nl.context import ConversationContext
from aimesh.nl.prefilter import PrefilterResult
from aimesh.tasks.tracker import TaskTracker
from aimesh.telegram.handlers import CommandHandlers
from aimesh.telegram.nl_handler import NaturalLanguageHandler


def _make_nl_handler(bus=None, tracker=None, handlers=None):
    """Create a NaturalLanguageHandler with mocked dependencies."""
    bus = bus or AsyncioMessageBus()
    tracker = tracker or TaskTracker()
    context = ConversationContext()

    if handlers is None:
        handlers = MagicMock(spec=CommandHandlers)
        handlers.handle_status = AsyncMock(return_value="No active tasks.")
        handlers.handle_approve = AsyncMock(return_value="Task approved.")
        handlers.handle_reject = AsyncMock(return_value="Task rejected.")
        handlers.handle_cancel = AsyncMock(return_value="Task cancelled.")

    return NaturalLanguageHandler(
        bus=bus, tracker=tracker, context=context, handlers=handlers,
    )


def _make_update(text: str, user_id: int = 123, chat_id: int = -100):
    """Create a mock Telegram Update."""
    update = MagicMock()
    update.message.text = text
    update.effective_user.id = user_id
    update.effective_user.first_name = "TestUser"
    update.effective_chat.id = chat_id
    update.message.reply_text = AsyncMock()
    return update


async def test_prefilter_shortcut_status():
    """'상태' is handled directly by pre-filter without PM."""
    handler = _make_nl_handler()
    update = _make_update("상태")

    await handler.handle_message(update, None)

    # Should call handlers.handle_status directly
    handler.handlers.handle_status.assert_called_once_with(123)
    update.message.reply_text.assert_called_once()


async def test_prefilter_shortcut_approve():
    """'승인' with 1 REVIEW task is handled by pre-filter."""
    tracker = TaskTracker()
    task = Task(title="Test", description="Test")
    task.transition(TaskState.DECOMPOSING)
    task.transition(TaskState.ASSIGNED)
    task.transition(TaskState.IN_PROGRESS)
    task.transition(TaskState.REVIEW)
    tracker.add(task)

    handler = _make_nl_handler(tracker=tracker)
    update = _make_update("승인")

    await handler.handle_message(update, None)

    handler.handlers.handle_approve.assert_called_once_with(123, task.id)


async def test_forward_to_pm_as_chat():
    """Non-matching text is forwarded to PM as MessageType.CHAT."""
    bus = AsyncioMessageBus()
    tracker = TaskTracker()

    handler = _make_nl_handler(bus=bus, tracker=tracker)

    published_msgs = []
    original_publish = bus.publish

    async def capture_publish(msg):
        published_msgs.append(msg)
        await original_publish(msg)

    bus.publish = capture_publish

    # Subscribe PM to respond
    async def pm_auto_respond(msg: MeshMessage):
        if msg.msg_type == MessageType.CHAT:
            response = MeshMessage(
                sender="pm", recipient="human",
                msg_type=MessageType.CHAT,
                content="Task created: login page",
                parent_id=msg.id,
            )
            await original_publish(response)

    await bus.subscribe("pm", pm_auto_respond)
    await bus.subscribe("human", handler.handle_pm_response)

    update = _make_update("로그인 페이지 만들어줘")
    await handler.handle_message(update, None)

    # Should have published a CHAT message to PM
    chat_msgs = [m for m in published_msgs if m.msg_type == MessageType.CHAT and m.recipient == "pm"]
    assert len(chat_msgs) >= 1
    assert chat_msgs[0].content == "로그인 페이지 만들어줘"
    assert "conversation_context" in chat_msgs[0].metadata


async def test_conversation_context_maintained():
    """Messages are added to conversation context."""
    handler = _make_nl_handler()

    # Send a status query (pre-filter match, no PM needed)
    update = _make_update("status", chat_id=-100)
    await handler.handle_message(update, None)

    # Context should have the message
    entries = handler.context.get_recent(-100)
    assert len(entries) >= 1
    assert entries[0].text == "status"


async def test_empty_message_ignored():
    """Empty or whitespace-only messages are ignored."""
    handler = _make_nl_handler()
    update = _make_update("   ")
    update.message.text = "   "

    await handler.handle_message(update, None)

    update.message.reply_text.assert_not_called()


async def test_mention_filtering():
    """In multi-team mode, messages mentioning other bots are ignored."""
    handler = _make_nl_handler()
    handler.bot_username = "team1_bot"

    # Message for another bot
    update = _make_update("@team2_bot 상태")
    await handler.handle_message(update, None)

    update.message.reply_text.assert_not_called()


async def test_pm_response_correlation():
    """handle_pm_response resolves pending futures by parent_id."""
    handler = _make_nl_handler()

    # Create a pending future
    loop = asyncio.get_event_loop()
    future = loop.create_future()
    handler._pending_responses["msg-123"] = future

    # Simulate PM response
    response = MeshMessage(
        sender="pm", recipient="human",
        msg_type=MessageType.CHAT,
        content="Done!",
        parent_id="msg-123",
    )
    await handler.handle_pm_response(response)

    assert future.done()
    assert future.result() == "Done!"
