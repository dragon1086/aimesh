"""Tests for TmuxPMOrchestrator."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aimesh.core.message import MeshMessage, MessageType
from aimesh.core.registry import AgentRegistry
from aimesh.core.task import TaskState
from aimesh.tasks.tracker import TaskTracker
from aimesh.tmux.orchestrator import TmuxPMOrchestrator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bus():
    mock = AsyncMock()
    mock.subscribe = AsyncMock()
    mock.unsubscribe = AsyncMock()
    mock.publish = AsyncMock()
    return mock


@pytest.fixture
def registry():
    reg = AgentRegistry()
    reg.register("worker-1", "coder", ["code", "test"])
    return reg


@pytest.fixture
def tracker():
    return TaskTracker()


@pytest.fixture
def orchestrator(bus, registry, tracker):
    return TmuxPMOrchestrator(
        bus=bus,
        registry=registry,
        tracker=tracker,
        pm_id="test",
        session_name="aimesh-pm-test",
        workspace="/tmp/workspace",
        data_dir="/tmp/data/pm",
    )


# ---------------------------------------------------------------------------
# Test 1: subscribes to bus as "pm"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_subscribes_as_pm(orchestrator, bus):
    with (
        patch(
            "aimesh.tmux.orchestrator.TmuxSession.spawn", new_callable=AsyncMock, return_value=True
        ),
        patch.object(
            orchestrator._bridge, "start_outbox_polling", new_callable=AsyncMock
        ),
    ):
        await orchestrator.start()
        bus.subscribe.assert_called_once_with("pm", orchestrator._on_message)
        await orchestrator._bridge.stop_outbox_polling()
        orchestrator._reminder_task.cancel()
        try:
            await orchestrator._reminder_task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# Test 2: CHAT message causes bridge.send_to_pm to be called with formatted prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_message_forwarded_to_bridge(orchestrator):
    chat_msg = MeshMessage(
        sender="human",
        recipient="pm",
        msg_type=MessageType.CHAT,
        content="What is the status?",
    )

    outbox_response = {
        "id": "resp-1",
        "reply_to": "INJECTED",  # will be patched
        "type": "chat_response",
        "content": "",
        "structured_data": {"action": "status"},
        "timestamp": "2024-01-01T00:00:00Z",
    }

    sent_msg_id: list[str] = []

    async def fake_send_to_pm(pm_id, session_name, prompt_text):
        import uuid
        mid = str(uuid.uuid4())
        sent_msg_id.append(mid)
        # Simulate outbox response arriving
        outbox_response["reply_to"] = mid
        asyncio.get_running_loop().call_soon(
            lambda: asyncio.ensure_future(
                orchestrator._outbox_callback("test", dict(outbox_response))
            )
        )
        return mid

    orchestrator._bridge.send_to_pm = AsyncMock(side_effect=fake_send_to_pm)

    await orchestrator._handle_chat(chat_msg)

    orchestrator._bridge.send_to_pm.assert_called_once()
    call_kwargs = orchestrator._bridge.send_to_pm.call_args
    assert call_kwargs.kwargs.get("pm_id") == "test" or call_kwargs.args[0] == "test"


# ---------------------------------------------------------------------------
# Test 3: CHAT response published with correct parent_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_response_published_with_parent_id(orchestrator, bus):
    chat_msg = MeshMessage(
        sender="human",
        recipient="pm",
        msg_type=MessageType.CHAT,
        content="show status",
    )

    async def fake_send_to_pm(pm_id, session_name, prompt_text):
        import uuid
        mid = str(uuid.uuid4())
        response = {
            "id": "r1",
            "reply_to": mid,
            "type": "chat_response",
            "content": "",
            "structured_data": {"action": "status"},
            "timestamp": "2024-01-01T00:00:00Z",
        }
        asyncio.get_running_loop().call_soon(
            lambda: asyncio.ensure_future(
                orchestrator._outbox_callback("test", response)
            )
        )
        return mid

    orchestrator._bridge.send_to_pm = AsyncMock(side_effect=fake_send_to_pm)

    await orchestrator._handle_chat(chat_msg)

    published = bus.publish.call_args_list
    assert published, "Expected bus.publish to be called"
    last_msg: MeshMessage = published[-1].args[0]
    assert last_msg.parent_id == chat_msg.id
    assert last_msg.msg_type == MessageType.CHAT


# ---------------------------------------------------------------------------
# Test 4: TASK_ASSIGN creates a task in the tracker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_assign_creates_task(orchestrator):
    task_msg = MeshMessage(
        sender="human",
        recipient="pm",
        msg_type=MessageType.TASK_ASSIGN,
        content="Build a REST API",
    )

    async def fake_send_to_pm(pm_id, session_name, prompt_text):
        import uuid
        mid = str(uuid.uuid4())
        response = {
            "id": "r2",
            "reply_to": mid,
            "type": "task_decomposition",
            "content": "",
            "structured_data": {
                "subtasks": [
                    {
                        "title": "Implement API",
                        "description": "Write REST endpoints",
                        "agent_type": "coder",
                        "priority": 1,
                    }
                ]
            },
            "timestamp": "2024-01-01T00:00:00Z",
        }
        asyncio.get_running_loop().call_soon(
            lambda: asyncio.ensure_future(
                orchestrator._outbox_callback("test", response)
            )
        )
        return mid

    orchestrator._bridge.send_to_pm = AsyncMock(side_effect=fake_send_to_pm)

    await orchestrator._handle_new_task(task_msg)

    active = orchestrator.tracker.get_active_tasks()
    # Parent task + subtask both tracked
    assert len(active) >= 1
    titles = [t.title for t in orchestrator.tracker.get_all_tasks() if t.title]
    assert any("REST API" in t for t in titles) or any("Implement API" in t for t in titles)


# ---------------------------------------------------------------------------
# Test 5: start() spawns the tmux session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_spawns_tmux_session(orchestrator):
    with (
        patch(
            "aimesh.tmux.orchestrator.TmuxSession.spawn",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_spawn,
        patch.object(
            orchestrator._bridge, "start_outbox_polling", new_callable=AsyncMock
        ),
    ):
        await orchestrator.start()
        mock_spawn.assert_called_once_with(
            "aimesh-pm-test",
            orchestrator.engine_command,
            cwd="/tmp/workspace",
        )
        orchestrator._reminder_task.cancel()
        try:
            await orchestrator._reminder_task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# Test 6: stop() kills the tmux session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_kills_tmux_session(orchestrator, bus):
    with (
        patch(
            "aimesh.tmux.orchestrator.TmuxSession.kill",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_kill,
        patch.object(
            orchestrator._bridge, "stop_outbox_polling", new_callable=AsyncMock
        ),
    ):
        # Create a dummy reminder task so stop() has something to cancel
        orchestrator._reminder_task = asyncio.create_task(asyncio.sleep(9999))
        await orchestrator.stop()
        mock_kill.assert_called_once_with("aimesh-pm-test")
        bus.unsubscribe.assert_called_once_with("pm")


# ---------------------------------------------------------------------------
# Test 7: RESULT message transitions task state to REVIEW
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_result_transitions_state(orchestrator, bus):
    from aimesh.core.task import Task, TaskState

    # Create a task already in IN_PROGRESS
    task = Task(title="Do work", description="Some work")
    task.transition(TaskState.DECOMPOSING)
    task.transition(TaskState.ASSIGNED)
    task.transition(TaskState.IN_PROGRESS)
    orchestrator.tracker.add(task)

    result_msg = MeshMessage(
        sender="worker-1",
        recipient="pm",
        msg_type=MessageType.RESULT,
        content="Work done",
        task_id=task.id,
    )

    await orchestrator._handle_result(result_msg)

    updated = orchestrator.tracker.get(task.id)
    assert updated is not None
    assert updated.state == TaskState.REVIEW

    # A REVIEW_REQUEST should have been broadcast
    published_types = [c.args[0].msg_type for c in bus.publish.call_args_list]
    assert MessageType.REVIEW_REQUEST in published_types
