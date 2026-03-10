"""Integration tests for AI Mesh v2 — end-to-end lifecycle and runtime spawning."""

import asyncio
import json
from unittest.mock import AsyncMock, patch, MagicMock

from aimesh.agents.executor import ExecutionResult, ToolDispatcher, ToolUsingExecutor
from aimesh.agents.factory import AgentFactory
from aimesh.agents.pm import PMAgent
from aimesh.agents.pm_tools import PMToolHandlers, PM_TOOL_SCHEMAS, create_pm_dispatcher
from aimesh.core.bus import AsyncioMessageBus
from aimesh.core.message import MeshMessage, MessageType
from aimesh.core.registry import AgentRegistry
from aimesh.core.task import Task, TaskState
from aimesh.tasks.tracker import TaskTracker


async def test_full_lifecycle_with_completion_protocol():
    """Full lifecycle: decompose → assign → execute → completion check → parent REVIEW.

    Boots from OrgConfig-style setup with AgentFactory, mocked executors,
    and verifies the entire flow including completion confirmation.
    """
    bus = AsyncioMessageBus()
    registry = AgentRegistry()
    tracker = TaskTracker()

    # Create factory with mocked ClaudeAgentSDKExecutor
    factory = AgentFactory(
        bus=bus, registry=registry, tracker=tracker,
        default_model="sonnet", default_workspace="./workspace",
    )

    # Mock ClaudeAgentSDKExecutor so factory.spawn_worker doesn't need real SDK
    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock(return_value=ExecutionResult(
        content="Task completed successfully",
        success=True,
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.01,
    ))

    with patch("aimesh.agents.factory.ClaudeAgentSDKExecutor", return_value=mock_executor):
        # Spawn 2 workers (simulating OrgConfig default roster)
        w1 = await factory.spawn_worker(
            agent_type="coder", soul_prompt="You are a coder.",
            capabilities=["code"], agent_id="coder-1",
        )
        w2 = await factory.spawn_worker(
            agent_type="researcher", soul_prompt="You are a researcher.",
            capabilities=["research"], agent_id="researcher-1",
        )

    # Verify workers registered
    assert len(registry.all_agents()) >= 2
    assert registry.discover_by_type("coder")
    assert registry.discover_by_type("researcher")

    # Create PM with mock executor that returns decomposition
    pm_mock_executor = AsyncMock()
    pm_mock_executor.execute = AsyncMock(return_value=ExecutionResult(
        content=json.dumps({
            "subtasks": [
                {"title": "Write code", "description": "Implement feature",
                 "agent_type": "coder", "priority": 1},
                {"title": "Research API", "description": "Find best API",
                 "agent_type": "researcher", "priority": 2},
            ]
        }),
        success=True,
    ))

    pm = PMAgent(
        bus=bus, executor=pm_mock_executor, registry=registry, tracker=tracker,
        completion_check_timeout=5.0,
    )
    registry.register("pm", "pm", ["decompose", "assign"])
    await pm.start()

    # Subscribe PM to receive COMPLETION_CONFIRM messages
    await bus.subscribe("pm", pm.handle_message)

    # Submit task to PM
    task_msg = MeshMessage(
        sender="human", recipient="pm",
        msg_type=MessageType.TASK_ASSIGN,
        content="Build a user system",
    )
    await pm.handle_message(task_msg)
    await asyncio.sleep(0.2)

    # Verify decomposition
    all_tasks = tracker.get_all_tasks()
    assert len(all_tasks) >= 3  # 1 parent + 2 subtasks

    parent = [t for t in all_tasks if t.parent_id is None][0]
    subtasks = tracker.get_subtasks(parent.id)
    assert len(subtasks) == 2

    # Verify subtasks were assigned
    assert subtasks[0].assigned_to == "coder-1"
    assert subtasks[1].assigned_to == "researcher-1"

    # Simulate workers completing — the mock executor was set up at factory time,
    # so the DynamicWorkers already have it. We need to trigger them via bus messages.
    # The PM already sent TASK_ASSIGN, so workers should have received them.
    # Wait for workers to process tasks
    await asyncio.sleep(0.5)

    # Workers should have sent results back. Check subtask states.
    # The DynamicWorker sends STATUS_UPDATE first (-> IN_PROGRESS), then RESULT (-> REVIEW).
    # The PM handles the STATUS_UPDATE and RESULT messages via bus subscription.
    # Since PM is subscribed, the completion protocol should have run.

    # Give time for the full round-trip
    await asyncio.sleep(0.5)

    # Verify final states
    for st in subtasks:
        assert st.state == TaskState.DONE, f"Subtask '{st.title}' is {st.state}, expected DONE"

    assert parent.state == TaskState.REVIEW, f"Parent is {parent.state}, expected REVIEW"

    # Verify soul_prompt was prepended in executor calls
    for call_args in mock_executor.execute.call_args_list:
        prompt = call_args[0][0]
        assert "You are a" in prompt, "Soul prompt should be prepended"

    await pm.stop()
    await factory.teardown_all()
    await bus.shutdown()


