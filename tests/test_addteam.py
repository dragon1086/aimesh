"""Tests for /addteam command handler."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from aimesh.telegram.handlers import CommandHandlers


def _make_handlers(admin_ids=None):
    """Create CommandHandlers with mocked dependencies."""
    pm = MagicMock()
    pm.bus = MagicMock()
    tracker = MagicMock()
    registry = MagicMock()
    return CommandHandlers(
        pm=pm, tracker=tracker, registry=registry,
        admin_ids=admin_ids,
    )


@pytest.mark.asyncio
async def test_addteam_unauthorized():
    """Non-admin user gets rejected."""
    h = _make_handlers(admin_ids=[111])
    result = await h.handle_addteam(user_id=999, text="myteam", chat_id=-100)
    assert "Unauthorized" in result


@pytest.mark.asyncio
async def test_addteam_no_args():
    """Empty text shows usage."""
    h = _make_handlers()
    result = await h.handle_addteam(user_id=111, text="", chat_id=-100)
    assert "Usage" in result


@pytest.mark.asyncio
async def test_addteam_invalid_engine():
    """Invalid engine is rejected."""
    h = _make_handlers()
    result = await h.handle_addteam(user_id=111, text="myteam badengine", chat_id=-100)
    assert "Invalid engine" in result


@pytest.mark.asyncio
async def test_addteam_non_ascii_name():
    """Korean-only name is rejected (no ASCII chars)."""
    h = _make_handlers()
    result = await h.handle_addteam(user_id=111, text="한국팀", chat_id=-100)
    assert "ASCII" in result


@pytest.mark.asyncio
async def test_addteam_success(tmp_path):
    """Successful team creation with config files."""
    h = _make_handlers()
    with patch("aimesh.telegram.handlers.ORGS_DIR", tmp_path):
        with patch("aimesh.telegram.handlers.save_org_config") as mock_save:
            result = await h.handle_addteam(
                user_id=111, text="alpha codex", chat_id=-100,
            )

    assert "created" in result
    assert "alpha" in result
    assert "codex" in result
    mock_save.assert_called_once()

    config = mock_save.call_args[0][0]
    assert config.org_id == "alpha"
    assert config.pm.engine == "codex"
    assert config.telegram.group_chat_id == -100
    assert 111 in config.telegram.admin_user_ids

    # Soul files created
    assert (tmp_path / "alpha" / "soul.md").exists()
    assert (tmp_path / "alpha" / "souls" / "coder.md").exists()
    assert (tmp_path / "alpha" / "souls" / "researcher.md").exists()


@pytest.mark.asyncio
async def test_addteam_default_engine(tmp_path):
    """Default engine is claude_code."""
    h = _make_handlers()
    with patch("aimesh.telegram.handlers.ORGS_DIR", tmp_path):
        with patch("aimesh.telegram.handlers.save_org_config") as mock_save:
            result = await h.handle_addteam(
                user_id=111, text="beta", chat_id=-200,
            )

    assert "created" in result
    config = mock_save.call_args[0][0]
    assert config.pm.engine == "claude_code"


@pytest.mark.asyncio
async def test_addteam_already_exists(tmp_path):
    """Duplicate team name is rejected."""
    h = _make_handlers()
    # Create existing config
    org_dir = tmp_path / "gamma"
    org_dir.mkdir()
    (org_dir / "config.yaml").write_text("org_id: gamma")

    with patch("aimesh.telegram.handlers.ORGS_DIR", tmp_path):
        result = await h.handle_addteam(
            user_id=111, text="gamma", chat_id=-100,
        )

    assert "already exists" in result
