"""Coder agent for AI Mesh — handles coding tasks via Claude Code."""

import structlog

from aimesh.agents.base import BaseAgent
from aimesh.agents.executor import BaseExecutor
from aimesh.core.bus import AbstractMessageBus
from aimesh.core.message import MeshMessage, MessageType
from aimesh.core.task import Task, TaskState
from aimesh.tasks.tracker import TaskTracker

logger = structlog.get_logger("coder")

CODER_PROMPT = """You are a coding agent. Complete the following task:

TASK: {task_description}

CONTEXT:
{context}

WORKSPACE: {workspace}
BRANCH: {branch}

Instructions:
1. Create a git branch named '{branch}' if it doesn't exist
2. Implement the requested changes
3. Commit your work with a descriptive message
4. Report what you did

Respond with a summary of what was implemented and any files changed.
"""


class CoderAgent(BaseAgent):
    """Agent that handles coding tasks using Claude Code CLI."""

    def __init__(
        self,
        agent_id: str,
        bus: AbstractMessageBus,
        executor: BaseExecutor,
        tracker: TaskTracker,
        workspace_path: str = "./workspace",
    ) -> None:
        super().__init__(
            agent_id=agent_id,
            agent_type="coder",
            bus=bus,
            capabilities=["code", "implement", "fix", "refactor"],
        )
        self.executor = executor
        self.tracker = tracker
        self.workspace_path = workspace_path

    async def handle_message(self, message: MeshMessage) -> None:
        """Handle incoming messages — primarily TASK_ASSIGN."""
        if message.msg_type == MessageType.TASK_ASSIGN:
            await self._handle_task(message)
        else:
            logger.debug("coder_ignored_message", msg_type=message.msg_type.value)

    async def _handle_task(self, message: MeshMessage) -> None:
        """Execute a coding task."""
        task_id = message.task_id or "unknown"
        branch = f"task/{task_id}"

        # Update task state
        task = self.tracker.get(task_id)
        if task and task.state == TaskState.ASSIGNED:
            task.transition(TaskState.IN_PROGRESS)

        # Send status update: starting
        await self.send(
            recipient="pm",
            msg_type=MessageType.TASK_ACCEPT,
            content=f"Accepted coding task: {message.metadata.get('title', task_id)}",
            task_id=task_id,
        )

        await self.send(
            recipient="broadcast",
            msg_type=MessageType.STATUS_UPDATE,
            content=f"Working on: {message.metadata.get('title', 'coding task')}",
            task_id=task_id,
        )

        # Build prompt and execute
        prompt = CODER_PROMPT.format(
            task_description=message.content,
            context=message.metadata.get("context", "No prior context."),
            workspace=self.workspace_path,
            branch=branch,
        )

        result = await self.executor.execute(prompt)

        if result.success:
            # Report success
            await self.send(
                recipient="pm",
                msg_type=MessageType.RESULT,
                content=result.content,
                task_id=task_id,
                metadata={
                    "branch": branch,
                    "cost_usd": result.cost_usd,
                    "agent_id": self.agent_id,
                },
            )
            logger.info("coder_task_done", task_id=task_id, branch=branch,
                        cost=result.cost_usd)
        else:
            # Report failure
            if task:
                task.retry_count += 1
                if task.retry_count >= 3:
                    task.transition(TaskState.FAILED)
                    task.error = result.error

            await self.send(
                recipient="pm",
                msg_type=MessageType.STATUS_UPDATE,
                content=f"Failed: {result.error}",
                task_id=task_id,
                metadata={"error": result.error, "retry_count": task.retry_count if task else 0},
            )
            logger.error("coder_task_failed", task_id=task_id, error=result.error)
