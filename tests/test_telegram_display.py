"""Tests for Telegram display layer: formatter and rate limiting."""

import asyncio
import time

from aimesh.core.bus import AsyncioMessageBus
from aimesh.core.message import MeshMessage, MessageType
from aimesh.telegram.display import TelegramDisplay
from aimesh.telegram.formatter import (
    format_agents_list,
    format_message,
    format_status_summary,
    get_persona_prefix,
)


# --- Formatter tests ---

def test_persona_prefix_pm():
    assert get_persona_prefix("pm") == "[PM]"


def test_persona_prefix_human():
    assert get_persona_prefix("human") == "[Human]"


def test_persona_prefix_coder():
    assert get_persona_prefix("coder-1") == "[Coder-1]"


def test_persona_prefix_researcher():
    assert get_persona_prefix("researcher-1") == "[Researcher-1]"


def test_format_task_assign():
    msg = MeshMessage(
        sender="pm", recipient="coder-1",
        msg_type=MessageType.TASK_ASSIGN, content="Build the API",
    )
    text = format_message(msg)
    assert "[PM]" in text
    assert "Task assigned" in text


def test_format_result_with_branch():
    msg = MeshMessage(
        sender="coder-1", recipient="pm",
        msg_type=MessageType.RESULT, content="Code complete",
        metadata={"branch": "task/abc-123", "cost_usd": 0.05},
    )
    text = format_message(msg)
    assert "[Coder-1]" in text
    assert "Result" in text
    assert "task/abc-123" in text


def test_format_status_update():
    msg = MeshMessage(
        sender="coder-1", recipient="broadcast",
        msg_type=MessageType.STATUS_UPDATE, content="Working on feature X",
    )
    text = format_message(msg)
    assert "[Coder-1]" in text
    assert "Working on feature X" in text.replace("\\", "")


def test_format_system_message():
    msg = MeshMessage(
        sender="pm", recipient="broadcast",
        msg_type=MessageType.SYSTEM, content="Agent coder-1 online",
    )
    text = format_message(msg)
    assert "[PM]" in text


def test_format_status_summary_empty():
    result = format_status_summary([])
    assert "No active tasks" in result


def test_format_status_summary_with_tasks():
    tasks = [
        {"title": "Build API", "state": "in_progress", "assigned_to": "coder-1"},
        {"title": "Research DB", "state": "assigned", "assigned_to": "researcher-1"},
    ]
    result = format_status_summary(tasks)
    assert "Active Tasks" in result
    assert "Build API" in result.replace("\\", "")


def test_format_agents_list_empty():
    result = format_agents_list([])
    assert "No agents" in result


def test_format_agents_list_with_agents():
    agents = [
        {"agent_id": "pm", "agent_type": "pm", "status": "idle"},
        {"agent_id": "coder-1", "agent_type": "coder", "status": "busy"},
    ]
    result = format_agents_list(agents)
    assert "[PM]" in result
    assert "[Coder-1]" in result


# --- Display rate limiting tests ---

async def test_priority_messages_bypass_debounce():
    """RESULT and TASK_ASSIGN should be sent immediately."""
    bus = AsyncioMessageBus()
    sent = []

    async def mock_send(chat_id, text, parse_mode):
        sent.append({"text": text, "time": time.monotonic()})

    display = TelegramDisplay(bus=bus, chat_id=123, send_fn=mock_send, debounce_seconds=5.0)
    await display.start()

    # Send two RESULT messages rapidly (should both go through immediately)
    msg1 = MeshMessage(
        sender="coder-1", recipient="display",
        msg_type=MessageType.RESULT, content="Result 1",
    )
    msg2 = MeshMessage(
        sender="coder-1", recipient="display",
        msg_type=MessageType.TASK_ASSIGN, content="Task 1",
    )

    await display._on_message(msg1)
    await display._on_message(msg2)

    assert len(sent) == 2  # Both sent immediately, no debounce
    await display.stop()
    await bus.shutdown()


async def test_status_update_debounced():
    """STATUS_UPDATE should be debounced — only latest sent after window."""
    bus = AsyncioMessageBus()
    sent = []

    async def mock_send(chat_id, text, parse_mode):
        sent.append({"text": text, "time": time.monotonic()})

    display = TelegramDisplay(bus=bus, chat_id=123, send_fn=mock_send, debounce_seconds=0.3)
    await display.start()

    # First STATUS_UPDATE goes through (no prior message)
    msg1 = MeshMessage(
        sender="coder-1", recipient="display",
        msg_type=MessageType.STATUS_UPDATE, content="Status 1",
    )
    await display._on_message(msg1)
    assert len(sent) == 1  # First one goes through

    # Rapid subsequent STATUS_UPDATEs should be debounced
    msg2 = MeshMessage(
        sender="coder-1", recipient="display",
        msg_type=MessageType.STATUS_UPDATE, content="Status 2",
    )
    msg3 = MeshMessage(
        sender="coder-1", recipient="display",
        msg_type=MessageType.STATUS_UPDATE, content="Status 3 (latest)",
    )
    await display._on_message(msg2)
    await display._on_message(msg3)

    # Only 1 sent so far (the first one)
    assert len(sent) == 1

    # Wait for debounce window to expire
    await asyncio.sleep(0.5)

    # Now the latest pending should have been flushed
    assert len(sent) == 2
    assert "Status 3" in sent[1]["text"].replace("\\", "")

    await display.stop()
    await bus.shutdown()


async def test_different_agents_debounced_independently():
    """Debounce should be per-agent, not global."""
    bus = AsyncioMessageBus()
    sent = []

    async def mock_send(chat_id, text, parse_mode):
        sent.append(text)

    display = TelegramDisplay(bus=bus, chat_id=123, send_fn=mock_send, debounce_seconds=5.0)
    await display.start()

    # Two different agents send STATUS_UPDATE at the same time
    msg1 = MeshMessage(
        sender="coder-1", recipient="display",
        msg_type=MessageType.STATUS_UPDATE, content="Coder working",
    )
    msg2 = MeshMessage(
        sender="researcher-1", recipient="display",
        msg_type=MessageType.STATUS_UPDATE, content="Researcher working",
    )
    await display._on_message(msg1)
    await display._on_message(msg2)

    # Both should go through (first message from each agent)
    assert len(sent) == 2

    await display.stop()
    await bus.shutdown()
