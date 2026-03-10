"""Researcher agent for AI Mesh — handles analysis tasks via Claude Code."""

import structlog

from aimesh.agents.base import BaseAgent
from aimesh.agents.executor import BaseExecutor
from aimesh.core.bus import AbstractMessageBus
from aimesh.core.message import MeshMessage, MessageType
from aimesh.core.task import Task, TaskState
from aimesh.tasks.tracker import TaskTracker

logger = structlog.get_logger("researcher")

RESEARCH_PROMPT = """You are a research agent. Complete the following analysis task:

TASK: {task_description}

CONTEXT:
{context}

Instructions:
1. Analyze the topic thoroughly
2. Provide a structured analysis with findings
3. Include recommendations where appropriate

Respond with a comprehensive analysis.
"""


class ResearcherAgent(BaseAgent):
    """Agent that handles research and analysis tasks."""

    def __init__(
        self,
        agent_id: str,
        bus: AbstractMessageBus,
        executor: BaseExecutor,
        tracker: TaskTracker,
    ) -> None:
        super().__init__(
            agent_id=agent_id,
            agent_type="researcher",
            bus=bus,
            capabilities=["research", "analyze", "compare", "evaluate"],
        )
        self.executor = executor
        self.tracker = tracker

    async def handle_message(self, message: MeshMessage) -> None:
        """Handle incoming messages — primarily TASK_ASSIGN."""
        if message.msg_type == MessageType.TASK_ASSIGN:
            await self._handle_task(message)
        else:
            logger.debug("researcher_ignored_message", msg_type=message.msg_type.value)

    async def _handle_task(self, message: MeshMessage) -> None:
        """Execute a research/analysis task."""
        task_id = message.task_id or "unknown"

        # Update task state
        task = self.tracker.get(task_id)
        if task and task.state == TaskState.ASSIGNED:
            task.transition(TaskState.IN_PROGRESS)

        # Send status update: starting
        await self.send(
            recipient="pm",
            msg_type=MessageType.TASK_ACCEPT,
            content=f"Accepted research task: {message.metadata.get('title', task_id)}",
            task_id=task_id,
        )

        await self.send(
            recipient="broadcast",
            msg_type=MessageType.STATUS_UPDATE,
            content=f"Researching: {message.metadata.get('title', 'analysis task')}",
            task_id=task_id,
        )

        # Build prompt and execute
        prompt = RESEARCH_PROMPT.format(
            task_description=message.content,
            context=message.metadata.get("context", "No prior context."),
        )

        result = await self.executor.execute(prompt)

        if result.success:
            await self.send(
                recipient="pm",
                msg_type=MessageType.RESULT,
                content=result.content,
                task_id=task_id,
                metadata={
                    "cost_usd": result.cost_usd,
                    "agent_id": self.agent_id,
                },
            )
            logger.info("researcher_task_done", task_id=task_id, cost=result.cost_usd)
        else:
            if task:
                task.retry_count += 1
                if task.retry_count >= 3:
                    task.transition(TaskState.FAILED)
                    task.error = result.error

            await self.send(
                recipient="pm",
                msg_type=MessageType.STATUS_UPDATE,
                content=f"Research failed: {result.error}",
                task_id=task_id,
                metadata={"error": result.error, "retry_count": task.retry_count if task else 0},
            )
            logger.error("researcher_task_failed", task_id=task_id, error=result.error)
