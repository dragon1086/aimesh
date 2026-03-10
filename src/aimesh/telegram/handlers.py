"""Telegram command handlers for human interaction."""

import structlog

from aimesh.agents.pm import PMAgent
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
