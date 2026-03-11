"""Tests for CLI setup wizard (Phase 1)."""

from unittest.mock import AsyncMock, MagicMock, patch

from aimesh.cli.wizard import (
    CLISetupWizard, TeamSetup, _sanitize_org_id,
    _detect_cli, _detect_available_engines,
)
from aimesh.config import AgentEntry, OrgConfig


def test_sanitize_org_id_basic():
    """Org ID is lowercased with special chars removed."""
    assert _sanitize_org_id("My Team") == "my-team"
    assert _sanitize_org_id("Team_One") == "team-one"
    assert _sanitize_org_id("team!@#123") == "team123"
    assert _sanitize_org_id("한국팀") == "한국팀"


def test_sanitize_org_id_empty():
    """Empty string produces empty org_id."""
    assert _sanitize_org_id("") == ""


def test_team_setup_dataclass():
    """TeamSetup has all required fields."""
    team = TeamSetup(
        name="Test Team",
        purpose="Build great things",
        bot_token="123:ABC",
        group_chat_id=-100123,
        org_id="test-team",
    )
    assert team.name == "Test Team"
    assert team.purpose == "Build great things"
    assert team.bot_token == "123:ABC"
    assert team.group_chat_id == -100123
    assert team.org_id == "test-team"


def test_generate_org_config_matches_telegram_wizard_format():
    """Generated OrgConfig has same structure as Telegram wizard."""
    wizard = CLISetupWizard()
    team = TeamSetup(
        name="Test", purpose="Testing", bot_token="tok",
        group_chat_id=-100, org_id="test",
    )
    config = wizard._generate_org_config(team)

    assert isinstance(config, OrgConfig)
    assert config.org_id == "test"
    assert config.org_name == "Test"
    assert config.telegram.group_chat_id == -100
    assert len(config.agents) == 2  # coder + researcher

    # Verify agent entries match Telegram wizard format
    coder = config.agents[0]
    assert isinstance(coder, AgentEntry)
    assert coder.id == "coder-1"
    assert coder.type == "coder"
    assert coder.soul_file == "souls/coder.md"
    assert "code" in coder.capabilities

    researcher = config.agents[1]
    assert researcher.id == "researcher-2"
    assert researcher.type == "researcher"
    assert "research" in researcher.capabilities


def test_generate_env_file_single_team():
    """Single-team .env has required keys."""
    wizard = CLISetupWizard()
    teams = [TeamSetup(
        name="T1", purpose="P1", bot_token="tok1",
        group_chat_id=-100, org_id="t1",
    )]
    env = wizard._generate_env_file(teams, "sk-key-123")

    assert "ANTHROPIC_API_KEY=sk-key-123" in env
    assert "TELEGRAM_BOT_TOKEN=tok1" in env
    assert "TELEGRAM_GROUP_CHAT_ID=-100" in env
    assert "AIMESH_ORG_ID=t1" in env


def test_generate_env_file_multi_team():
    """Multi-team .env has team IDs."""
    wizard = CLISetupWizard()
    teams = [
        TeamSetup(name="A", purpose="P", bot_token="tok1", group_chat_id=-1, org_id="a"),
        TeamSetup(name="B", purpose="P", bot_token="tok2", group_chat_id=-2, org_id="b"),
    ]
    env = wizard._generate_env_file(teams, "sk-key")

    assert "AIMESH_TEAM_IDS=a,b" in env
    assert "TELEGRAM_BOT_TOKEN=tok1" in env  # First team as default


def test_generate_and_save_config_creates_files(tmp_path):
    """Config and soul files are created on disk."""
    wizard = CLISetupWizard()
    team = TeamSetup(
        name="Test", purpose="We build cool stuff",
        bot_token="tok", group_chat_id=-100, org_id="test",
    )

    with patch("aimesh.cli.wizard.ORGS_DIR", tmp_path):
        with patch("aimesh.cli.wizard.save_org_config") as mock_save:
            wizard._generate_and_save_config(team)

            # save_org_config was called
            mock_save.assert_called_once()
            config_arg = mock_save.call_args[0][0]
            assert isinstance(config_arg, OrgConfig)
            assert config_arg.org_id == "test"

        # soul.md written
        soul_path = tmp_path / "test" / "soul.md"
        assert soul_path.exists()
        assert soul_path.read_text() == "We build cool stuff"

        # Agent soul files created
        coder_soul = tmp_path / "test" / "souls" / "coder.md"
        assert coder_soul.exists()
        assert "coder" in coder_soul.read_text()


def test_multi_team_generates_separate_dirs(tmp_path):
    """Each team gets its own org directory."""
    wizard = CLISetupWizard()
    teams = [
        TeamSetup(name="A", purpose="P1", bot_token="t1", group_chat_id=-1, org_id="a"),
        TeamSetup(name="B", purpose="P2", bot_token="t2", group_chat_id=-2, org_id="b"),
    ]

    with patch("aimesh.cli.wizard.ORGS_DIR", tmp_path):
        with patch("aimesh.cli.wizard.save_org_config"):
            for team in teams:
                wizard._generate_and_save_config(team)

    assert (tmp_path / "a" / "soul.md").exists()
    assert (tmp_path / "b" / "soul.md").exists()
    assert (tmp_path / "a" / "soul.md").read_text() == "P1"
    assert (tmp_path / "b" / "soul.md").read_text() == "P2"


