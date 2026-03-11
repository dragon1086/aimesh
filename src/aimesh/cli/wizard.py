"""CLI setup wizard for AI Mesh first-run experience.

Guides users from zero to working system via interactive terminal prompts.
Produces the same OrgConfig format as the Telegram SetupWizard.
"""

import asyncio
import getpass
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import structlog

from aimesh.config import (
    AgentEntry,
    OrgConfig,
    ORGS_DIR,
    PMConfig,
    TelegramOrgConfig,
    save_org_config,
)
from aimesh.telegram.setup_wizard import SetupWizard

logger = structlog.get_logger("cli_wizard")

# Default agent roster (same as Telegram wizard)
DEFAULT_AGENT_TYPES = ["coder", "researcher"]

# Engine display info
ENGINE_INFO = {
    "claude_code": {
        "name": "Claude Code",
        "command": "claude",
        "install": "npm install -g @anthropic-ai/claude-code",
        "auth_hint": "Run 'claude' once to authenticate via OAuth",
    },
    "codex": {
        "name": "Codex",
        "command": "codex",
        "install": "npm install -g @openai/codex",
        "auth_hint": "Run 'codex' once to authenticate",
    },
    "gemini": {
        "name": "Gemini CLI",
        "command": "gemini-cli",
        "install": "See https://github.com/google-gemini/gemini-cli",
        "auth_hint": "Run 'gemini-cli' once to authenticate",
    },
    "anthropic": {
        "name": "Anthropic API (legacy)",
        "command": None,
        "install": None,
        "auth_hint": "Requires ANTHROPIC_API_KEY",
    },
}


@dataclass
class TeamSetup:
    """Configuration collected for one team during setup."""

    name: str
    purpose: str  # Written to orgs/{org_id}/soul.md
    bot_token: str
    group_chat_id: int
    org_id: str  # Derived from name
    engine: str = "claude_code"


def _sanitize_org_id(name: str) -> str:
    """Convert team name to a valid org_id (same logic as Telegram wizard)."""
    org_id = name.lower().replace(" ", "-").replace("_", "-")
    return "".join(c for c in org_id if c.isalnum() or c == "-")


def _detect_cli(command: str) -> bool:
    """Check if a CLI command is available on PATH."""
    return shutil.which(command) is not None


def _detect_available_engines() -> dict[str, bool]:
    """Detect which CLI engines are installed locally."""
    return {
        "claude_code": _detect_cli("claude"),
        "codex": _detect_cli("codex"),
        "gemini": _detect_cli("gemini-cli"),
        "anthropic": True,  # Always available (just needs API key)
    }


