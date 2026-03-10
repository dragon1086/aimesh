"""Dynamic worker agent for AI Mesh v2.

Replaces hardcoded CoderAgent/ResearcherAgent with a generic agent
that takes a soul_prompt at construction and uses it for all tasks.
"""

import structlog

from aimesh.agents.base import BaseAgent
from aimesh.agents.executor import BaseExecutor
from aimesh.core.bus import AbstractMessageBus
from aimesh.core.message import MeshMessage, MessageType
from aimesh.tasks.tracker import TaskTracker

logger = structlog.get_logger("dynamic_worker")


class DynamicWorker(BaseAgent):
    """Generic worker agent configured by soul_prompt at runtime.

    Unlike CoderAgent/ResearcherAgent, DynamicWorker's behavior is defined
    entirely by its soul_prompt (system prompt), not by its Python class.
    """

    MAX_RETRIES = 3

    def __init__(
        self,
        agent_id: str,
        agent_type: str,
        bus: AbstractMessageBus,
        executor: BaseExecutor,
        tracker: TaskTracker,
        soul_prompt: str,
        capabilities: list[str] | None = None,
        workspace_path: str | None = None,
    ) -> None:
        super().__init__(
            agent_id=agent_id,
            agent_type=agent_type,
            bus=bus,
            capabilities=capabilities or [],
        )
        self.executor = executor
        self.tracker = tracker
        self.soul_prompt = soul_prompt
        self.workspace_path = workspace_path
        self._retry_counts: dict[str, int] = {}
        self._active_task_id: str | None = None

    async def handle_message(self, message: MeshMessage) -> None:
        """Handle incoming messages."""
        if message.msg_type == MessageType.TASK_ASSIGN:
            await self._handle_task(message)
        elif message.msg_type == MessageType.COMPLETION_CHECK:
            await self._handle_completion_check(message)
        else:
            logger.debug(
                "worker_ignored_message",
                agent_id=self.agent_id,
                msg_type=message.msg_type.value,
            )

    async def _handle_task(self, message: MeshMessage) -> None:
        """Execute an assigned task using the soul_prompt as context."""
        task_id = message.task_id or "unknown"
        self._active_task_id = task_id

        # Send status update
        await self.send(
            recipient="broadcast",
            msg_type=MessageType.STATUS_UPDATE,
            content=f"Working on: {message.content[:100]}",
            task_id=task_id,
        )

        # Build the full prompt with soul context
        full_prompt = f"{self.soul_prompt}\n\n---\n\nTASK:\n{message.content}"

        try:
            result = await self.executor.execute(full_prompt)

            if result.success:
                self._retry_counts.pop(task_id, None)
                self._active_task_id = None
                await self.send(
                    recipient="pm",
                    msg_type=MessageType.RESULT,
                    content=result.content,
                    task_id=task_id,
                    metadata={
                        "agent_type": self.agent_type,
                        "cost_usd": result.cost_usd,
                        "tokens_used": result.input_tokens + result.output_tokens,
                    },
                )
                logger.info(
                    "task_completed",
                    agent_id=self.agent_id,
                    task_id=task_id,
                    cost=result.cost_usd,
                )
            else:
                await self._handle_failure(task_id, message, result.error or "Unknown error")

        except Exception as e:
            await self._handle_failure(task_id, message, str(e))
        finally:
            self._active_task_id = None

    async def _handle_failure(self, task_id: str, message: MeshMessage, error: str) -> None:
        """Handle task execution failure with retry logic."""
        retry_count = self._retry_counts.get(task_id, 0) + 1
        self._retry_counts[task_id] = retry_count

        if retry_count >= self.MAX_RETRIES:
            # Max retries exceeded — report failure
            await self.send(
                recipient="pm",
                msg_type=MessageType.RESULT,
                content=f"FAILED after {retry_count} attempts: {error}",
                task_id=task_id,
                metadata={"success": False, "error": error, "retries": retry_count},
            )
            self._retry_counts.pop(task_id, None)
            logger.error(
                "task_failed_max_retries",
                agent_id=self.agent_id,
                task_id=task_id,
                error=error,
            )
        else:
            await self.send(
                recipient="broadcast",
                msg_type=MessageType.STATUS_UPDATE,
                content=f"Attempt {retry_count} failed: {error}. Retrying...",
                task_id=task_id,
            )
            logger.warning(
                "task_retry",
                agent_id=self.agent_id,
                task_id=task_id,
                attempt=retry_count,
            )

    async def _handle_completion_check(self, message: MeshMessage) -> None:
        """Respond to completion check from PM."""
        task_id = message.task_id or "unknown"

        # Determine current status based on active task tracking
        # (self.status reflects bus-level activity via BaseAgent._on_message,
        # not actual task work — so we use _active_task_id instead)
        if self._active_task_id is not None:
            status = "still_working"
            details = f"Agent {self.agent_id} is still processing task {self._active_task_id}"
        else:
            status = "confirmed_done"
            details = f"Agent {self.agent_id} has completed its work"

        await self.send(
            recipient="pm",
            msg_type=MessageType.COMPLETION_CONFIRM,
            content=details,
            task_id=task_id,
            metadata={"status": status, "agent_id": self.agent_id},
        )
