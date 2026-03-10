"""Tests for LLM executor wrappers."""

import asyncio
from unittest.mock import AsyncMock, patch

from aimesh.agents.executor import (
    AnthropicExecutor,
    ClaudeCodeExecutor,
    ExecutionResult,
)


async def test_execution_result_defaults():
    result = ExecutionResult(content="hello")
    assert result.success is True
    assert result.cost_usd == 0.0
    assert result.error is None


async def test_claude_code_executor_non_blocking():
    """Verify ClaudeCodeExecutor doesn't block the event loop."""
    executor = ClaudeCodeExecutor()

    timer_ticks = []

    async def tick():
        for i in range(3):
            await asyncio.sleep(0.1)
            timer_ticks.append(i)

    # Mock the subprocess to avoid actually calling claude
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(
        b'{"result": "test output", "cost_usd": 0.01}',
        b"",
    ))
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        timer_task = asyncio.create_task(tick())
        result = await executor.execute("test prompt")
        await timer_task

    assert result.success
    assert result.content == "test output"
    assert result.cost_usd == 0.01
    assert len(timer_ticks) == 3  # timer was not blocked


async def test_claude_code_executor_failure():
    executor = ClaudeCodeExecutor()

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"Error: something went wrong"))
    mock_proc.returncode = 1

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await executor.execute("test")

    assert not result.success
    assert "something went wrong" in result.error


async def test_anthropic_executor_with_mock():
    """Test AnthropicExecutor with mocked client."""
    executor = AnthropicExecutor(api_key="test-key")

    mock_response = AsyncMock()
    mock_response.content = [AsyncMock(text="decomposed tasks")]
    mock_response.usage = AsyncMock(input_tokens=100, output_tokens=50)

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    executor._client = mock_client

    result = await executor.execute("Decompose this task")

    assert result.success
    assert result.content == "decomposed tasks"
    assert result.input_tokens == 100
    assert result.output_tokens == 50
    assert result.cost_usd > 0
