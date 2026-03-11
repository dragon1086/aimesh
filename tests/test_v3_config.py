"""Tests for v3 config additions and SDK executor swap."""

from aimesh.config import PMConfig, OrgConfig


# --- PMConfig engine_type Tests ---


def test_pm_config_default_engine_type():
    """PMConfig defaults to 'anthropic' engine_type."""
    config = PMConfig()
    assert config.engine_type == "anthropic"


def test_pm_config_claude_sdk_engine():
    """PMConfig accepts 'claude_sdk' engine_type."""
    config = PMConfig(engine_type="claude_sdk")
    assert config.engine_type == "claude_sdk"


def test_pm_config_tmux_engine():
    """PMConfig accepts 'tmux' engine_type."""
    config = PMConfig(engine_type="tmux")
    assert config.engine_type == "tmux"


def test_org_config_backward_compat():
    """OrgConfig loads with default PMConfig (no engine_type in YAML)."""
    config = OrgConfig(org_id="test-org")
    assert config.pm.engine_type == "anthropic"
    assert config.pm.model == "claude-sonnet-4-20250514"
    assert config.pm.tools_enabled is True


def test_org_config_with_engine_type():
    """OrgConfig loads with engine_type specified."""
    config = OrgConfig(
        org_id="test-org",
        pm=PMConfig(engine_type="claude_sdk"),
    )
    assert config.pm.engine_type == "claude_sdk"


def test_org_config_save_load_roundtrip(tmp_path):
    """OrgConfig with engine_type survives save/load cycle."""
    import yaml
    config = OrgConfig(
        org_id="roundtrip-test",
        pm=PMConfig(engine_type="claude_sdk", model="sonnet"),
    )

    # Save
    org_dir = tmp_path / "roundtrip-test"
    org_dir.mkdir()
    config_path = org_dir / "config.yaml"
    data = config.model_dump()
    with open(config_path, "w") as f:
        yaml.dump(data, f)

    # Load
    with open(config_path) as f:
        loaded_data = yaml.safe_load(f)
    loaded = OrgConfig(**loaded_data)

    assert loaded.pm.engine_type == "claude_sdk"
    assert loaded.pm.model == "sonnet"


# --- main.py executor branching Tests ---


def test_anthropic_branch_creates_tool_executor():
    """engine_type 'anthropic' creates ToolUsingExecutor (v2.1 path)."""
    from aimesh.agents.executor import AnthropicExecutor, ToolUsingExecutor
    from aimesh.agents.pm_tools import PM_TOOL_SCHEMAS, PMToolHandlers, create_pm_dispatcher

    pm_tool_handlers = PMToolHandlers()
    pm_dispatcher = create_pm_dispatcher(pm_tool_handlers)

    config = PMConfig(engine_type="anthropic")
    assert config.engine_type == "anthropic"

    inner = AnthropicExecutor(model=config.model, api_key="test-key")
    executor = ToolUsingExecutor(
        inner=inner, tools=PM_TOOL_SCHEMAS, tool_dispatcher=pm_dispatcher,
    )
    assert isinstance(executor, ToolUsingExecutor)


def test_claude_sdk_branch_creates_sdk_executor():
    """engine_type 'claude_sdk' creates ClaudeAgentSDKExecutor (v2.5 path)."""
    from aimesh.agents.executor import ClaudeAgentSDKExecutor

    config = PMConfig(engine_type="claude_sdk")
    assert config.engine_type == "claude_sdk"

    executor = ClaudeAgentSDKExecutor(
        model="sonnet",
        system_prompt="You are the PM agent.",
        permission_mode="acceptEdits",
        max_turns=15,
    )
    assert isinstance(executor, ClaudeAgentSDKExecutor)
    assert executor.model == "sonnet"
    assert executor.max_turns == 15
