"""Tests for DynamicWorker agent."""
import asyncio
import pytest
from unittest.mock import AsyncMock

from aimesh.agents.dynamic_worker import DynamicWorker
from aimesh.agents.executor import ExecutionResult
from aimesh.core.bus import AsyncioMessageBus
from aimesh.core.message import MeshMessage, MessageType
from aimesh.tasks.tracker import TaskTracker


@pytest.fixture
def bus():
    return AsyncioMessageBus()


@pytest.fixture
def tracker():
    return TaskTracker()


@pytest.fixture
def mock_executor():
    executor = AsyncMock()
    executor.execute = AsyncMock(return_value=ExecutionResult(
        content="Task completed successfully",
        success=True,
        input_tokens=500,
        cost_usd=0.01,
    ))
    return executor


@pytest.fixture
def worker(bus, mock_executor, tracker):
    return DynamicWorker(
        agent_id="test-worker-1",
        agent_type="tester",
        bus=bus,
        executor=mock_executor,
        tracker=tracker,
        soul_prompt="You are a test agent.",
        capabilities=["test", "verify"],
    )


@pytest.mark.asyncio
async def test_dynamic_worker_executes_task(worker, bus, mock_executor):
    """DynamicWorker executes task with soul_prompt prepended."""
    received = []

    async def collect(msg):
        received.append(msg)

    await bus.subscribe("pm", collect)
    await bus.subscribe("broadcast", collect)
    await worker.start()

    msg = MeshMessage(
        sender="pm",
        recipient="test-worker-1",
        msg_type=MessageType.TASK_ASSIGN,
        content="Run all tests",
        task_id="task-1",
    )
    await worker.handle_message(msg)
    await asyncio.sleep(0.1)

    # Verify soul_prompt was prepended
    call_args = mock_executor.execute.call_args
    prompt = call_args[0][0] if call_args[0] else call_args[1].get("prompt", "")
    assert "You are a test agent" in prompt
    assert "Run all tests" in prompt

    # Verify RESULT was sent to PM
    results = [m for m in received if m.msg_type == MessageType.RESULT]
    assert len(results) >= 1
    assert results[0].recipient == "pm"
    assert "Task completed" in results[0].content

    await bus.shutdown()


@pytest.mark.asyncio
async def test_dynamic_worker_any_type(bus, mock_executor, tracker):
    """DynamicWorker with agent_type='designer' works identically to 'coder'."""
    designer = DynamicWorker(
        agent_id="designer-1",
        agent_type="designer",
        bus=bus,
        executor=mock_executor,
        tracker=tracker,
        soul_prompt="You are a UI designer.",
        capabilities=["design", "mockup"],
    )
    assert designer.agent_type == "designer"
    assert designer.capabilities == ["design", "mockup"]
    await bus.shutdown()


@pytest.mark.asyncio
async def test_dynamic_worker_retries_then_fails(bus, tracker):
    """After 3 failures, DynamicWorker sends FAILED result."""
    failing_executor = AsyncMock()
    failing_executor.execute = AsyncMock(return_value=ExecutionResult(
        content="", success=False, error="API error",
    ))

    worker = DynamicWorker(
        agent_id="fail-worker",
        agent_type="coder",
        bus=bus,
        executor=failing_executor,
        tracker=tracker,
        soul_prompt="You are a coder.",
    )

    received = []

    async def collect(msg):
        received.append(msg)

    await bus.subscribe("pm", collect)
    await bus.subscribe("broadcast", collect)
    await worker.start()

    msg = MeshMessage(
        sender="pm", recipient="fail-worker",
        msg_type=MessageType.TASK_ASSIGN, content="Do something",
        task_id="task-fail",
    )

    # Call 3 times to exhaust retries
    for _ in range(3):
        await worker.handle_message(msg)

    await asyncio.sleep(0.1)

    # Should have a FAILED result
    results = [m for m in received if m.msg_type == MessageType.RESULT]
    assert len(results) >= 1
    assert "FAILED" in results[-1].content

    await bus.shutdown()


@pytest.mark.asyncio
async def test_dynamic_worker_completion_check(worker, bus):
    """DynamicWorker responds to COMPLETION_CHECK with COMPLETION_CONFIRM."""
    received = []

    async def collect(msg):
        received.append(msg)

    await bus.subscribe("pm", collect)
    await worker.start()

    msg = MeshMessage(
        sender="pm", recipient="test-worker-1",
        msg_type=MessageType.COMPLETION_CHECK,
        content="Confirm completion",
        task_id="task-1",
    )
    await worker.handle_message(msg)
    await asyncio.sleep(0.1)

    confirms = [m for m in received if m.msg_type == MessageType.COMPLETION_CONFIRM]
    assert len(confirms) == 1
    assert confirms[0].metadata["status"] == "confirmed_done"

    await bus.shutdown()