async def test_runtime_spawning_and_teardown():
    """Boot with 2 agents, spawn 3rd mid-run, verify it works, then teardown."""
    bus = AsyncioMessageBus()
    registry = AgentRegistry()
    tracker = TaskTracker()

    factory = AgentFactory(
        bus=bus, registry=registry, tracker=tracker,
        default_model="sonnet",
    )

    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock(return_value=ExecutionResult(
        content="Done", success=True, input_tokens=10, output_tokens=5, cost_usd=0.001,
    ))

    with patch("aimesh.agents.factory.ClaudeAgentSDKExecutor", return_value=mock_executor):
        # Boot with 2 agents
        await factory.spawn_worker("coder", "Coder soul", ["code"], agent_id="coder-1")
        await factory.spawn_worker("researcher", "Researcher soul", ["research"], agent_id="researcher-1")

        assert len(factory.list_workers()) == 2
        assert len(registry.all_agents()) == 2

        # Spawn 3rd agent at runtime (PM would do this via spawn_agent tool)
        w3 = await factory.spawn_worker(
            agent_type="designer",
            soul_prompt="You are a UI designer specializing in modern interfaces.",
            capabilities=["design", "mockup"],
            agent_id="designer-1",
        )

    assert len(factory.list_workers()) == 3
    assert len(registry.all_agents()) == 3
    assert registry.discover_by_type("designer")

    # Verify 3rd agent can receive and process a task
    result_msgs: list[MeshMessage] = []

    async def capture(msg: MeshMessage):
        result_msgs.append(msg)

    await bus.subscribe("pm", capture)

    task_msg = MeshMessage(
        sender="pm", recipient="designer-1",
        msg_type=MessageType.TASK_ASSIGN,
        content="Design a login page",
        task_id="task-design-1",
    )
    await w3.handle_message(task_msg)
    await asyncio.sleep(0.2)

    # Designer should have sent result back to PM
    results = [m for m in result_msgs if m.msg_type == MessageType.RESULT]
    assert len(results) >= 1
    assert results[0].sender == "designer-1"

    # Verify soul_prompt was prepended
    call_prompt = mock_executor.execute.call_args_list[-1][0][0]
    assert "UI designer" in call_prompt

    # Teardown 3rd agent
    success = await factory.teardown_worker("designer-1")
    assert success
    assert len(factory.list_workers()) == 2
    assert len(registry.all_agents()) == 2
    assert not registry.discover_by_type("designer")

    await factory.teardown_all()
    await bus.shutdown()


