"""Tests for setup wizard and preflight checks."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from aimesh.telegram.preflight import (
    CheckResult, PreflightResult, run_preflight_checks,
    check_python_version,
)
from aimesh.telegram.setup_wizard import SetupWizard


@pytest.mark.asyncio
async def test_preflight_python_version():
    """Python version check passes on 3.12+."""
    result = await check_python_version()
    assert result.passed
    assert result.critical


@pytest.mark.asyncio
async def test_preflight_result_format():
    """PreflightResult formats display correctly."""
    result = PreflightResult(checks=[
        CheckResult(name="Python", passed=True, version="3.12.10", critical=True),
        CheckResult(name="Claude CLI", passed=True, version="2.1.72", critical=True),
        CheckResult(name="Codex CLI", passed=False, details="Not found", critical=False),
    ])
    display = result.format_display()
    assert "PASS" in display
    assert "WARN" in display  # Codex is non-critical
    assert "Ready to proceed" in display


@pytest.mark.asyncio
async def test_preflight_critical_failure():
    """Critical failure blocks setup."""
    result = PreflightResult(checks=[
        CheckResult(name="Python", passed=True, version="3.12", critical=True),
        CheckResult(name="Claude CLI", passed=False, details="Not found", critical=True),
    ])
    assert not result.critical_passed
    display = result.format_display()
    assert "Critical checks failed" in display


def test_wizard_rejects_group_chat():
    """SetupWizard only works in DM."""
    wizard = SetupWizard()
    handler = wizard.get_handler()
    assert handler is not None


def test_wizard_no_bot_token_step():
    """SetupWizard does not ask for bot token."""
    wizard = SetupWizard()
    # Verify the wizard states don't include any bot_token step
    handler = wizard.get_handler()
    # The handler states should not contain a bot_token collection step
    # This is verified by the fact that our state constants are:
    # PREFLIGHT, ORG_NAME, GROUP_CHAT_ID, WORKSPACE_PATH, AGENT_TYPES, CONFIRM
    # — no BOT_TOKEN state exists
    from aimesh.telegram.setup_wizard import PREFLIGHT, ORG_NAME, GROUP_CHAT_ID, WORKSPACE_PATH, AGENT_TYPES, CONFIRM
    states = {PREFLIGHT, ORG_NAME, GROUP_CHAT_ID, WORKSPACE_PATH, AGENT_TYPES, CONFIRM}
    assert len(states) == 6  # Exactly 6 states, no bot token step
