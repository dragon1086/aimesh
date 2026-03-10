"""Tests for ToolUsingExecutor and PM tools."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from aimesh.agents.executor import (
    BaseExecutor, ExecutionResult, ToolDispatcher, ToolUsingExecutor,
)
from aimesh.agents.pm_tools import PMToolHandlers, PM_TOOL_SCHEMAS, create_pm_dispatcher


@pytest.mark.asyncio
async def test_tool_dispatcher_routes_correctly():
    """ToolDispatcher routes tool calls to registered handlers."""
    dispatcher = ToolDispatcher()
    handler = AsyncMock(return_value={"result": "ok"})
    dispatcher.register("test_tool", handler)

    result = await dispatcher.dispatch("test_tool", {"arg1": "value1"})
    handler.assert_called_once_with(arg1="value1")
    assert "ok" in result


@pytest.mark.asyncio
async def test_tool_dispatcher_unknown_tool():
    """ToolDispatcher returns error for unknown tools."""
    dispatcher = ToolDispatcher()
    result = await dispatcher.dispatch("nonexistent", {})
    assert "Unknown tool" in result


@pytest.mark.asyncio
async def test_tool_using_executor_no_tools_passthrough():
    """When inner executor returns plain text (no tool_use), pass through."""
    inner = AsyncMock(spec=BaseExecutor)
    inner.execute = AsyncMock(return_value=ExecutionResult(
        content="Hello world", success=True, input_tokens=10, output_tokens=0, cost_usd=0.001
    ))

    dispatcher = ToolDispatcher()
    executor = ToolUsingExecutor(inner=inner, tools=[], tool_dispatcher=dispatcher)

    result = await executor.execute("hello")
    assert result.success
    assert result.content == "Hello world"


@pytest.mark.asyncio
async def test_tool_using_executor_loops_on_tool_use():
    """ToolUsingExecutor loops when inner returns tool_use, then returns final text."""
    inner = AsyncMock(spec=BaseExecutor)

    # First call: return a tool_use block
    tool_use_response = json.dumps([{
        "type": "tool_use",
        "id": "call_1",
        "name": "list_agents",
        "input": {},
    }])

    # Second call: return final text
    inner.execute = AsyncMock(side_effect=[
        ExecutionResult(content=tool_use_response, success=True, input_tokens=10, output_tokens=0, cost_usd=0.001),
        ExecutionResult(content="There are 3 agents registered.", success=True, input_tokens=10, output_tokens=5, cost_usd=0.002),
    ])

    dispatcher = ToolDispatcher()
    dispatcher.register("list_agents", AsyncMock(return_value={"agents": [], "count": 3}))

    executor = ToolUsingExecutor(inner=inner, tools=[], tool_dispatcher=dispatcher)
    result = await executor.execute("list agents")

    assert result.success
    assert "3 agents" in result.content
    assert result.input_tokens + result.output_tokens == 25  # 10 + 0 + 10 + 5
    assert inner.execute.call_count == 2


@pytest.mark.asyncio
async def test_tool_using_executor_max_iterations():
    """ToolUsingExecutor stops after max_iterations to prevent runaway."""
    inner = AsyncMock(spec=BaseExecutor)

    # Always return tool_use — never a final text response
    tool_use = json.dumps([{"type": "tool_use", "id": "x", "name": "loop_tool", "input": {}}])
    inner.execute = AsyncMock(return_value=ExecutionResult(
        content=tool_use, success=True, input_tokens=5, output_tokens=0, cost_usd=0.001
    ))

    dispatcher = ToolDispatcher()
    dispatcher.register("loop_tool", AsyncMock(return_value="ok"))

    executor = ToolUsingExecutor(
        inner=inner, tools=[], tool_dispatcher=dispatcher, max_iterations=3
    )
    result = await executor.execute("do something")

    assert not result.success
    assert "exceeded" in result.error.lower() or "iterations" in result.error.lower()
    assert inner.execute.call_count == 3


@pytest.mark.asyncio
async def test_pm_tool_schemas_valid():
    """PM_TOOL_SCHEMAS has all 6 required tools."""
    tool_names = {t["name"] for t in PM_TOOL_SCHEMAS}
    assert tool_names == {
        "spawn_agent", "teardown_agent", "list_agents",
        "assign_task", "check_task_status", "request_completion_check",
    }


@pytest.mark.asyncio
async def test_pm_tool_handlers_list_agents():
    """PMToolHandlers.list_agents returns agent info from registry."""
    handlers = PMToolHandlers()
    mock_registry = MagicMock()
    mock_entry = MagicMock()
    mock_entry.agent_id = "coder-1"
    mock_entry.agent_type = "coder"
    mock_entry.capabilities = ["code"]
    mock_entry.status = "idle"
    mock_registry.all.return_value = [mock_entry]
    handlers.registry = mock_registry

    result = await handlers.list_agents()
    assert result["count"] == 1
    assert result["agents"][0]["agent_id"] == "coder-1"