async def test_pm_tool_dispatch_all_tools():
    """Verify all 6 PM tools dispatch correctly via create_pm_dispatcher."""
    bus = AsyncioMessageBus()
    registry = AgentRegistry()
    tracker = TaskTracker()

    factory = AgentFactory(
        bus=bus, registry=registry, tracker=tracker,
        default_model="sonnet",
    )

    handlers = PMToolHandlers()
    handlers.set_dependencies(
        factory=factory, registry=registry, tracker=tracker, bus=bus,
    )
    dispatcher = create_pm_dispatcher(handlers)

    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock(return_value=ExecutionResult(
        content="Done", success=True, input_tokens=10, output_tokens=5, cost_usd=0.001,
    ))

    with patch("aimesh.agents.factory.ClaudeAgentSDKExecutor", return_value=mock_executor):
        # 1. spawn_agent
        result = await dispatcher.dispatch("spawn_agent", {
            "agent_type": "tester",
            "soul_prompt": "You are a QA tester.",
            "capabilities": ["test", "verify"],
        })
        assert "spawned" in result or "tester" in result

    # 2. list_agents
    result = await dispatcher.dispatch("list_agents", {})
    assert "count" in result or "agents" in result

    # 3. assign_task — need a subscriber to avoid undelivered messages
    async def sink(msg):
        pass
    tester_agents = registry.discover_by_type("tester")
    tester_id = tester_agents[0].agent_id if tester_agents else "tester-unknown"
    await bus.subscribe(tester_id, sink)

    result = await dispatcher.dispatch("assign_task", {
        "agent_id": tester_id,
        "task_description": "Run integration tests",
    })
    assert "task_id" in result

    # 4. check_task_status
    all_tasks = tracker.get_all_tasks()
    if all_tasks:
        result = await dispatcher.dispatch("check_task_status", {
            "task_id": all_tasks[0].id,
        })
        assert "state" in result

    # 5. request_completion_check
    result = await dispatcher.dispatch("request_completion_check", {
        "task_id": all_tasks[0].id if all_tasks else "nonexistent",
    })
    # Either returns agents_checked or error for nonexistent
    assert "agents_checked" in result or "error" in result

    # 6. teardown_agent
    result = await dispatcher.dispatch("teardown_agent", {
        "agent_id": tester_id,
    })
    assert "removed" in result

    await bus.shutdown()


async def test_dynamic_decomposer_uses_registry_types():
    """Decomposer builds AVAILABLE AGENT TYPES from registry, not hardcoded."""
    from aimesh.tasks.decomposer import decompose_task

    registry = AgentRegistry()
    registry.register("coder-1", "coder", ["code"])
    registry.register("researcher-1", "researcher", ["research"])
    registry.register("designer-1", "designer", ["design", "mockup"])

    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock(return_value=ExecutionResult(
        content=json.dumps({"subtasks": [
            {"title": "Design UI", "agent_type": "designer", "priority": 1},
        ]}),
        success=True,
    ))

    parent_task = Task(title="Build app", description="Build a web application")

    result = await decompose_task(parent_task, mock_executor, registry=registry)

    # Verify the prompt sent to executor contained all 3 types
    call_args = mock_executor.execute.call_args
    prompt = call_args[0][0]
    assert "coder" in prompt
    assert "researcher" in prompt
    assert "designer" in prompt

    # Verify decomposition result
    assert len(result) == 1
    assert result[0]["agent_type"] == "designer"


async def test_setup_wizard_preflight():
    """Setup wizard preflight checks return structured results."""
    from aimesh.telegram.preflight import check_python_version, PreflightResult

    # Test Python check directly (doesn't need shell commands)
    python_result = await check_python_version()
    assert python_result.passed
    assert "3.12" in python_result.version

    # Verify PreflightResult aggregation
    result = PreflightResult(checks=[python_result])
    assert result.critical_passed
    assert result.all_passed
    assert hasattr(result, "format_display")
    display = result.format_display()
    assert "Python" in display


async def test_org_config_no_bot_token():
    """OrgConfig should not contain bot_token field."""
    from aimesh.config import OrgConfig

    config = OrgConfig(
        org_id="test",
        org_name="Test Org",
        agents=[],
    )
    data = config.model_dump()

    # bot_token should NOT be in OrgConfig
    assert "bot_token" not in data
    assert "telegram_bot_token" not in data

    # soul_file should be available in agent entries
    from aimesh.config import AgentEntry
    entry = AgentEntry(
        id="coder-1", type="coder",
        soul_file="souls/coder.md",
        capabilities=["code"],
    )
    assert entry.soul_file == "souls/coder.md"
