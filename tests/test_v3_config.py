"""Tests for v3 config additions and engine selection."""

from aimesh.config import PMConfig, OrgConfig, EngineConfig


# --- PMConfig engine Tests ---


def test_pm_config_default_engine():
    """PMConfig defaults to 'claude_code' engine."""
    config = PMConfig()
    assert config.engine == "claude_code"


def test_pm_config_codex_engine():
    """PMConfig accepts 'codex' engine."""
    config = PMConfig(engine="codex")
    assert config.engine == "codex"


def test_pm_config_gemini_engine():
    """PMConfig accepts 'gemini' engine."""
    config = PMConfig(engine="gemini")
    assert config.engine == "gemini"


def test_pm_config_anthropic_legacy():
    """PMConfig accepts 'anthropic' for legacy API mode."""
    config = PMConfig(engine="anthropic")
    assert config.engine == "anthropic"


def test_org_config_backward_compat():
    """OrgConfig loads with default PMConfig (no engine in YAML)."""
    config = OrgConfig(org_id="test-org")
    assert config.pm.engine == "claude_code"
    assert config.pm.model == "claude-sonnet-4-20250514"
    assert config.pm.tools_enabled is True


def test_org_config_with_engine():
    """OrgConfig loads with engine specified."""
    config = OrgConfig(
        org_id="test-org",
        pm=PMConfig(engine="codex"),
    )
    assert config.pm.engine == "codex"


def test_org_config_save_load_roundtrip(tmp_path):
    """OrgConfig with engine survives save/load cycle."""
    import yaml
    config = OrgConfig(
        org_id="roundtrip-test",
        pm=PMConfig(engine="codex", model="sonnet"),
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

    assert loaded.pm.engine == "codex"
    assert loaded.pm.model == "sonnet"


# --- Engine config Tests ---


def test_engine_config_get_command():
    """EngineConfig returns correct command for each engine."""
    ec = EngineConfig()
    assert ec.get_command("claude_code") == "claude --dangerously-skip-permissions"
    assert ec.get_command("codex") == "codex --full-auto"
    assert ec.get_command("gemini") == "gemini-cli"


def test_engine_config_unknown_fallback():
    """EngineConfig falls back to claude_code for unknown engine."""
    ec = EngineConfig()
    assert ec.get_command("unknown") == "claude --dangerously-skip-permissions"


def test_anthropic_branch_creates_tool_executor():
    """engine 'anthropic' creates ToolUsingExecutor (legacy path)."""
    from aimesh.agents.executor import AnthropicExecutor, ToolUsingExecutor
    from aimesh.agents.pm_tools import PM_TOOL_SCHEMAS, PMToolHandlers, create_pm_dispatcher

    pm_tool_handlers = PMToolHandlers()
    pm_dispatcher = create_pm_dispatcher(pm_tool_handlers)

    config = PMConfig(engine="anthropic")
    assert config.engine == "anthropic"

    inner = AnthropicExecutor(model=config.model, api_key="test-key")
    executor = ToolUsingExecutor(
        inner=inner, tools=PM_TOOL_SCHEMAS, tool_dispatcher=pm_dispatcher,
    )
    assert isinstance(executor, ToolUsingExecutor)
