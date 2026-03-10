"""PM (Project Manager) agent for AI Mesh."""

import asyncio
import time
from dataclasses import dataclass, field

import structlog

from aimesh.agents.base import BaseAgent
from aimesh.agents.executor import BaseExecutor
from aimesh.core.bus import AbstractMessageBus
from aimesh.core.message import MeshMessage, MessageType
from aimesh.core.registry import AgentRegistry
from aimesh.core.task import Task, TaskState
from aimesh.tasks.decomposer import decompose_task
from aimesh.tasks.tracker import TaskTracker

logger = structlog.get_logger("pm")


@dataclass
class _PendingCheck:
    """Tracks a pending completion check for a parent task."""

    parent_id: str
    expected_agents: set[str]
    confirmations: dict[str, str] = field(default_factory=dict)  # agent_id -> status
    event: asyncio.Event = field(default_factory=asyncio.Event)


class PMAgent(BaseAgent):
    """PM agent that decomposes tasks, assigns to workers, and tracks progress."""

    def __init__(
        self,
        bus: AbstractMessageBus,
        executor: BaseExecutor,
        registry: AgentRegistry,
        tracker: TaskTracker,
        review_timeout_minutes: int = 30,
        completion_check_timeout: float = 60.0,
    ) -> None:
        super().__init__(
            agent_id="pm",
            agent_type="pm",
            bus=bus,
            capabilities=["decompose", "assign", "track", "coordinate"],
        )
        self.executor = executor
        self.registry = registry
        self.tracker = tracker
        self.review_timeout_minutes = review_timeout_minutes
        self.completion_check_timeout = completion_check_timeout
        self._review_timestamps: dict[str, float] = {}
        self._reminder_task: asyncio.Task[None] | None = None
        self._pending_checks: dict[str, _PendingCheck] = {}  # parent_id -> check

    async def start(self) -> None:
        """Start the PM agent and the review reminder loop."""
        await super().start()
        self._reminder_task = asyncio.create_task(self._review_reminder_loop())

    async def stop(self) -> None:
        """Stop the PM agent and cancel the reminder loop."""
        if self._reminder_task:
            self._reminder_task.cancel()
            try:
                await self._reminder_task
            except asyncio.CancelledError:
                pass
            self._reminder_task = None
        await super().stop()

    async def _review_reminder_loop(self) -> None:
        """Periodically check for stale reviews and send reminders."""
        while True:
            await asyncio.sleep(60)  # Check every minute
            now = time.monotonic()
            timeout_secs = self.review_timeout_minutes * 60
            for task_id, entered_at in list(self._review_timestamps.items()):
                if now - entered_at >= timeout_secs:
                    task = self.tracker.get(task_id)
                    if task and task.state == TaskState.REVIEW:
                        await self.send(
                            recipient="broadcast",
                            msg_type=MessageType.SYSTEM,
                            content=f"Reminder: Task '{task.title}' has been awaiting review for {self.review_timeout_minutes}+ minutes. Use /approve {task_id} or /reject {task_id}.",
                            task_id=task_id,
                        )
                        # Reset timer so reminder repeats at next interval
                        self._review_timestamps[task_id] = now
                    else:
                        # Task no longer in REVIEW, stop tracking
                        del self._review_timestamps[task_id]

    async def handle_message(self, message: MeshMessage) -> None:
        """Handle incoming messages based on type."""
        if message.msg_type == MessageType.TASK_ASSIGN:
            await self._handle_new_task(message)
        elif message.msg_type == MessageType.RESULT:
            await self._handle_result(message)
        elif message.msg_type == MessageType.STATUS_UPDATE:
            await self._handle_status_update(message)
        elif message.msg_type == MessageType.QUESTION:
            await self._handle_question(message)
        elif message.msg_type == MessageType.COMPLETION_CONFIRM:
            await self._handle_completion_confirm(message)
        elif message.msg_type == MessageType.CHAT:
            await self._handle_chat(message)
        else:
            logger.debug("pm_ignored_message", msg_type=message.msg_type.value)

    async def _handle_new_task(self, message: MeshMessage) -> None:
        """Decompose a new task and assign subtasks to workers."""
        task = Task(
            title=message.content[:100],
            description=message.content,
        )
        task.transition(TaskState.DECOMPOSING)
        self.tracker.add(task)

        # Notify: decomposing
        await self.send(
            recipient="broadcast",
            msg_type=MessageType.STATUS_UPDATE,
            content=f"Decomposing task: {task.title}",
            task_id=task.id,
        )

        # Decompose via LLM
        subtask_defs = await decompose_task(task, self.executor, registry=self.registry)

        if not subtask_defs:
            task.transition(TaskState.FAILED)
            task.error = "Failed to decompose task"
            await self.send(
                recipient="broadcast",
                msg_type=MessageType.SYSTEM,
                content=f"Failed to decompose task: {task.title}",
                task_id=task.id,
            )
            return

        # Create subtasks and assign
        for subtask_def in sorted(subtask_defs, key=lambda x: x.get("priority", 5)):
            subtask = Task(
                title=subtask_def["title"],
                description=subtask_def.get("description", ""),
                parent_id=task.id,
            )
            task.subtask_ids.append(subtask.id)
            self.tracker.add(subtask)

            # Find a suitable worker
            agent_type = subtask_def.get("agent_type", "coder")
            workers = self.registry.discover_by_type(agent_type)

            if workers:
                worker = workers[0]  # Simple: pick first available
                subtask.assigned_to = worker.agent_id
                subtask.transition(TaskState.DECOMPOSING)
                subtask.transition(TaskState.ASSIGNED)

                await self.send(
                    recipient=worker.agent_id,
                    msg_type=MessageType.TASK_ASSIGN,
                    content=subtask.description,
                    task_id=subtask.id,
                    metadata={
                        "title": subtask.title,
                        "parent_task_id": task.id,
                        "agent_type": agent_type,
                    },
                )

                logger.info("subtask_assigned",
                            subtask_id=subtask.id,
                            worker=worker.agent_id,
                            title=subtask.title)
            else:
                subtask.transition(TaskState.DECOMPOSING)
                subtask.transition(TaskState.FAILED)
                subtask.error = f"No worker of type '{agent_type}' available"
                logger.warning("no_worker_available", agent_type=agent_type)

        # Move parent to assigned
        task.transition(TaskState.ASSIGNED)

        await self.send(
            recipient="broadcast",
            msg_type=MessageType.STATUS_UPDATE,
            content=f"Task decomposed into {len(subtask_defs)} subtasks and assigned",
            task_id=task.id,
        )

    async def _handle_result(self, message: MeshMessage) -> None:
        """Handle a worker's completed result.

        Intercepts before update_parent_status to run the completion
        confirmation protocol when all sibling subtasks reach REVIEW.
        """
        if not message.task_id:
            return

        task = self.tracker.get(message.task_id)
        if not task:
            return

        task.result = message.content
        task.metadata.update(message.metadata)

        if task.state == TaskState.IN_PROGRESS:
            task.transition(TaskState.REVIEW)
            self._review_timestamps[message.task_id] = time.monotonic()

        await self.send(
            recipient="broadcast",
            msg_type=MessageType.REVIEW_REQUEST,
            content=f"Task '{task.title}' completed by {message.sender}. Awaiting approval.",
            task_id=message.task_id,
            metadata={"result_preview": message.content[:500]},
        )

        # Completion confirmation protocol: before calling update_parent_status,
        # check if all sibling subtasks are in REVIEW and run confirmation check.
        # Run as a background task so the bus consumer isn't blocked while
        # awaiting COMPLETION_CONFIRM responses (which arrive on the same queue).
        if task.parent_id:
            siblings = self.tracker.get_subtasks(task.parent_id)
            all_in_review = all(s.state == TaskState.REVIEW for s in siblings)

            if all_in_review and siblings:
                asyncio.create_task(
                    self._run_completion_check(task.parent_id, siblings)
                )
            # If not all in REVIEW yet, don't call update_parent_status

    async def _run_completion_check(
        self, parent_id: str, subtasks: list[Task]
    ) -> None:
        """Broadcast COMPLETION_CHECK to all assigned agents and collect confirmations."""
        # Gather assigned agents
        assigned_agents: set[str] = set()
        for st in subtasks:
            if st.assigned_to:
                assigned_agents.add(st.assigned_to)

        if not assigned_agents:
            # No agents to check — approve directly
            self._approve_subtasks_and_update_parent(parent_id, subtasks)
            return

        # Set up pending check
        check = _PendingCheck(parent_id=parent_id, expected_agents=assigned_agents)
        self._pending_checks[parent_id] = check

        # Broadcast COMPLETION_CHECK to each assigned agent
        for agent_id in assigned_agents:
            await self.send(
                recipient=agent_id,
                msg_type=MessageType.COMPLETION_CHECK,
                content=f"Confirm completion status for parent task {parent_id}",
                task_id=parent_id,
                metadata={"requesting_agent": "pm"},
            )

        logger.info(
            "completion_check_sent",
            parent_id=parent_id,
            agents=list(assigned_agents),
        )

        # Wait for all confirmations or timeout
        try:
            await asyncio.wait_for(check.event.wait(), timeout=self.completion_check_timeout)
        except asyncio.TimeoutError:
            missing = assigned_agents - set(check.confirmations.keys())
            logger.warning(
                "completion_check_timeout",
                parent_id=parent_id,
                missing_agents=list(missing),
            )
            # Approve anyway with warning
            self._approve_subtasks_and_update_parent(parent_id, subtasks)
            await self.send(
                recipient="broadcast",
                msg_type=MessageType.SYSTEM,
                content=(
                    f"Warning: Completion check timed out for parent task {parent_id}. "
                    f"Missing confirmations from: {', '.join(missing)}. "
                    f"Proceeding with approval."
                ),
                task_id=parent_id,
            )
            self._pending_checks.pop(parent_id, None)
            return

        # All confirmations received — check statuses
        still_working = [
            aid for aid, status in check.confirmations.items()
            if status != "confirmed_done"
        ]

        if still_working:
            logger.info(
                "completion_check_not_ready",
                parent_id=parent_id,
                still_working=still_working,
            )
            # Don't approve — agents are still working. They will send
            # another RESULT when done, retriggering this flow.
            self._pending_checks.pop(parent_id, None)
        else:
            # All confirmed done — approve subtasks and update parent
            self._approve_subtasks_and_update_parent(parent_id, subtasks)
            self._pending_checks.pop(parent_id, None)

    def _approve_subtasks_and_update_parent(
        self, parent_id: str, subtasks: list[Task]
    ) -> None:
        """Transition all REVIEW subtasks to DONE and update parent status."""
        for st in subtasks:
            if st.state == TaskState.REVIEW:
                st.transition(TaskState.DONE)
                self._review_timestamps.pop(st.id, None)

        # Ensure parent is in a state that can transition to REVIEW
        parent = self.tracker.get(parent_id)
        if parent and parent.state == TaskState.ASSIGNED:
            parent.transition(TaskState.IN_PROGRESS)

        self.tracker.update_parent_status(parent_id)
        logger.info("subtasks_approved", parent_id=parent_id)

    async def _handle_completion_confirm(self, message: MeshMessage) -> None:
        """Handle COMPLETION_CONFIRM from a worker agent."""
        # Find the pending check by task_id (which is the parent_id)
        parent_id = message.task_id
        if not parent_id or parent_id not in self._pending_checks:
            logger.debug(
                "completion_confirm_no_pending_check",
                task_id=parent_id,
                sender=message.sender,
            )
            return

        check = self._pending_checks[parent_id]
        agent_id = message.metadata.get("agent_id", message.sender)
        status = message.metadata.get("status", "confirmed_done")

        check.confirmations[agent_id] = status
        logger.info(
            "completion_confirm_received",
            parent_id=parent_id,
            agent_id=agent_id,
            status=status,
        )

        # If all expected agents have responded, signal the event
        if check.expected_agents <= set(check.confirmations.keys()):
            check.event.set()

    async def _handle_status_update(self, message: MeshMessage) -> None:
        """Track worker progress updates."""
        if not message.task_id:
            return
        task = self.tracker.get(message.task_id)
        if task and task.state == TaskState.ASSIGNED:
            task.transition(TaskState.IN_PROGRESS)

    async def _handle_question(self, message: MeshMessage) -> None:
        """Forward worker questions to the group for human attention."""
        await self.send(
            recipient="broadcast",
            msg_type=MessageType.QUESTION,
            content=f"Question from {message.sender}: {message.content}",
            task_id=message.task_id,
            parent_id=message.id,
        )

    async def approve_task(self, task_id: str) -> bool:
        """Approve a task and move to DONE."""
        task = self.tracker.get(task_id)
        if not task or task.state != TaskState.REVIEW:
            return False
        task.transition(TaskState.DONE)
        self._review_timestamps.pop(task_id, None)

        if task.parent_id:
            self.tracker.update_parent_status(task.parent_id)

        await self.send(
            recipient="broadcast",
            msg_type=MessageType.SYSTEM,
            content=f"Task '{task.title}' approved and marked DONE.",
            task_id=task_id,
        )
        return True

    async def reject_task(self, task_id: str, feedback: str) -> bool:
        """Reject a task and send it back for rework."""
        task = self.tracker.get(task_id)
        if not task or task.state != TaskState.REVIEW:
            return False
        task.transition(TaskState.REWORK)
        self._review_timestamps.pop(task_id, None)

        if task.assigned_to:
            await self.send(
                recipient=task.assigned_to,
                msg_type=MessageType.TASK_ASSIGN,
                content=f"REWORK: {feedback}\n\nOriginal task: {task.description}",
                task_id=task_id,
                metadata={"rework": True, "feedback": feedback},
            )
        return True

    def _get_active_tasks_summary(self) -> str:
        """Build a summary of active tasks for CHAT classification prompt."""
        active = self.tracker.get_active_tasks()
        if not active:
            return "(no active tasks)"
        lines = []
        for t in active:
            lines.append(f"- [{t.state.value}] {t.title} (id: {t.id})")
        return "\n".join(lines)

    async def _handle_chat(self, message: MeshMessage) -> None:
        """Handle natural language message — classify intent via LLM and act."""
        conversation_context = message.metadata.get("conversation_context", "")
        active_tasks = self._get_active_tasks_summary()

        prompt = (
            "You are the PM for this team. A user sent this message in the group chat.\n\n"
            f"ACTIVE TASKS:\n{active_tasks}\n\n"
            f"RECENT CONVERSATION:\n{conversation_context}\n\n"
            f"USER MESSAGE: {message.content}\n\n"
            "Classify the intent and respond in JSON:\n"
            '- New task: {"action": "create_task", "description": "..."}\n'
            '- Approval: {"action": "approve", "task_id": "..."}\n'
            '- Rejection: {"action": "reject", "task_id": "...", "feedback": "..."}\n'
            '- Status query: {"action": "status"}\n'
            '- Cancel: {"action": "cancel", "task_id": "..."}\n'
            '- General chat: {"action": "chat", "response": "..."}\n\n'
            "Respond in JSON only."
        )

        try:
            result = await self.executor.execute(prompt)
            import json
            try:
                data = json.loads(result.content if hasattr(result, "content") else str(result))
            except (json.JSONDecodeError, TypeError):
                # Try to extract JSON from response
                raw = result.content if hasattr(result, "content") else str(result)
                # Find first { and last }
                start = raw.find("{")
                end = raw.rfind("}") + 1
                if start >= 0 and end > start:
                    data = json.loads(raw[start:end])
                else:
                    data = {"action": "chat", "response": raw}

            action = data.get("action", "chat")

            if action == "create_task":
                desc = data.get("description", message.content)
                task_msg = MeshMessage(
                    sender="human",
                    recipient="pm",
                    msg_type=MessageType.TASK_ASSIGN,
                    content=desc,
                )
                await self._handle_new_task(task_msg)
                response = f"Task created: {desc[:100]}"

            elif action == "approve":
                task_id = data.get("task_id", "")
                success = await self.approve_task(task_id)
                response = f"Task {task_id} approved." if success else f"Cannot approve {task_id}."

            elif action == "reject":
                task_id = data.get("task_id", "")
                feedback = data.get("feedback", "Please revise.")
                success = await self.reject_task(task_id, feedback)
                response = f"Task {task_id} rejected." if success else f"Cannot reject {task_id}."

            elif action == "status":
                active = self.tracker.get_active_tasks()
                if active:
                    lines = [f"Active tasks ({len(active)}):"]
                    for t in active:
                        lines.append(f"  [{t.state.value}] {t.title}")
                    response = "\n".join(lines)
                else:
                    response = "No active tasks."

            elif action == "cancel":
                task_id = data.get("task_id", "")
                task = self.tracker.get(task_id)
                if task and not task.is_terminal:
                    task.transition(TaskState.CANCELLED)
                    response = f"Task {task_id} cancelled."
                else:
                    response = f"Cannot cancel {task_id}."

            else:
                response = data.get("response", "I'm not sure how to help with that.")

            # Send response back with parent_id for correlation
            await self.send(
                recipient=message.sender,
                msg_type=MessageType.CHAT,
                content=response,
                parent_id=message.id,
                metadata={"action": action, "original_message": message.content[:200]},
            )

        except Exception as e:
            logger.error("chat_classification_failed", error=str(e))
            await self.send(
                recipient=message.sender,
                msg_type=MessageType.CHAT,
                content="Sorry, I couldn't understand that message.",
                parent_id=message.id,
            )
