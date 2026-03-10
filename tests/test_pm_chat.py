"""Tests for PM CHAT message handling (Phase 2b)."""

import json
from unittest.mock import AsyncMock

from aimesh.agents.executor import ExecutionResult
from aimesh.agents.pm import PMAgent
from aimesh.core.bus import AsyncioMessageBus
from aimesh.core.message import MeshMessage, MessageType
from aimesh.core.registry import AgentRegistry
from aimesh.core.task import Task, TaskState
from aimesh.tasks.tracker import TaskTracker


async def test_pm_handles_chat_message_type():
    """PM dispatches MessageType.CHAT to _handle_chat."""
    bus = AsyncioMessageBus()
    registry = AgentRegistry()
    tracker = TaskTracker()

    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock(return_value=ExecutionResult(
        content=json.dumps({"action": "status"}),
        success=True,
    ))

    pm = PMAgent(
        bus=bus, executor=mock_executor, registry=registry, tracker=tracker,
    )

    response_msgs = []

    async def capture(msg: MeshMessage):
        response_msgs.append(msg)

    await bus.subscribe("human", capture)

    chat_msg = MeshMessage(
        sender="human", recipient="pm",
        msg_type=MessageType.CHAT,
        content="뭐하고 있어?",
        metadata={"conversation_context": ""},
    )
    await pm.handle_message(chat_msg)

    import asyncio
    await asyncio.sleep(0.1)

    assert len(response_msgs) >= 1
    assert response_msgs[0].msg_type == MessageType.CHAT
    assert response_msgs[0].parent_id == chat_msg.id


async def test_pm_classify_task_creation():
    """PM classifies Korean task creation and creates a task."""
    bus = AsyncioMessageBus()
    registry = AgentRegistry()
    tracker = TaskTracker()

    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock(return_value=ExecutionResult(
        content=json.dumps({
            "action": "create_task",
            "description": "Build a login page",
        }),
        success=True,
    ))

    pm = PMAgent(
        bus=bus, executor=mock_executor, registry=registry, tracker=tracker,
    )

    response_msgs = []

    async def capture(msg: MeshMessage):
        response_msgs.append(msg)

    await bus.subscribe("human", capture)

    chat_msg = MeshMessage(
        sender="human", recipient="pm",
        msg_type=MessageType.CHAT,
        content="로그인 페이지 만들어줘",
        metadata={"conversation_context": ""},
    )
    await pm.handle_message(chat_msg)

    import asyncio
    await asyncio.sleep(0.1)

    # PM should have created a task
    tasks = tracker.get_all_tasks()
    assert len(tasks) >= 1

    # Response should mention task creation
    chat_responses = [m for m in response_msgs if m.msg_type == MessageType.CHAT]
    assert len(chat_responses) >= 1
    assert "Task created" in chat_responses[0].content or "login" in chat_responses[0].content.lower()


async def test_pm_classify_approve():
    """PM classifies approval and approves a task."""
    bus = AsyncioMessageBus()
    registry = AgentRegistry()
    tracker = TaskTracker()

    # Create a task in REVIEW state
    task = Task(title="Test Task", description="Test")
    task.transition(TaskState.DECOMPOSING)
    task.transition(TaskState.ASSIGNED)
    task.transition(TaskState.IN_PROGRESS)
    task.transition(TaskState.REVIEW)
    tracker.add(task)

    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock(return_value=ExecutionResult(
        content=json.dumps({
            "action": "approve",
            "task_id": task.id,
        }),
        success=True,
    ))

    pm = PMAgent(
        bus=bus, executor=mock_executor, registry=registry, tracker=tracker,
    )

    response_msgs = []

    async def capture(msg: MeshMessage):
        response_msgs.append(msg)

    await bus.subscribe("human", capture)
    await bus.subscribe("broadcast", lambda m: None)

    chat_msg = MeshMessage(
        sender="human", recipient="pm",
        msg_type=MessageType.CHAT,
        content="좋아 승인해",
        metadata={"conversation_context": f"[{task.title}] is awaiting review"},
    )
    await pm.handle_message(chat_msg)

    import asyncio
    await asyncio.sleep(0.1)

    assert task.state == TaskState.DONE


async def test_pm_classify_status_query():
    """PM classifies status query and returns task summary."""
    bus = AsyncioMessageBus()
    registry = AgentRegistry()
    tracker = TaskTracker()

    # Add an active task
    task = Task(title="Build API", description="API work")
    task.transition(TaskState.DECOMPOSING)
    task.transition(TaskState.ASSIGNED)
    task.transition(TaskState.IN_PROGRESS)
    tracker.add(task)

    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock(return_value=ExecutionResult(
        content=json.dumps({"action": "status"}),
        success=True,
    ))

    pm = PMAgent(
        bus=bus, executor=mock_executor, registry=registry, tracker=tracker,
    )

    response_msgs = []

    async def capture(msg: MeshMessage):
        response_msgs.append(msg)

    await bus.subscribe("human", capture)

    chat_msg = MeshMessage(
        sender="human", recipient="pm",
        msg_type=MessageType.CHAT,
        content="지금 뭐하고 있어?",
        metadata={"conversation_context": ""},
    )
    await pm.handle_message(chat_msg)

    import asyncio
    await asyncio.sleep(0.1)

    chat_responses = [m for m in response_msgs if m.msg_type == MessageType.CHAT]
    assert len(chat_responses) >= 1
    assert "Build API" in chat_responses[0].content or "active" in chat_responses[0].content.lower()


async def test_pm_chat_with_malformed_json():
    """PM handles malformed LLM response gracefully."""
    bus = AsyncioMessageBus()
    registry = AgentRegistry()
    tracker = TaskTracker()

    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock(return_value=ExecutionResult(
        content="This is not JSON at all",
        success=True,
    ))

    pm = PMAgent(
        bus=bus, executor=mock_executor, registry=registry, tracker=tracker,
    )

    response_msgs = []

    async def capture(msg: MeshMessage):
        response_msgs.append(msg)

    await bus.subscribe("human", capture)

    chat_msg = MeshMessage(
        sender="human", recipient="pm",
        msg_type=MessageType.CHAT,
        content="안녕하세요",
        metadata={"conversation_context": ""},
    )
    await pm.handle_message(chat_msg)

    import asyncio
    await asyncio.sleep(0.1)

    # Should still respond (fallback)
    chat_responses = [m for m in response_msgs if m.msg_type == MessageType.CHAT]
    assert len(chat_responses) >= 1


async def test_pm_get_active_tasks_summary():
    """_get_active_tasks_summary returns formatted task list."""
    bus = AsyncioMessageBus()
    registry = AgentRegistry()
    tracker = TaskTracker()

    pm = PMAgent(
        bus=bus, executor=AsyncMock(), registry=registry, tracker=tracker,
    )

    # No tasks
    assert "(no active tasks)" in pm._get_active_tasks_summary()

    # Add a task
    task = Task(title="Build API", description="API work")
    task.transition(TaskState.DECOMPOSING)
    task.transition(TaskState.ASSIGNED)
    task.transition(TaskState.IN_PROGRESS)
    tracker.add(task)

    summary = pm._get_active_tasks_summary()
    assert "Build API" in summary
    assert "in_progress" in summary
