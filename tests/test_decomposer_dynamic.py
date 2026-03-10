"""Tests for dynamic agent types in decomposer."""
import pytest
from unittest.mock import AsyncMock

from aimesh.agents.executor import ExecutionResult
from aimesh.core.registry import AgentRegistry
from aimesh.core.task import Task
from aimesh.tasks.decomposer import decompose_task, _build_agent_types_from_registry


def test_build_agent_types_from_registry():
    """Registry with designer agent includes it in the output."""
    registry = AgentRegistry()
    registry.register("coder-1", "coder", ["code", "implement"])
    registry.register("designer-1", "designer", ["design", "mockup"])

    result = _build_agent_types_from_registry(registry)
    assert "designer" in result
    assert "coder" in result
    assert "mockup" in result


def test_build_agent_types_empty_registry():
    """Empty registry returns sensible defaults."""
    registry = AgentRegistry()
    result = _build_agent_types_from_registry(registry)
    assert "coder" in result  # Falls back to defaults


@pytest.mark.asyncio
async def test_decompose_with_registry():
    """decompose_task with registry includes dynamic agent types in prompt."""
    registry = AgentRegistry()
    registry.register("coder-1", "coder", ["code"])
    registry.register("designer-1", "designer", ["design", "ui"])

    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock(return_value=ExecutionResult(
        content='{"subtasks": [{"title": "Design the UI", "description": "Create mockups", "agent_type": "designer", "priority": 1}]}',
        success=True,
    ))

    task = Task(title="Build a landing page", description="Create a beautiful landing page")
    result = await decompose_task(task, mock_executor, registry=registry)

    # Verify the prompt included designer
    call_args = mock_executor.execute.call_args
    prompt = call_args[0][0] if call_args[0] else call_args[1].get("prompt", "")
    assert "designer" in prompt

    # Verify the result includes the designer subtask
    assert len(result) >= 1
    assert result[0]["agent_type"] == "designer"
