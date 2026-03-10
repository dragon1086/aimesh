"""Tests for PM agent with mocked executor."""

import asyncio
import json
from unittest.mock import AsyncMock

from aimesh.agents.executor import ExecutionResult
from aimesh.agents.pm import PMAgent
from aimesh.core.bus import AsyncioMessageBus
from aimesh.core.message import MeshMessage, MessageType
from aimesh.core.registry import AgentRegistry
from aimesh.tasks.tracker import TaskTracker


async def test_pm_decomposes_task():
    """PM should decompose a high-level task into subtasks."""
    bus = AsyncioMessageBus()
    registry = AgentRegistry()
    tracker = TaskTracker()

    # Register a coder worker
    registry.register("coder-1", "coder", capabilities=["code"])

    # Mock executor returns structured subtasks
    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock(return_value=ExecutionResult(
        content=json.dumps({
            "subtasks": [
                {
                    "title": "Create user model",
                    "description": "Define User dataclass in models.py",
                    "agent_type": "coder",
                    "priority": 1,
                },
                {
                    "title": "Create API endpoint",
                    "description": "Add /users endpoint in routes.py",
                    "agent_type": "coder",
                    "priority": 2,
                },
            ]
        }),
        success=True,
    ))

    pm = PMAgent(bus=bus, executor=mock_executor, registry=registry, tracker=tracker)

    # Capture messages sent by PM
    sent_messages = []

    async def capture(msg: MeshMessage):
        sent_messages.append(msg)

    await bus.subscribe("coder-1", capture)
    await pm.start()

    # Send a task to PM
    task_msg = MeshMessage(
        sender="human",
        recipient="pm",
        msg_type=MessageType.TASK_ASSIGN,
        content="Build a user management system",
    )
    await pm.handle_message(task_msg)
    await asyncio.sleep(0.1)

    # Verify decomposition happened
    assert mock_executor.execute.called

    # Verify subtasks were created
    all_tasks = tracker.get_all_tasks()
    assert len(all_tasks) >= 3  # 1 parent + 2 subtasks

    # Verify messages sent to coder
    assign_msgs = [m for m in sent_messages if m.msg_type == MessageType.TASK_ASSIGN]
    assert len(assign_msgs) == 2

    await bus.shutdown()


async def test_pm_assigns_to_correct_agent_type():
    """PM should assign subtasks to workers matching the required type."""
    bus = AsyncioMessageBus()
    registry = AgentRegistry()
    tracker = TaskTracker()

    registry.register("coder-1", "coder", capabilities=["code"])
    registry.register("researcher-1", "researcher", capabilities=["research"])

    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock(return_value=ExecutionResult(
        content=json.dumps({
            "subtasks": [
                {"title": "Write code", "description": "Implement feature",
                 "agent_type": "coder", "priority": 1},
                {"title": "Research libs", "description": "Find best library",
                 "agent_type": "researcher", "priority": 2},
            ]
        }),
        success=True,
    ))

    pm = PMAgent(bus=bus, executor=mock_executor, registry=registry, tracker=tracker)

    coder_msgs = []
    researcher_msgs = []

    async def coder_handler(msg):
        coder_msgs.append(msg)

    async def researcher_handler(msg):
        researcher_msgs.append(msg)

    await bus.subscribe("coder-1", coder_handler)
    await bus.subscribe("researcher-1", researcher_handler)
    await pm.start()

    task_msg = MeshMessage(
        sender="human", recipient="pm",
        msg_type=MessageType.TASK_ASSIGN, content="Build and research",
    )
    await pm.handle_message(task_msg)
    await asyncio.sleep(0.1)

    # Coder gets coding task, researcher gets research task
    coder_assigns = [m for m in coder_msgs if m.msg_type == MessageType.TASK_ASSIGN]
    researcher_assigns = [m for m in researcher_msgs if m.msg_type == MessageType.TASK_ASSIGN]
    assert len(coder_assigns) == 1
    assert len(researcher_assigns) == 1

    await bus.shutdown()


async def test_pm_approve_task():
    """PM should move task to DONE on approval."""
    bus = AsyncioMessageBus()
    registry = AgentRegistry()
    tracker = TaskTracker()

    pm = PMAgent(bus=bus, executor=AsyncMock(), registry=registry, tracker=tracker)
    await pm.start()

    # Manually create a task in REVIEW state
    from aimesh.core.task import Task, TaskState
    task = Task(title="Test", description="Test")
    task.transition(TaskState.DECOMPOSING)
    task.transition(TaskState.ASSIGNED)
    task.transition(TaskState.IN_PROGRESS)
    task.transition(TaskState.REVIEW)
    tracker.add(task)

    result = await pm.approve_task(task.id)
    assert result is True
    assert task.state == TaskState.DONE

    await bus.shutdown()


async def test_pm_reject_task():
    """PM should move task to REWORK on rejection."""
    bus = AsyncioMessageBus()
    registry = AgentRegistry()
    tracker = TaskTracker()

    pm = PMAgent(bus=bus, executor=AsyncMock(), registry=registry, tracker=tracker)
    await pm.start()

    from aimesh.core.task import Task, TaskState
    task = Task(title="Test", description="Test", assigned_to="coder-1")
    task.transition(TaskState.DECOMPOSING)
    task.transition(TaskState.ASSIGNED)
    task.transition(TaskState.IN_PROGRESS)
    task.transition(TaskState.REVIEW)
    tracker.add(task)

    # Need coder-1 to receive rework message
    rework_msgs = []

    async def handler(msg):
        rework_msgs.append(msg)

    await bus.subscribe("coder-1", handler)

    result = await pm.reject_task(task.id, "Needs error handling")
    await asyncio.sleep(0.05)

    assert result is True
    assert task.state == TaskState.REWORK
    assert len(rework_msgs) == 1
    assert "REWORK" in rework_msgs[0].content

    await bus.shutdown()
