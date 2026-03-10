"""Tests for CoderAgent with mocked executor."""

import asyncio
from unittest.mock import AsyncMock

from aimesh.agents.coder import CoderAgent
from aimesh.agents.executor import ExecutionResult
from aimesh.core.bus import AsyncioMessageBus
from aimesh.core.message import MeshMessage, MessageType
from aimesh.core.task import Task, TaskState
from aimesh.tasks.tracker import TaskTracker


async def test_coder_accepts_and_works():
    bus = AsyncioMessageBus()
    tracker = TaskTracker()

    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock(return_value=ExecutionResult(
        content="Implemented user model in models.py",
        cost_usd=0.05,
    ))

    coder = CoderAgent("coder-1", bus=bus, executor=mock_executor, tracker=tracker)

    pm_msgs = []

    async def pm_handler(msg):
        pm_msgs.append(msg)

    await bus.subscribe("pm", pm_handler)
    await coder.start()

    # Create a task in ASSIGNED state
    task = Task(title="Create user model", description="Define User class", assigned_to="coder-1")
    task.transition(TaskState.DECOMPOSING)
    task.transition(TaskState.ASSIGNED)
    tracker.add(task)

    # Send task to coder
    msg = MeshMessage(
        sender="pm", recipient="coder-1",
        msg_type=MessageType.TASK_ASSIGN,
        content="Define User class in models.py",
        task_id=task.id,
        metadata={"title": "Create user model", "parent_task_id": "parent-1"},
    )
    await coder.handle_message(msg)
    await asyncio.sleep(0.1)

    # Task should move to IN_PROGRESS
    assert task.state == TaskState.IN_PROGRESS

    # Should have sent TASK_ACCEPT, STATUS_UPDATE, and RESULT
    accept_msgs = [m for m in pm_msgs if m.msg_type == MessageType.TASK_ACCEPT]
    result_msgs = [m for m in pm_msgs if m.msg_type == MessageType.RESULT]
    assert len(accept_msgs) == 1
    assert len(result_msgs) == 1

    # RESULT should contain branch name in metadata
    assert result_msgs[0].metadata["branch"] == f"task/{task.id}"
    assert result_msgs[0].metadata["cost_usd"] == 0.05

    await bus.shutdown()


async def test_coder_handles_failure():
    bus = AsyncioMessageBus()
    tracker = TaskTracker()

    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock(return_value=ExecutionResult(
        content="", success=False, error="Timeout after 300s",
    ))

    coder = CoderAgent("coder-1", bus=bus, executor=mock_executor, tracker=tracker)

    pm_msgs = []

    async def pm_handler(msg):
        pm_msgs.append(msg)

    await bus.subscribe("pm", pm_handler)
    await coder.start()

    task = Task(title="Test", description="Test", assigned_to="coder-1")
    task.transition(TaskState.DECOMPOSING)
    task.transition(TaskState.ASSIGNED)
    tracker.add(task)

    msg = MeshMessage(
        sender="pm", recipient="coder-1",
        msg_type=MessageType.TASK_ASSIGN,
        content="Do something",
        task_id=task.id,
        metadata={"title": "Test"},
    )
    await coder.handle_message(msg)
    await asyncio.sleep(0.1)

    # Should report failure
    status_msgs = [m for m in pm_msgs if m.msg_type == MessageType.STATUS_UPDATE]
    assert any("Failed" in m.content for m in status_msgs)
    assert task.retry_count == 1

    await bus.shutdown()


async def test_coder_fails_after_max_retries():
    bus = AsyncioMessageBus()
    tracker = TaskTracker()

    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock(return_value=ExecutionResult(
        content="", success=False, error="Persistent failure",
    ))

    coder = CoderAgent("coder-1", bus=bus, executor=mock_executor, tracker=tracker)
    await bus.subscribe("pm", AsyncMock())
    await coder.start()

    task = Task(title="Test", description="Test", assigned_to="coder-1")
    task.transition(TaskState.DECOMPOSING)
    task.transition(TaskState.ASSIGNED)
    task.retry_count = 2  # Already retried twice
    tracker.add(task)

    msg = MeshMessage(
        sender="pm", recipient="coder-1",
        msg_type=MessageType.TASK_ASSIGN,
        content="Do something",
        task_id=task.id,
        metadata={"title": "Test"},
    )
    await coder.handle_message(msg)

    assert task.state == TaskState.FAILED
    assert task.retry_count == 3

    await bus.shutdown()
