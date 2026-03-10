"""Tests for ResearcherAgent with mocked executor."""

import asyncio
from unittest.mock import AsyncMock

from aimesh.agents.researcher import ResearcherAgent
from aimesh.agents.executor import ExecutionResult
from aimesh.core.bus import AsyncioMessageBus
from aimesh.core.message import MeshMessage, MessageType
from aimesh.core.task import Task, TaskState
from aimesh.tasks.tracker import TaskTracker


async def test_researcher_accepts_and_works():
    bus = AsyncioMessageBus()
    tracker = TaskTracker()

    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock(return_value=ExecutionResult(
        content="Analysis: FastAPI is the best choice for this project because...",
        cost_usd=0.02,
    ))

    researcher = ResearcherAgent("researcher-1", bus=bus, executor=mock_executor, tracker=tracker)

    pm_msgs = []

    async def pm_handler(msg):
        pm_msgs.append(msg)

    await bus.subscribe("pm", pm_handler)
    await researcher.start()

    task = Task(title="Research frameworks", description="Compare FastAPI vs Django",
                assigned_to="researcher-1")
    task.transition(TaskState.DECOMPOSING)
    task.transition(TaskState.ASSIGNED)
    tracker.add(task)

    msg = MeshMessage(
        sender="pm", recipient="researcher-1",
        msg_type=MessageType.TASK_ASSIGN,
        content="Compare FastAPI vs Django for building REST APIs",
        task_id=task.id,
        metadata={"title": "Research frameworks"},
    )
    await researcher.handle_message(msg)
    await asyncio.sleep(0.1)

    assert task.state == TaskState.IN_PROGRESS

    # Should have sent TASK_ACCEPT and RESULT
    accept_msgs = [m for m in pm_msgs if m.msg_type == MessageType.TASK_ACCEPT]
    result_msgs = [m for m in pm_msgs if m.msg_type == MessageType.RESULT]
    assert len(accept_msgs) == 1
    assert len(result_msgs) == 1
    assert "FastAPI" in result_msgs[0].content

    await bus.shutdown()


async def test_researcher_sends_status_updates():
    bus = AsyncioMessageBus()
    tracker = TaskTracker()

    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock(return_value=ExecutionResult(
        content="Research complete.", cost_usd=0.01,
    ))

    researcher = ResearcherAgent("researcher-1", bus=bus, executor=mock_executor, tracker=tracker)

    all_msgs = []

    async def capture_all(msg):
        all_msgs.append(msg)

    # Subscribe a broadcast listener
    await bus.subscribe("listener", capture_all)
    await bus.subscribe("pm", AsyncMock())
    await researcher.start()

    task = Task(title="Test", description="Test", assigned_to="researcher-1")
    task.transition(TaskState.DECOMPOSING)
    task.transition(TaskState.ASSIGNED)
    tracker.add(task)

    msg = MeshMessage(
        sender="pm", recipient="researcher-1",
        msg_type=MessageType.TASK_ASSIGN,
        content="Analyze this",
        task_id=task.id,
        metadata={"title": "Test"},
    )
    await researcher.handle_message(msg)
    await asyncio.sleep(0.1)

    # Should have broadcast STATUS_UPDATE
    status_msgs = [m for m in all_msgs if m.msg_type == MessageType.STATUS_UPDATE]
    assert len(status_msgs) >= 1

    await bus.shutdown()


async def test_researcher_handles_failure():
    bus = AsyncioMessageBus()
    tracker = TaskTracker()

    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock(return_value=ExecutionResult(
        content="", success=False, error="API rate limit exceeded",
    ))

    researcher = ResearcherAgent("researcher-1", bus=bus, executor=mock_executor, tracker=tracker)
    await bus.subscribe("pm", AsyncMock())
    await researcher.start()

    task = Task(title="Test", description="Test", assigned_to="researcher-1")
    task.transition(TaskState.DECOMPOSING)
    task.transition(TaskState.ASSIGNED)
    tracker.add(task)

    msg = MeshMessage(
        sender="pm", recipient="researcher-1",
        msg_type=MessageType.TASK_ASSIGN,
        content="Research something",
        task_id=task.id,
        metadata={"title": "Test"},
    )
    await researcher.handle_message(msg)

    assert task.retry_count == 1

    await bus.shutdown()
