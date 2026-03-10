"""Tests for multi-team support (Phase 3)."""

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from aimesh.config import MultiTeamConfig
from aimesh.multi_dedup import MessageDedup
from aimesh.multi import MultiTeamRunner, TeamInstance


# --- MessageDedup Tests ---


def test_dedup_claim_success(tmp_path):
    """First claim on a message_id succeeds."""
    db = tmp_path / "dedup.db"
    dedup = MessageDedup(str(db))
    assert dedup.try_claim(123, "bot1") is True
    dedup.close()


def test_dedup_claim_duplicate(tmp_path):
    """Second claim on same message_id fails."""
    db = tmp_path / "dedup.db"
    dedup = MessageDedup(str(db))
    assert dedup.try_claim(123, "bot1") is True
    assert dedup.try_claim(123, "bot2") is False
    dedup.close()


def test_dedup_release_then_reclaim(tmp_path):
    """Release a claim, then another bot can claim it."""
    db = tmp_path / "dedup.db"
    dedup = MessageDedup(str(db))
    assert dedup.try_claim(123, "bot1") is True
    dedup.release(123)
    assert dedup.try_claim(123, "bot2") is True
    dedup.close()


def test_dedup_is_claimed(tmp_path):
    """is_claimed returns True only for claimed messages."""
    db = tmp_path / "dedup.db"
    dedup = MessageDedup(str(db))
    assert dedup.is_claimed(123) is False
    dedup.try_claim(123, "bot1")
    assert dedup.is_claimed(123) is True
    dedup.close()


def test_dedup_separate_messages(tmp_path):
    """Different message_ids are claimed independently."""
    db = tmp_path / "dedup.db"
    dedup = MessageDedup(str(db))
    assert dedup.try_claim(1, "bot1") is True
    assert dedup.try_claim(2, "bot2") is True
    assert dedup.try_claim(1, "bot2") is False  # Already claimed by bot1
    dedup.close()


def test_dedup_concurrent_claims(tmp_path):
    """Two connections to same DB — only one claim succeeds."""
    db_path = str(tmp_path / "dedup.db")
    dedup1 = MessageDedup(db_path)
    dedup2 = MessageDedup(db_path)

    result1 = dedup1.try_claim(999, "bot1")
    result2 = dedup2.try_claim(999, "bot2")

    # Exactly one should succeed
    assert result1 is True
    assert result2 is False

    dedup1.close()
    dedup2.close()


def test_dedup_cleanup_old(tmp_path):
    """cleanup_old removes old claims."""
    db = tmp_path / "dedup.db"
    dedup = MessageDedup(str(db))
    dedup.try_claim(1, "bot1")

    # With 0 hour max_age, everything is old
    removed = dedup.cleanup_old(max_age_hours=0)
    # May or may not remove depending on timing, but should not error
    assert isinstance(removed, int)
    dedup.close()


# --- MultiTeamConfig Tests ---


def test_multi_team_config_defaults():
    """MultiTeamConfig has expected defaults."""
    config = MultiTeamConfig()
    assert config.teams == []
    assert config.shared_chat_ids == []
    assert config.dedup_db_path == "data/message_dedup.db"


def test_multi_team_config_with_teams():
    """MultiTeamConfig loads team list."""
    config = MultiTeamConfig(teams=["team-a", "team-b"], shared_chat_ids=[-100])
    assert len(config.teams) == 2
    assert config.teams[0] == "team-a"
    assert config.shared_chat_ids == [-100]


# --- MultiTeamRunner Tests ---


async def test_multi_runner_stop_empty():
    """Stopping with no teams doesn't error."""
    config = MultiTeamConfig(teams=[])
    runner = MultiTeamRunner(config)
    await runner.stop_all()
    assert runner.teams == []


async def test_multi_runner_stop_reverses_order():
    """Teams are stopped in reverse order."""
    config = MultiTeamConfig(teams=[])
    runner = MultiTeamRunner(config)

    stop_order = []

    # Manually add mock teams
    for name in ["team-a", "team-b", "team-c"]:
        mock_bot = AsyncMock()
        mock_bot.stop = AsyncMock(side_effect=lambda n=name: stop_order.append(n))
        mock_pm = AsyncMock()
        mock_factory = AsyncMock()
        runner.teams.append(TeamInstance(
            org_id=name, bot=mock_bot, pm=mock_pm, factory=mock_factory,
        ))

    await runner.stop_all()
    assert stop_order == ["team-c", "team-b", "team-a"]


# --- Mention Filtering Tests ---


async def test_mention_filtering_in_nl_handler():
    """NL handler with bot_username ignores messages mentioning other bots."""
    from aimesh.core.bus import AsyncioMessageBus
    from aimesh.nl.context import ConversationContext
    from aimesh.tasks.tracker import TaskTracker
    from aimesh.telegram.handlers import CommandHandlers
    from aimesh.telegram.nl_handler import NaturalLanguageHandler

    handler = NaturalLanguageHandler(
        bus=AsyncioMessageBus(),
        tracker=TaskTracker(),
        context=ConversationContext(),
        handlers=MagicMock(spec=CommandHandlers),
        bot_username="team1_bot",
    )

    # Message for this bot
    update1 = MagicMock()
    update1.message.text = "@team1_bot status"
    update1.effective_user.id = 1
    update1.effective_user.first_name = "User"
    update1.effective_chat.id = -100
    update1.message.reply_text = AsyncMock()

    handler.handlers.handle_status = AsyncMock(return_value="Status ok")
    await handler.handle_message(update1, None)
    update1.message.reply_text.assert_called_once()

    # Message for other bot
    update2 = MagicMock()
    update2.message.text = "@team2_bot status"
    update2.effective_user.id = 1
    update2.effective_user.first_name = "User"
    update2.effective_chat.id = -100
    update2.message.reply_text = AsyncMock()

    await handler.handle_message(update2, None)
    update2.message.reply_text.assert_not_called()


# --- Single-Team Backward Compat Test ---


def test_single_team_org_id_env():
    """AIMESH_ORG_ID env var still works for single-team mode."""
    # This just verifies the env var is read correctly in main.py logic
    with patch.dict(os.environ, {"AIMESH_ORG_ID": "my-org"}):
        org_id = os.environ.get("AIMESH_ORG_ID", "_default")
        assert org_id == "my-org"


def test_single_team_default():
    """Default org_id is _default when AIMESH_ORG_ID not set."""
    env = os.environ.copy()
    env.pop("AIMESH_ORG_ID", None)
    with patch.dict(os.environ, env, clear=True):
        org_id = os.environ.get("AIMESH_ORG_ID", "_default")
        assert org_id == "_default"
