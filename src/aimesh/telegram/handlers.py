"""Telegram command handlers for human interaction."""

import structlog

from aimesh.agents.pm import PMAgent
from aimesh.config import (
    AgentEntry, OrgConfig, ORGS_DIR,
    PMConfig, TelegramOrgConfig, save_org_config,
)
from aimesh.core.message import MeshMessage, MessageType
from aimesh.core.registry import AgentRegistry
from aimesh.tasks.tracker import TaskTracker
from aimesh.telegram.formatter import format_agents_list, format_status_summary

logger = structlog.get_logger("handlers")


class CommandHandlers:
    """Handles Telegram bot commands from human admins."""

    def __init__(
        self,
        pm: PMAgent,
        tracker: TaskTracker,
        registry: AgentRegistry,
        admin_ids: list[int] | None = None,
    ) -> None:
        self.pm = pm
        self.tracker = tracker
        self.registry = registry
        self.admin_ids = admin_ids or []

    def _is_admin(self, user_id: int) -> bool:
        """Check if user is an authorized admin."""
        if not self.admin_ids:
            return True  # No restrictions if no admin IDs configured
        return user_id in self.admin_ids

    async def handle_task(self, user_id: int, text: str) -> str:
        """Handle /task command — submit a new task."""
        if not self._is_admin(user_id):
            return "Unauthorized\\. Only admins can submit tasks\\."

        if not text.strip():
            return "Usage: /task <description>"

        # Send as a message to PM
        msg = MeshMessage(
            sender="human",
            recipient="pm",
            msg_type=MessageType.TASK_ASSIGN,
            content=text.strip(),
        )
        await self.pm.bus.publish(msg)

        logger.info("task_submitted", user_id=user_id, content_length=len(text))
        return f"Task submitted: {text[:100]}"

    async def handle_status(self, user_id: int) -> str:
        """Handle /status command — show active tasks."""
        tasks = self.tracker.get_active_tasks()
        task_dicts = [t.to_dict() for t in tasks]
        return format_status_summary(task_dicts)

    async def handle_agents(self, user_id: int) -> str:
        """Handle /agents command — show registered agents."""
        agents = self.registry.all_agents()
        agent_dicts = [
            {"agent_id": a.agent_id, "agent_type": a.agent_type, "status": a.status}
            for a in agents
        ]
        return format_agents_list(agent_dicts)

    async def handle_cancel(self, user_id: int, task_id: str) -> str:
        """Handle /cancel command — cancel a task."""
        if not self._is_admin(user_id):
            return "Unauthorized\\."

        task = self.tracker.get(task_id)
        if not task:
            return f"Task {task_id} not found\\."
        if task.is_terminal:
            return f"Task already in terminal state: {task.state.value}"

        try:
            task.transition(task.state.__class__["CANCELLED"])
        except (ValueError, KeyError):
            from aimesh.core.task import TaskState
            task.transition(TaskState.CANCELLED)

        return f"Task {task_id} cancelled\\."

    async def handle_approve(self, user_id: int, task_id: str) -> str:
        """Handle /approve command — approve a task result."""
        if not self._is_admin(user_id):
            return "Unauthorized\\."

        result = await self.pm.approve_task(task_id)
        if result:
            return f"Task {task_id} approved and marked DONE\\."
        return f"Cannot approve task {task_id}\\. Not in REVIEW state\\."

    async def handle_reject(self, user_id: int, task_id: str, feedback: str = "") -> str:
        """Handle /reject command — reject and request rework."""
        if not self._is_admin(user_id):
            return "Unauthorized\\."

        result = await self.pm.reject_task(task_id, feedback or "Please revise.")
        if result:
            return f"Task {task_id} rejected\\. Sent for rework\\."
        return f"Cannot reject task {task_id}\\. Not in REVIEW state\\."

    async def handle_addteam(
        self, user_id: int, text: str, chat_id: int, engine: str = ""
    ) -> str:
        """Handle /addteam command — create a new team from group chat.

        Usage: /addteam <name> [engine]
        Engine: claude_code (default), codex, gemini, anthropic
        """
        if not self._is_admin(user_id):
            return "Unauthorized\\. Only admins can add teams\\."

        parts = text.strip().split()
        if not parts:
            return (
                "Usage: /addteam <name> [engine]\n"
                "Engine: claude\\_code \\(default\\), codex, gemini, anthropic"
            )

        team_name = parts[0]
        engine_choice = parts[1] if len(parts) > 1 else (engine or "claude_code")

        valid_engines = {"claude_code", "codex", "gemini", "anthropic"}
        if engine_choice not in valid_engines:
            return f"Invalid engine: {engine_choice}\\. Choose from: {', '.join(sorted(valid_engines))}"

        # Sanitize org_id (ASCII only)
        org_id = team_name.lower().replace(" ", "-").replace("_", "-")
        org_id = "".join(c for c in org_id if c.isascii() and (c.isalnum() or c == "-"))
        if not org_id:
            return "Team name must contain at least one ASCII letter or number\\."

        # Check if org already exists
        org_dir = ORGS_DIR / org_id
        if (org_dir / "config.yaml").exists():
            return f"Team '{org_id}' already exists\\. Edit orgs/{org_id}/config\\.yaml to modify\\."

        # Default agent roster
        default_capabilities = {
            "coder": ["code", "implement", "fix", "refactor"],
            "researcher": ["research", "analyze", "compare"],
        }
        agent_entries = []
        for i, agent_type in enumerate(["coder", "researcher"], 1):
            agent_entries.append(AgentEntry(
                id=f"{agent_type}-{i}",
                type=agent_type,
                soul_file=f"souls/{agent_type}.md",
                capabilities=default_capabilities.get(agent_type, [agent_type]),
                model="sonnet",
                max_budget_usd=5.0,
            ))

        config = OrgConfig(
            org_id=org_id,
            org_name=team_name,
            telegram=TelegramOrgConfig(
                group_chat_id=chat_id,
                admin_user_ids=[user_id],
            ),
            agents=agent_entries,
            pm=PMConfig(engine=engine_choice),
            workspace_path="./workspace",
        )

        try:
            save_org_config(config)

            # Create default soul files
            soul_dir = org_dir / "souls"
            soul_dir.mkdir(parents=True, exist_ok=True)

            org_soul = org_dir / "soul.md"
            if not org_soul.exists():
                org_soul.write_text(
                    f"You are the PM for team '{team_name}'.\n",
                    encoding="utf-8",
                )

            for agent_type in ["coder", "researcher"]:
                agent_soul = soul_dir / f"{agent_type}.md"
                if not agent_soul.exists():
                    agent_soul.write_text(
                        f"You are a {agent_type} agent for team '{team_name}'.\n",
                        encoding="utf-8",
                    )

            logger.info("team_added", org_id=org_id, engine=engine_choice, user_id=user_id)
            return (
                f"Team '{team_name}' created\\!\n\n"
                f"Config: orgs/{org_id}/config\\.yaml\n"
                f"Engine: {engine_choice}\n"
                f"Agents: coder\\-1, researcher\\-1\n\n"
                f"Restart the bot to activate this team\\."
            )
        except Exception as e:
            logger.error("team_add_failed", error=str(e), org_id=org_id)
            return f"Error creating team: {str(e)}"
