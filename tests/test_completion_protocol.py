"""Tests for the completion confirmation protocol (Step 5.5)."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from aimesh.agents.pm import PMAgent
from aimesh.core.bus import AsyncioMessageBus
from aimesh.core.message import MeshMessage, MessageType
from aimesh.core.registry import AgentRegistry
from aimesh.core.task import Task, TaskState
from aimesh.tasks.tracker import TaskTracker


def _make_parent_with_subtasks(
    tracker: TaskTracker,
    num_subtasks: int = 3,
    agent_ids: list[str] | None = None,
) -> tuple[Task, list[Task]]:
    """Create a parent task with subtasks in IN_PROGRESS state."""
    if agent_ids is None:
        agent_ids = [f"worker-{i}" for i in range(num_subtasks)]

    parent = Task(title="Parent Task", description="Parent")
    parent.transition(TaskState.DECOMPOSING)
    parent.transition(TaskState.ASSIGNED)
    tracker.add(parent)

    subtasks = []
    for i in range(num_subtasks):
        st = Task(
            title=f"Subtask {i}",
            description=f"Subtask {i} desc",
            parent_id=parent.id,
            assigned_to=agent_ids[i],
        )
        st.transition(TaskState.DECOMPOSING)
        st.transition(TaskState.ASSIGNED)
        st.transition(TaskState.IN_PROGRESS)
        parent.subtask_ids.append(st.id)
        tracker.add(st)
        subtasks.append(st)

    return parent, subtasks


async def test_all_confirm_done_subtasks_approved():
    """When all agents confirm done, subtasks move to DONE and parent to REVIEW."""
    bus = AsyncioMessageBus()
    registry = AgentRegistry()
    tracker = TaskTracker()

    agent_ids = ["worker-0", "worker-1", "worker-2"]
    for aid in agent_ids:
        registry.register(aid, "coder", capabilities=["code"])

    pm = PMAgent(
        bus=bus, executor=AsyncMock(), registry=registry, tracker=tracker,
        completion_check_timeout=5.0,
    )

    parent, subtasks = _make_parent_with_subtasks(tracker, 3, agent_ids)

    # Set up handlers to auto-respond with COMPLETION_CONFIRM
    for aid in agent_ids:
        async def make_handler(agent_id):
            async def handler(msg: MeshMessage):
                if msg.msg_type == MessageType.COMPLETION_CHECK:
                    await bus.publish(MeshMessage(
                        sender=agent_id,
                        recipient="pm",
                        msg_type=MessageType.COMPLETION_CONFIRM,
                        content=f"Agent {agent_id} done",
                        task_id=msg.task_id,
                        metadata={"status": "confirmed_done", "agent_id": agent_id},
                    ))
            return handler

        await bus.subscribe(aid, await make_handler(aid))

    # Subscribe PM to receive COMPLETION_CONFIRM
    await bus.subscribe("pm", pm.handle_message)
    await pm.start()

    # Simulate all subtasks completing: send RESULT for each
    # First two subtasks → REVIEW but not all siblings in REVIEW yet
    for i in range(2):
        result_msg = MeshMessage(
            sender=agent_ids[i], recipient="pm",
            msg_type=MessageType.RESULT,
            content=f"Result from worker-{i}",
            task_id=subtasks[i].id,
        )
        await pm.handle_message(result_msg)

    # Verify subtasks 0,1 are in REVIEW but no completion check yet
    assert subtasks[0].state == TaskState.REVIEW
    assert subtasks[1].state == TaskState.REVIEW
    assert subtasks[2].state == TaskState.IN_PROGRESS

    # Last subtask completes → triggers completion check
    result_msg = MeshMessage(
        sender=agent_ids[2], recipient="pm",
        msg_type=MessageType.RESULT,
        content="Result from worker-2",
        task_id=subtasks[2].id,
    )
    await pm.handle_message(result_msg)

    # Allow time for COMPLETION_CHECK/CONFIRM round-trip
    await asyncio.sleep(0.3)

    # All subtasks should be DONE
    for st in subtasks:
        assert st.state == TaskState.DONE, f"{st.title} is {st.state}, expected DONE"

    # Parent should be in REVIEW (all subtasks DONE triggers tracker rule)
    assert parent.state == TaskState.REVIEW

    await pm.stop()
    await bus.shutdown()


async def test_still_working_blocks_parent_update():
    """If an agent reports still_working, PM does not approve subtasks."""
    bus = AsyncioMessageBus()
    registry = AgentRegistry()
    tracker = TaskTracker()

    agent_ids = ["worker-0", "worker-1"]
    for aid in agent_ids:
        registry.register(aid, "coder", capabilities=["code"])

    pm = PMAgent(
        bus=bus, executor=AsyncMock(), registry=registry, tracker=tracker,
        completion_check_timeout=5.0,
    )

    parent, subtasks = _make_parent_with_subtasks(tracker, 2, agent_ids)

    # worker-0 confirms done, worker-1 says still_working
    async def handler_0(msg: MeshMessage):
        if msg.msg_type == MessageType.COMPLETION_CHECK:
            await bus.publish(MeshMessage(
                sender="worker-0", recipient="pm",
                msg_type=MessageType.COMPLETION_CONFIRM,
                content="Done",
                task_id=msg.task_id,
                metadata={"status": "confirmed_done", "agent_id": "worker-0"},
            ))

    async def handler_1(msg: MeshMessage):
        if msg.msg_type == MessageType.COMPLETION_CHECK:
            await bus.publish(MeshMessage(
                sender="worker-1", recipient="pm",
                msg_type=MessageType.COMPLETION_CONFIRM,
                content="Still working",
                task_id=msg.task_id,
                metadata={"status": "still_working", "agent_id": "worker-1"},
            ))

    await bus.subscribe("worker-0", handler_0)
    await bus.subscribe("worker-1", handler_1)
    await bus.subscribe("pm", pm.handle_message)
    await pm.start()

    # Both subtasks complete (reach REVIEW)
    for i in range(2):
        result_msg = MeshMessage(
            sender=agent_ids[i], recipient="pm",
            msg_type=MessageType.RESULT,
            content=f"Result {i}",
            task_id=subtasks[i].id,
        )
        await pm.handle_message(result_msg)

    await asyncio.sleep(0.3)

    # Subtasks should remain in REVIEW (not approved to DONE)
    assert subtasks[0].state == TaskState.REVIEW
    assert subtasks[1].state == TaskState.REVIEW

    # Parent should NOT be in REVIEW
    assert parent.state == TaskState.ASSIGNED

    await pm.stop()
    await bus.shutdown()


async def test_timeout_approves_with_warning():
    """If confirmation times out, PM approves anyway with a warning."""
    bus = AsyncioMessageBus()
    registry = AgentRegistry()
    tracker = TaskTracker()

    agent_ids = ["worker-0", "worker-1"]
    for aid in agent_ids:
        registry.register(aid, "coder", capabilities=["code"])

    # Very short timeout for test
    pm = PMAgent(
        bus=bus, executor=AsyncMock(), registry=registry, tracker=tracker,
        completion_check_timeout=0.2,
    )

    parent, subtasks = _make_parent_with_subtasks(tracker, 2, agent_ids)

    # worker-0 responds, worker-1 does NOT respond (simulating timeout)
    async def handler_0(msg: MeshMessage):
        if msg.msg_type == MessageType.COMPLETION_CHECK:
            await bus.publish(MeshMessage(
                sender="worker-0", recipient="pm",
                msg_type=MessageType.COMPLETION_CONFIRM,
                content="Done",
                task_id=msg.task_id,
                metadata={"status": "confirmed_done", "agent_id": "worker-0"},
            ))

    # worker-1 handler that never sends COMPLETION_CONFIRM
    async def handler_1(msg: MeshMessage):
        pass  # Intentionally ignores COMPLETION_CHECK

    broadcast_msgs: list[MeshMessage] = []

    async def capture_broadcast(msg: MeshMessage):
        broadcast_msgs.append(msg)

    await bus.subscribe("worker-0", handler_0)
    await bus.subscribe("worker-1", handler_1)
    await bus.subscribe("pm", pm.handle_message)
    await bus.subscribe("broadcast", capture_broadcast)
    await pm.start()

    # Both subtasks reach REVIEW
    for i in range(2):
        result_msg = MeshMessage(
            sender=agent_ids[i], recipient="pm",
            msg_type=MessageType.RESULT,
            content=f"Result {i}",
            task_id=subtasks[i].id,
        )
        await pm.handle_message(result_msg)

    # Wait for timeout
    await asyncio.sleep(0.5)

    # Subtasks should be DONE (approved despite timeout)
    for st in subtasks:
        assert st.state == TaskState.DONE, f"{st.title} is {st.state}, expected DONE"

    # Parent should be in REVIEW
    assert parent.state == TaskState.REVIEW

    # Warning message should mention worker-1
    warning_msgs = [
        m for m in broadcast_msgs
        if m.msg_type == MessageType.SYSTEM and "worker-1" in m.content
    ]
    assert len(warning_msgs) >= 1, "Expected warning about missing worker-1"

    await pm.stop()
    await bus.shutdown()


async def test_completion_confirm_from_dynamic_worker():
    """DynamicWorker responds to COMPLETION_CHECK with COMPLETION_CONFIRM within 5s."""
    from aimesh.agents.dynamic_worker import DynamicWorker

    bus = AsyncioMessageBus()
    tracker = TaskTracker()

    mock_executor = AsyncMock()
    worker = DynamicWorker(
        agent_id="worker-test",
        agent_type="coder",
        bus=bus,
        executor=mock_executor,
        tracker=tracker,
        soul_prompt="You are a test worker.",
    )

    confirm_msgs: list[MeshMessage] = []

    async def capture(msg: MeshMessage):
        confirm_msgs.append(msg)

    await bus.subscribe("pm", capture)
    await worker.start()

    # Send COMPLETION_CHECK
    check_msg = MeshMessage(
        sender="pm", recipient="worker-test",
        msg_type=MessageType.COMPLETION_CHECK,
        content="Confirm completion",
        task_id="task-123",
        metadata={"requesting_agent": "pm"},
    )
    await worker.handle_message(check_msg)
    await asyncio.sleep(0.1)

    # Worker should respond with COMPLETION_CONFIRM
    assert len(confirm_msgs) == 1
    assert confirm_msgs[0].msg_type == MessageType.COMPLETION_CONFIRM
    assert confirm_msgs[0].metadata["status"] == "confirmed_done"
    assert confirm_msgs[0].metadata["agent_id"] == "worker-test"

    await worker.stop()
    await bus.shutdown()


async def test_single_subtask_no_assigned_agent():
    """If subtasks have no assigned agents, approve directly."""
    bus = AsyncioMessageBus()
    registry = AgentRegistry()
    tracker = TaskTracker()

    pm = PMAgent(
        bus=bus, executor=AsyncMock(), registry=registry, tracker=tracker,
        completion_check_timeout=1.0,
    )

    parent = Task(title="Parent", description="Parent")
    parent.transition(TaskState.DECOMPOSING)
    parent.transition(TaskState.ASSIGNED)
    tracker.add(parent)

    # Subtask with no assigned_to
    st = Task(title="Sub", description="Sub", parent_id=parent.id, assigned_to=None)
    st.transition(TaskState.DECOMPOSING)
    st.transition(TaskState.ASSIGNED)
    st.transition(TaskState.IN_PROGRESS)
    parent.subtask_ids.append(st.id)
    tracker.add(st)

    await pm.start()

    # Send result — subtask moves to REVIEW, triggers completion check
    result_msg = MeshMessage(
        sender="unknown", recipient="pm",
        msg_type=MessageType.RESULT,
        content="Done",
        task_id=st.id,
    )
    await pm.handle_message(result_msg)
    await asyncio.sleep(0.1)

    # Should approve directly (no agents to check)
    assert st.state == TaskState.DONE
    assert parent.state == TaskState.REVIEW

    await pm.stop()
    await bus.shutdown()
