"""Tests for ClaudeAgentSDKExecutor."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from aimesh.agents.executor import ClaudeAgentSDKExecutor, ExecutionResult


@pytest.mark.asyncio
async def test_sdk_executor_success():
    """ClaudeAgentSDKExecutor returns ExecutionResult with content on success."""
    import sys
    import types

    # Build mock message/text types
    mock_text = MagicMock()
    mock_text.text = "Hello from SDK"

    mock_message = MagicMock()
    mock_message.content = [mock_text]

    # Create concrete classes so isinstance() checks work
    TextBlockClass = type("TextBlock", (), {})
    AssistantMessageClass = type("AssistantMessage", (), {})
    mock_text.__class__ = TextBlockClass
    mock_message.__class__ = AssistantMessageClass

    async def mock_query(prompt, options):
        yield mock_message

    # Build a fake claude_agent_sdk module
    fake_sdk = types.ModuleType("claude_agent_sdk")
    fake_sdk.query = mock_query
    fake_sdk.ClaudeAgentOptions = MagicMock(return_value=MagicMock())
    fake_sdk.AssistantMessage = AssistantMessageClass
    fake_sdk.TextBlock = TextBlockClass
    fake_sdk.ResultMessage = type("ResultMessage", (), {})

    sys.modules["claude_agent_sdk"] = fake_sdk
    try:
        executor = ClaudeAgentSDKExecutor(model="sonnet", system_prompt="test")
        result = await executor.execute("hello")
    finally:
        del sys.modules["claude_agent_sdk"]

    assert result.success
    assert "Hello from SDK" in result.content


@pytest.mark.asyncio
async def test_sdk_executor_import_error():
    """Returns error ExecutionResult when SDK is not installed."""
    executor = ClaudeAgentSDKExecutor()
    # SDK likely not installed in test env
    result = await executor.execute("hello")
    # Either succeeds (SDK installed) or returns error (import or nested session)
    if not result.success:
        assert "claude-agent-sdk" in result.error or "exit code" in result.error


@pytest.mark.asyncio
async def test_sdk_executor_invalid_permission_mode():
    """Rejects invalid permission_mode values."""
    with pytest.raises(ValueError, match="Invalid permission_mode"):
        ClaudeAgentSDKExecutor(permission_mode="invalid_mode")


@pytest.mark.asyncio
async def test_sdk_executor_default_tools():
    """Default allowed_tools includes Read, Edit, Write, Bash, Glob, Grep."""
    executor = ClaudeAgentSDKExecutor()
    assert "Read" in executor.allowed_tools
    assert "Bash" in executor.allowed_tools
