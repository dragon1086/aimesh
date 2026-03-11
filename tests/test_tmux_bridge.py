"""Tests for HybridBridge IPC module."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aimesh.tmux.bridge import HybridBridge
from aimesh.tmux.protocol import write_response
from aimesh.tmux.session import TmuxSession


def make_bridge(tmp_path: Path) -> tuple[HybridBridge, AsyncMock]:
    """Return a (bridge, mock_session) pair backed by tmp_path."""
    mock_session = MagicMock(spec=TmuxSession)
    mock_session.send_keys = AsyncMock(return_value=None)
    bridge = HybridBridge(session=mock_session, data_dir=tmp_path)
    return bridge, mock_session


# ---------------------------------------------------------------------------
# 1. send_to_pm calls send_keys
# ---------------------------------------------------------------------------

async def test_send_to_pm_calls_send_keys(tmp_path):
    bridge, mock_session = make_bridge(tmp_path)
    prompt = "Hello PM, your MSG_ID is {MSG_ID}"
    await bridge.send_to_pm("pm-1", "my-session", prompt)
    mock_session.send_keys.assert_called_once()
    call_args = mock_session.send_keys.call_args
    assert call_args[0][0] == "my-session"
    # The injected text must no longer contain the literal placeholder
    injected_text = call_args[0][1]
    assert "{MSG_ID}" not in injected_text
    assert "Hello PM" in injected_text


# ---------------------------------------------------------------------------
# 2. send_to_pm returns a valid UUID
# ---------------------------------------------------------------------------

async def test_send_to_pm_returns_msg_id(tmp_path):
    bridge, _ = make_bridge(tmp_path)
    msg_id = await bridge.send_to_pm("pm-1", "sess", "prompt {MSG_ID}")
    # Must be a valid UUID4
    parsed = uuid.UUID(msg_id, version=4)
    assert str(parsed) == msg_id


# ---------------------------------------------------------------------------
# 3. format_prompt includes all sections when all args supplied
# ---------------------------------------------------------------------------

def test_format_prompt_includes_all_sections():
    msg_id = "test-id-123"
    result = HybridBridge.format_prompt(
        msg_id=msg_id,
        msg_type="orchestrator_prompt",
        user_message="Do the thing",
        active_tasks="task-1: in_progress",
        available_workers="worker-a, worker-b",
        conversation_context="Previous turn: hello",
        outbox_path="/data/pm1/outbox",
    )
    assert f"[AIMESH-MSG id={msg_id} type=orchestrator_prompt]" in result
    assert "ACTIVE TASKS:" in result
    assert "task-1: in_progress" in result
    assert "AVAILABLE WORKERS:" in result
    assert "worker-a, worker-b" in result
    assert "CONVERSATION CONTEXT:" in result
    assert "Previous turn: hello" in result
    assert "NEW MESSAGE:" in result
    assert "Do the thing" in result
    assert "Respond by writing a JSON file to /data/pm1/outbox" in result
    assert f'"reply_to": "{msg_id}"' in result


# ---------------------------------------------------------------------------
# 4. format_prompt minimal — no empty sections
# ---------------------------------------------------------------------------

def test_format_prompt_minimal():
    result = HybridBridge.format_prompt(
        msg_id="mid",
        msg_type="chat",
        user_message="Hello",
    )
    assert "[AIMESH-MSG id=mid type=chat]" in result
    assert "Hello" in result
    assert "ACTIVE TASKS:" not in result
    assert "AVAILABLE WORKERS:" not in result
    assert "CONVERSATION CONTEXT:" not in result
    assert "Respond by writing" not in result


# ---------------------------------------------------------------------------
# 5. Outbox polling detects files and fires callback
# ---------------------------------------------------------------------------

async def test_outbox_polling_detects_files(tmp_path):
    bridge, _ = make_bridge(tmp_path)
    bridge._poll_interval = 0.05  # fast polling for tests

    outbox = bridge.outbox_dir("pm-1")
    outbox.mkdir(parents=True, exist_ok=True)

    received: list[tuple[str, dict]] = []

    async def callback(pm_id: str, data: dict) -> None:
        received.append((pm_id, data))

    await bridge.start_outbox_polling(["pm-1"], callback)

    # Write a response file after polling has started
    payload = {
        "id": "resp-001",
        "reply_to": "msg-abc",
        "type": "chat_response",
        "content": "done",
        "structured_data": {},
        "timestamp": "2025-01-01T00:00:00Z",
    }
    write_response(outbox, payload)

    # Wait up to 2 s for the callback to fire
    async def _wait():
        while not received:
            await asyncio.sleep(0.02)

    await asyncio.wait_for(_wait(), timeout=2.0)
    await bridge.stop_outbox_polling()

    assert len(received) == 1
    pm_id, data = received[0]
    assert pm_id == "pm-1"
    assert data["id"] == "resp-001"
    assert data["content"] == "done"


# ---------------------------------------------------------------------------
# 6. stop_outbox_polling cancels the task
# ---------------------------------------------------------------------------

async def test_stop_polling_cancels_task(tmp_path):
    bridge, _ = make_bridge(tmp_path)
    bridge._poll_interval = 10.0  # long interval so task is sleeping

    async def noop(pm_id: str, data: dict) -> None:
        pass

    await bridge.start_outbox_polling(["pm-1"], noop)
    task = bridge._polling_task
    assert task is not None
    assert not task.done()

    await bridge.stop_outbox_polling()

    assert bridge._polling_task is None
    assert task.done()
    assert task.cancelled()


# ---------------------------------------------------------------------------
# 7. Malformed JSON in outbox is skipped gracefully (no callback, no crash)
# ---------------------------------------------------------------------------

async def test_poll_handles_malformed_json(tmp_path):
    bridge, _ = make_bridge(tmp_path)
    bridge._poll_interval = 0.05

    outbox = bridge.outbox_dir("pm-bad")
    outbox.mkdir(parents=True, exist_ok=True)

    # Write a malformed JSON file directly (bypassing write_response)
    bad_file = outbox / "bad-response.json"
    bad_file.write_text("{not valid json", encoding="utf-8")

    callback_called = False

    async def callback(pm_id: str, data: dict) -> None:
        nonlocal callback_called
        callback_called = True

    await bridge.start_outbox_polling(["pm-bad"], callback)

    # Give the poller two cycles to process the bad file
    await asyncio.sleep(0.15)

    await bridge.stop_outbox_polling()

    # The malformed file should have been moved to processed/ but not trigger callback
    assert not callback_called
    processed = outbox / "processed" / "bad-response.json"
    assert processed.exists()
