"""Telegram setup wizard for AI Mesh org onboarding."""

import structlog
from telegram import Update
from telegram.ext import (
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from aimesh.config import AgentEntry, OrgConfig, PMConfig, TelegramOrgConfig, save_org_config
from aimesh.telegram.preflight import run_preflight_checks

logger = structlog.get_logger("setup_wizard")

# Conversation states
PREFLIGHT, ORG_NAME, GROUP_CHAT_ID, WORKSPACE_PATH, AGENT_TYPES, CONFIRM = range(6)


class SetupWizard:
    """Interactive setup wizard via Telegram DM."""

    def __init__(self, admin_ids: list[int] | None = None) -> None:
        self.admin_ids = admin_ids or []

    def _is_authorized(self, user_id: int) -> bool:
        """Check if user is authorized (empty admin_ids = anyone)."""
        return not self.admin_ids or user_id in self.admin_ids

    def get_handler(self) -> ConversationHandler:
        """Return the ConversationHandler for registration."""
        return ConversationHandler(
            entry_points=[CommandHandler("setup", self.start_setup)],
            states={
                PREFLIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.after_preflight)],
                ORG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_org_name)],
                GROUP_CHAT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_group_chat_id)],
                WORKSPACE_PATH: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_workspace_path)],
                AGENT_TYPES: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_agent_types)],
                CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_confirmation)],
            },
            fallbacks=[CommandHandler("setup_cancel", self.cancel)],
        )

    async def start_setup(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle /setup command — start the wizard."""
        if not update.effective_chat or not update.effective_user:
            return ConversationHandler.END

        # Only allow in DM (private chat)
        if update.effective_chat.type != "private":
            await update.message.reply_text(
                "Please use this command in a private message."
            )
            return ConversationHandler.END

        # Auth check
        if not self._is_authorized(update.effective_user.id):
            await update.message.reply_text("You are not authorized to run setup.")
            return ConversationHandler.END

        # Run preflight checks
        await update.message.reply_text("Running pre-flight checks...")
        result = await run_preflight_checks()
        await update.message.reply_text(result.format_display())

        if not result.critical_passed:
            await update.message.reply_text(
                "Critical checks failed. Please fix the issues above and try /setup again."
            )
            return ConversationHandler.END

        await update.message.reply_text(
            "Pre-flight checks passed! Let's set up your organization.\n\n"
            "What would you like to name your organization?"
        )
        return ORG_NAME

    async def after_preflight(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Fallback handler for PREFLIGHT state (shouldn't normally be reached)."""
        return ORG_NAME

    async def receive_org_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Receive organization name."""
        org_name = update.message.text.strip()
        if not org_name:
            await update.message.reply_text("Please enter a valid organization name:")
            return ORG_NAME

        # Generate org_id from name
        org_id = org_name.lower().replace(" ", "-").replace("_", "-")
        org_id = "".join(c for c in org_id if c.isalnum() or c == "-")

        context.user_data["org_name"] = org_name
        context.user_data["org_id"] = org_id

        await update.message.reply_text(
            f"Organization: {org_name} (ID: {org_id})\n\n"
            "Now, please add the bot to your Telegram group and send me the group chat ID.\n"
            "Tip: Forward any message from the group, or use @RawDataBot to find the chat ID."
        )
        return GROUP_CHAT_ID

    async def receive_group_chat_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Receive group chat ID."""
        try:
            chat_id = int(update.message.text.strip())
        except ValueError:
            await update.message.reply_text(
                "Invalid chat ID. Please enter a number (usually negative for groups):"
            )
            return GROUP_CHAT_ID

        context.user_data["group_chat_id"] = chat_id

        await update.message.reply_text(
            f"Group chat ID: {chat_id}\n\n"
            "Enter the workspace path for code operations (default: ./workspace):"
        )
        return WORKSPACE_PATH

    async def receive_workspace_path(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Receive workspace path."""
        workspace = update.message.text.strip() or "./workspace"
        context.user_data["workspace_path"] = workspace

        await update.message.reply_text(
            f"Workspace: {workspace}\n\n"
            "Which agent types should be in the default roster?\n"
            "Options: coder, researcher, both (default: both)"
        )
        return AGENT_TYPES

    async def receive_agent_types(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Receive agent type selections."""
        choice = update.message.text.strip().lower()

        agents = []
        if choice in ("coder", "both", ""):
            agents.append("coder")
        if choice in ("researcher", "both", ""):
            agents.append("researcher")
        if not agents:
            agents = ["coder", "researcher"]  # Default to both

        context.user_data["agent_types"] = agents

        # Show confirmation
        data = context.user_data
        summary = (
            f"*Setup Summary:*\n"
            f"Organization: {data['org_name']}\n"
            f"Org ID: {data['org_id']}\n"
            f"Group Chat ID: {data['group_chat_id']}\n"
            f"Workspace: {data['workspace_path']}\n"
            f"Agents: {', '.join(agents)}\n\n"
            f"Type 'confirm' to save, or 'cancel' to abort."
        )
        await update.message.reply_text(summary)
        return CONFIRM

    async def receive_confirmation(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle confirmation."""
        text = update.message.text.strip().lower()

        if text != "confirm":
            await update.message.reply_text("Setup cancelled.")
            return ConversationHandler.END

        data = context.user_data

        # Build agent entries
        agent_entries = []
        for i, agent_type in enumerate(data["agent_types"], 1):
            agent_entries.append(AgentEntry(
                id=f"{agent_type}-{i}",
                type=agent_type,
                soul_file=f"souls/{agent_type}.md",
                capabilities=self._default_capabilities(agent_type),
                model="sonnet",
                max_budget_usd=5.0,
            ))

        # Build and save config
        config = OrgConfig(
            org_id=data["org_id"],
            org_name=data["org_name"],
            telegram=TelegramOrgConfig(
                group_chat_id=data["group_chat_id"],
                admin_user_ids=[update.effective_user.id] if update.effective_user else [],
            ),
            agents=agent_entries,
            pm=PMConfig(),
            workspace_path=data["workspace_path"],
        )

        try:
            save_org_config(config)

            # Create soul files from defaults
            from pathlib import Path
            from aimesh.config import ORGS_DIR
            org_dir = ORGS_DIR / config.org_id

            # Copy default soul files
            default_dir = ORGS_DIR / "_default"
            soul_dir = org_dir / "souls"
            soul_dir.mkdir(parents=True, exist_ok=True)

            # Org-level soul.md
            default_soul = default_dir / "soul.md"
            if default_soul.exists():
                (org_dir / "soul.md").write_text(default_soul.read_text())

            # Per-agent souls
            for agent_type in data["agent_types"]:
                default_agent_soul = default_dir / "souls" / f"{agent_type}.md"
                if default_agent_soul.exists():
                    (soul_dir / f"{agent_type}.md").write_text(
                        default_agent_soul.read_text()
                    )

            await update.message.reply_text(
                f"Setup complete! Organization '{config.org_name}' has been configured.\n\n"
                f"Config saved to: orgs/{config.org_id}/config.yaml\n"
                f"Soul files created in: orgs/{config.org_id}/souls/\n\n"
                f"Restart the bot to apply changes."
            )
        except Exception as e:
            logger.error("setup_save_failed", error=str(e))
            await update.message.reply_text(f"Error saving configuration: {e}")

        return ConversationHandler.END

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle /setup_cancel."""
        await update.message.reply_text("Setup wizard cancelled.")
        return ConversationHandler.END

    @staticmethod
    def _default_capabilities(agent_type: str) -> list[str]:
        """Default capabilities for known agent types."""
        defaults = {
            "coder": ["code", "implement", "fix", "refactor"],
            "researcher": ["research", "analyze", "compare"],
        }
        return defaults.get(agent_type, [agent_type])