class CLISetupWizard:
    """Interactive terminal wizard for first-run AI Mesh setup."""

    def __init__(self, input_fn=None, getpass_fn=None):
        """Initialize wizard with optional input function overrides for testing."""
        self._input = input_fn or input
        self._getpass = getpass_fn or getpass.getpass

    async def run(self) -> list[TeamSetup]:
        """Run the full setup wizard. Returns list of configured teams."""
        self._print_banner()

        # Preflight checks
        await self._run_preflight()

        # Step 1: Engine selection
        engine = self._ask_engine()

        # Step 2: API key (only for anthropic engine)
        api_key = ""
        if engine == "anthropic":
            api_key = await self._ask_api_key()
        else:
            info = ENGINE_INFO[engine]
            print(f"\nUsing {info['name']} engine — no API key needed.")
            print(f"Authentication: {info['auth_hint']}\n")

        # Step 3: Team count
        num_teams = self._ask_team_count()

        # Collect team configurations
        teams: list[TeamSetup] = []
        for i in range(num_teams):
            team = await self._ask_team(i + 1, num_teams, engine)
            teams.append(team)

        # Generate configs and .env
        for team in teams:
            self._generate_and_save_config(team)

        env_content = self._generate_env_file(teams, api_key)
        Path(".env").write_text(env_content, encoding="utf-8")

        self._print_completion(teams)
        return teams

    def _print_banner(self) -> None:
        print("\n" + "=" * 60)
        print("  AI Mesh Setup Wizard")
        print("  First-run configuration")
        print("=" * 60 + "\n")

    async def _run_preflight(self) -> None:
        """Run preflight checks and block on critical failures."""
        from aimesh.telegram.preflight import run_preflight_checks

        print("Running pre-flight checks...")
        result = await run_preflight_checks()
        print(result.format_display())

        if not result.critical_passed:
            print("\nCritical checks failed. Please fix the issues above and try again.")
            sys.exit(1)

        print("Pre-flight checks passed!\n")

    def _ask_engine(self) -> str:
        """Ask which PM engine to use, with local CLI detection."""
        print("Step 1: PM Engine Selection")
        print("-" * 40)

        available = _detect_available_engines()

        print("\nDetected CLI tools:")
        for key, installed in available.items():
            if key == "anthropic":
                continue
            info = ENGINE_INFO[key]
            status = "FOUND" if installed else "not found"
            print(f"  {info['name']:15s} ({info['command']}) ... {status}")

        print("\nAvailable engines:")
        engines = ["claude_code", "codex", "gemini", "anthropic"]
        for i, key in enumerate(engines, 1):
            info = ENGINE_INFO[key]
            marker = " *" if available.get(key) and key != "anthropic" else ""
            print(f"  {i}. {info['name']}{marker}")

        # Default to first available CLI engine
        default_engine = "claude_code"
        for key in ["claude_code", "codex", "gemini"]:
            if available[key]:
                default_engine = key
                break

        default_idx = engines.index(default_engine) + 1

        while True:
            choice = self._input(f"\nChoose engine (1-4, default: {default_idx}): ").strip()
            if not choice:
                engine = default_engine
                break
            try:
                idx = int(choice)
                if 1 <= idx <= 4:
                    engine = engines[idx - 1]
                    break
                print("Please enter 1-4.")
            except ValueError:
                print("Please enter a valid number.")

        info = ENGINE_INFO[engine]

        # Warn if CLI not found
        if engine != "anthropic" and not available[engine]:
            print(f"\nWarning: '{info['command']}' not found on PATH.")
            print(f"Install: {info['install']}")
            proceed = self._input("Continue anyway? (y/N): ").strip().lower()
            if proceed != "y":
                sys.exit(1)

        print(f"\nEngine: {info['name']}\n")
        return engine

    async def _ask_api_key(self) -> str:
        """Ask for and validate the Anthropic API key."""
        print("Step 2: Anthropic API Key")
        print("-" * 40)

        api_key = self._getpass("Enter your Anthropic API key: ").strip()
        if not api_key:
            print("API key is required for anthropic engine.")
            sys.exit(1)

        # Basic format validation
        if not api_key.startswith("sk-"):
            print("Warning: API key doesn't start with 'sk-'. Proceeding anyway.")

        print("API key saved.\n")
        return api_key

    def _ask_team_count(self) -> int:
        """Ask how many teams to configure."""
        step = "Step 2" if True else "Step 3"  # Dynamic based on engine
        print(f"{step}: Team Configuration")
        print("-" * 40)

        while True:
            count_str = self._input("How many teams? (1-5, default: 1): ").strip()
            if not count_str:
                return 1
            try:
                count = int(count_str)
                if 1 <= count <= 5:
                    return count
                print("Please enter a number between 1 and 5.")
            except ValueError:
                print("Please enter a valid number.")

    async def _ask_team(self, index: int, total: int, engine: str) -> TeamSetup:
        """Collect configuration for one team."""
        prefix = f"Team {index}/{total}" if total > 1 else "Team"
        print(f"\n{prefix} Setup")
        print("-" * 40)

        # Team name
        name = self._input("Team name: ").strip()
        if not name:
            name = f"team-{index}"

        org_id = _sanitize_org_id(name)

        # Team purpose (becomes soul.md)
        purpose = self._input("Team purpose/direction (e.g., 'Build a SaaS product'): ").strip()
        if not purpose:
            purpose = f"Team {name} - AI development team"

        # Bot token
        print("\nGet a bot token from @BotFather on Telegram.")
        bot_token = self._getpass("Bot token: ").strip()
        if not bot_token:
            print("Bot token is required.")
            sys.exit(1)

        # Validate bot token
        valid, bot_info = await self._validate_bot_token(bot_token)
        if not valid:
            print(f"Invalid bot token: {bot_info}")
            sys.exit(1)
        print(f"Bot validated: {bot_info}")

        # Group chat ID
        group_chat_id = await self._ask_group_chat_id(bot_token)

        return TeamSetup(
            name=name,
            purpose=purpose,
            bot_token=bot_token,
            group_chat_id=group_chat_id,
            org_id=org_id,
            engine=engine,
        )

    async def _validate_bot_token(self, token: str) -> tuple[bool, str]:
        """Validate bot token against Telegram API."""
        try:
            from telegram import Bot

            bot = Bot(token=token)
            me = await bot.get_me()
            return True, f"@{me.username}"
        except Exception as e:
            return False, str(e)

    async def _ask_group_chat_id(self, bot_token: str) -> int:
        """Ask for group chat ID with auto-detect option."""
        print("\nGroup Chat ID:")
        print("  1. Enter manually")
        print("  2. Auto-detect (bot sends test message)")

        choice = self._input("Choose (1/2, default: 1): ").strip()

        if choice == "2":
            detected = await self._detect_group_chat(bot_token)
            if detected:
                print(f"Detected group chat ID: {detected}")
                return detected
            print("Auto-detect failed. Please enter manually.")

        while True:
            chat_id_str = self._input("Group chat ID (negative number): ").strip()
            try:
                chat_id = int(chat_id_str)
                # Validate access
                if await self._validate_group_access(bot_token, chat_id):
                    print("Group chat validated!")
                    return chat_id
                print("Bot cannot send to this chat. Check the bot is added to the group.")
            except ValueError:
                print("Please enter a valid number.")

    async def _detect_group_chat(self, token: str) -> int | None:
        """Try to auto-detect group chat by sending test message."""
        try:
            from telegram import Bot

            bot = Bot(token=token)
            # getUpdates to find recent group chats the bot was added to
            updates = await bot.get_updates(limit=100)
            chat_ids: set[int] = set()
            for update in updates:
                if update.message and update.message.chat.type in ("group", "supergroup"):
                    chat_ids.add(update.message.chat_id)

            if not chat_ids:
                return None

            if len(chat_ids) == 1:
                return chat_ids.pop()

            # Multiple chats — ask user to pick
            print("Found multiple group chats:")
            chat_list = sorted(chat_ids)
            for i, cid in enumerate(chat_list, 1):
                print(f"  {i}. {cid}")

            choice = self._input("Pick a chat (number): ").strip()
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(chat_list):
                    return chat_list[idx]
            except ValueError:
                pass

            return None
        except Exception:
            return None

    async def _validate_group_access(self, token: str, chat_id: int) -> bool:
        """Validate bot can send to the given group chat."""
        try:
            from telegram import Bot

            bot = Bot(token=token)
            msg = await bot.send_message(
                chat_id=chat_id,
                text="AI Mesh setup test - this message confirms bot access.",
            )
            return msg is not None
        except Exception:
            return False

    def _generate_org_config(self, team: TeamSetup) -> OrgConfig:
        """Generate OrgConfig from TeamSetup (same format as Telegram wizard)."""
        agent_entries = []
        for i, agent_type in enumerate(DEFAULT_AGENT_TYPES, 1):
            agent_entries.append(
                AgentEntry(
                    id=f"{agent_type}-{i}",
                    type=agent_type,
                    soul_file=f"souls/{agent_type}.md",
                    capabilities=SetupWizard._default_capabilities(agent_type),
                    model="sonnet",
                    max_budget_usd=5.0,
                )
            )

        return OrgConfig(
            org_id=team.org_id,
            org_name=team.name,
            telegram=TelegramOrgConfig(group_chat_id=team.group_chat_id),
            agents=agent_entries,
            pm=PMConfig(engine=team.engine),
            workspace_path="./workspace",
        )

    def _generate_and_save_config(self, team: TeamSetup) -> None:
        """Generate and save OrgConfig + soul.md for a team."""
        config = self._generate_org_config(team)
        save_org_config(config)

        # Write soul.md from team purpose
        org_dir = ORGS_DIR / team.org_id
        org_dir.mkdir(parents=True, exist_ok=True)
        soul_path = org_dir / "soul.md"
        soul_path.write_text(team.purpose, encoding="utf-8")

        # Create default soul files for agents
        soul_dir = org_dir / "souls"
        soul_dir.mkdir(parents=True, exist_ok=True)
        for agent_type in DEFAULT_AGENT_TYPES:
            agent_soul = soul_dir / f"{agent_type}.md"
            if not agent_soul.exists():
                agent_soul.write_text(
                    f"You are a {agent_type} agent for team '{team.name}'.\n"
                    f"Team direction: {team.purpose}\n",
                    encoding="utf-8",
                )

        logger.info(
            "org_config_saved",
            org_id=team.org_id,
            config_path=str(org_dir / "config.yaml"),
        )

    def _generate_env_file(self, teams: list[TeamSetup], api_key: str) -> str:
        """Generate .env file content."""
        lines = [
            "# AI Mesh Configuration",
            "# Generated by CLI Setup Wizard",
            "",
        ]

        # Only include API key if provided (anthropic engine)
        if api_key:
            lines.extend([
                "# Anthropic",
                f"ANTHROPIC_API_KEY={api_key}",
                "",
            ])

        if len(teams) == 1:
            team = teams[0]
            lines.extend([
                "# Telegram",
                f"TELEGRAM_BOT_TOKEN={team.bot_token}",
                f"TELEGRAM_GROUP_CHAT_ID={team.group_chat_id}",
                "",
                "# Organization",
                f"AIMESH_ORG_ID={team.org_id}",
            ])
        else:
            # Multi-team: first team is the default
            lines.extend([
                "# Telegram (first team as default)",
                f"TELEGRAM_BOT_TOKEN={teams[0].bot_token}",
                f"TELEGRAM_GROUP_CHAT_ID={teams[0].group_chat_id}",
                "",
                "# Multi-team org IDs",
                f"AIMESH_ORG_ID={teams[0].org_id}",
                f"AIMESH_TEAM_IDS={','.join(t.org_id for t in teams)}",
            ])

        lines.append("")
        return "\n".join(lines)

    def _print_completion(self, teams: list[TeamSetup]) -> None:
        """Print setup completion message."""
        engine_name = ENGINE_INFO[teams[0].engine]["name"]
        print("\n" + "=" * 60)
        print("  Setup Complete!")
        print("=" * 60)
        print()
        print(f"  Engine: {engine_name}")
        for team in teams:
            print(f"  Team: {team.name}")
            print(f"    Config: orgs/{team.org_id}/config.yaml")
            print(f"    Soul:   orgs/{team.org_id}/soul.md")
        print()
        print("  .env file generated.")
        print()
        print("  Run 'python -m aimesh.main' to start.")
        print()


def main() -> None:
    """CLI entry point for setup wizard."""
    wizard = CLISetupWizard()
    asyncio.run(wizard.run())


if __name__ == "__main__":
    main()