async def test_validate_bot_token_success():
    """Valid bot token returns (True, bot_info)."""
    wizard = CLISetupWizard()

    mock_bot = AsyncMock()
    mock_me = MagicMock()
    mock_me.username = "test_bot"
    mock_bot.get_me.return_value = mock_me

    with patch("telegram.Bot", return_value=mock_bot):
        valid, info = await wizard._validate_bot_token("123:ABC")
        assert valid is True
        assert "test_bot" in info


async def test_validate_bot_token_failure():
    """Invalid bot token returns (False, error_msg)."""
    wizard = CLISetupWizard()

    with patch("telegram.Bot") as mock_cls:
        mock_bot = AsyncMock()
        mock_bot.get_me.side_effect = Exception("Unauthorized")
        mock_cls.return_value = mock_bot

        valid, info = await wizard._validate_bot_token("invalid")
        assert valid is False
        assert "Unauthorized" in info


async def test_validate_group_access_success():
    """Valid group chat returns True."""
    wizard = CLISetupWizard()

    with patch("telegram.Bot") as mock_cls:
        mock_bot = AsyncMock()
        mock_bot.send_message.return_value = MagicMock()
        mock_cls.return_value = mock_bot

        result = await wizard._validate_group_access("tok", -100)
        assert result is True


async def test_validate_group_access_failure():
    """Invalid group chat returns False."""
    wizard = CLISetupWizard()

    with patch("telegram.Bot") as mock_cls:
        mock_bot = AsyncMock()
        mock_bot.send_message.side_effect = Exception("Chat not found")
        mock_cls.return_value = mock_bot

        result = await wizard._validate_group_access("tok", -999)
        assert result is False


def test_ask_team_count_default():
    """Empty input defaults to 1 team."""
    wizard = CLISetupWizard(input_fn=lambda _: "")
    count = wizard._ask_team_count()
    assert count == 1


def test_ask_team_count_valid():
    """Valid numeric input returns that count."""
    wizard = CLISetupWizard(input_fn=lambda _: "3")
    count = wizard._ask_team_count()
    assert count == 3


# --- Engine detection and selection tests ---


def test_detect_cli_finds_python():
    """_detect_cli finds python3 which should always be available."""
    assert _detect_cli("python3") is True


def test_detect_cli_missing_command():
    """_detect_cli returns False for nonexistent command."""
    assert _detect_cli("nonexistent-cli-tool-xyz") is False


def test_detect_available_engines():
    """_detect_available_engines returns dict with all engine keys."""
    result = _detect_available_engines()
    assert "claude_code" in result
    assert "codex" in result
    assert "gemini" in result
    assert "anthropic" in result
    assert result["anthropic"] is True  # Always available


def test_team_setup_engine_default():
    """TeamSetup defaults to claude_code engine."""
    team = TeamSetup(
        name="T", purpose="P", bot_token="tok",
        group_chat_id=-100, org_id="t",
    )
    assert team.engine == "claude_code"


def test_team_setup_engine_codex():
    """TeamSetup accepts codex engine."""
    team = TeamSetup(
        name="T", purpose="P", bot_token="tok",
        group_chat_id=-100, org_id="t", engine="codex",
    )
    assert team.engine == "codex"


def test_generate_org_config_with_engine():
    """Generated OrgConfig respects team engine selection."""
    wizard = CLISetupWizard()
    team = TeamSetup(
        name="Test", purpose="Testing", bot_token="tok",
        group_chat_id=-100, org_id="test", engine="codex",
    )
    config = wizard._generate_org_config(team)
    assert config.pm.engine == "codex"


def test_generate_env_file_no_api_key():
    """CLI engine skips ANTHROPIC_API_KEY in .env."""
    wizard = CLISetupWizard()
    teams = [TeamSetup(
        name="T1", purpose="P1", bot_token="tok1",
        group_chat_id=-100, org_id="t1", engine="claude_code",
    )]
    env = wizard._generate_env_file(teams, "")

    assert "ANTHROPIC_API_KEY" not in env
    assert "TELEGRAM_BOT_TOKEN=tok1" in env


def test_generate_env_file_with_api_key():
    """Anthropic engine includes ANTHROPIC_API_KEY in .env."""
    wizard = CLISetupWizard()
    teams = [TeamSetup(
        name="T1", purpose="P1", bot_token="tok1",
        group_chat_id=-100, org_id="t1", engine="anthropic",
    )]
    env = wizard._generate_env_file(teams, "sk-key-123")

    assert "ANTHROPIC_API_KEY=sk-key-123" in env


def test_ask_engine_default_selection():
    """Empty input selects default engine."""
    wizard = CLISetupWizard(input_fn=lambda _: "")
    with patch("aimesh.cli.wizard._detect_available_engines", return_value={
        "claude_code": True, "codex": False, "gemini": False, "anthropic": True,
    }):
        engine = wizard._ask_engine()
    assert engine == "claude_code"


def test_ask_engine_codex_selection():
    """Input '2' selects codex engine."""
    wizard = CLISetupWizard(input_fn=lambda _: "2")
    with patch("aimesh.cli.wizard._detect_available_engines", return_value={
        "claude_code": True, "codex": True, "gemini": False, "anthropic": True,
    }):
        engine = wizard._ask_engine()
    assert engine == "codex"
