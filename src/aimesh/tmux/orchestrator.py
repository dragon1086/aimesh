"""TmuxPMOrchestrator — PM logic bridge for tmux-hosted PM processes."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from aimesh.core.bus import AbstractMessageBus
from aimesh.core.message import MeshMessage, MessageType
from aimesh.core.registry import AgentRegistry
from aimesh.core.task import Task, TaskState
from aimesh.tasks.tracker import TaskTracker
from aimesh.tmux.bridge import HybridBridge
from aimesh.tmux.protocol import poll_outbox
from aimesh.tmux.session import TmuxSession

logger = structlog.get_logger("tmux.orchestrator")


@dataclass
class _PendingCheck:
    """Tracks a pending completion check for a parent task."""

    parent_id: str
    expected_agents: set[str]
    confirmations: dict[str, str] = field(default_factory=dict)
    event: asyncio.Event = field(default_factory=asyncio.Event)


class TmuxPMOrchestrator:
    """Drop-in replacement for PMAgent when engine_type is 'tmux'.

    Subscribes to bus as agent_id='pm'. Retains all stateful logic
    (TaskTracker, AgentRegistry, completion protocol, asyncio.Future).
    Delegates NL understanding and task decomposition to tmux PM via HybridBridge.
    """

    agent_id: str = "pm"
    agent_type: str = "pm"

    def __init__(
        self,
        bus: AbstractMessageBus,
        registry: AgentRegistry,
        tracker: TaskTracker,
        pm_id: str = "default",
        session_name: str = "",
        engine_command: str = "claude --dangerously-skip-permissions",
        workspace: str = "./workspace",
        data_dir: str = "data/pm",
        review_timeout_minutes: int = 30,
        completion_check_timeout: float = 60.0,
    ) -> None:
        self.bus = bus
        self.registry = registry
        self.tracker = tracker
        self.pm_id = pm_id
        self.session_name = session_name or f"aimesh-pm-{pm_id}"
        self.engine_command = engine_command
        self.workspace = workspace
        self.data_dir = Path(data_dir)
        self.review_timeout_minutes = review_timeout_minutes
        self.completion_check_timeout = completion_check_timeout
        self.status: str = "idle"

        self._session = TmuxSession()
        self._bridge = HybridBridge(session=self._session, data_dir=self.data_dir)
        self._review_timestamps: dict[str, float] = {}
        self._reminder_task: asyncio.Task[None] | None = None
        self._pending_checks: dict[str, _PendingCheck] = {}
        # Futures keyed by msg_id, resolved when outbox response arrives
        self._pending_responses: dict[str, asyncio.Future[dict]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Subscribe to bus, spawn tmux session, start polling."""
        await self.bus.subscribe(self.agent_id, self._on_message)
        self.status = "idle"

        spawned = await TmuxSession.spawn(
            self.session_name, self.engine_command, cwd=self.workspace
        )
        if not spawned:
            logger.warning(
                "tmux_spawn_failed",
                session_name=self.session_name,
                hint="PM will still run but tmux delegation unavailable",
            )

        await self._bridge.start_outbox_polling(
            pm_ids=[self.pm_id],
            callback=self._outbox_callback,
        )
        self._reminder_task = asyncio.create_task(self._review_reminder_loop())
        logger.info(
            "tmux_pm_orchestrator_started",
            pm_id=self.pm_id,
            session_name=self.session_name,
        )

    async def stop(self) -> None:
        """Stop polling, kill tmux session, unsubscribe."""
        if self._reminder_task:
            self._reminder_task.cancel()
            try:
                await self._reminder_task
            except asyncio.CancelledError:
                pass
            self._reminder_task = None

        await self._bridge.stop_outbox_polling()
        await TmuxSession.kill(self.session_name)
        await self.bus.unsubscribe(self.agent_id)
        self.status = "stopped"
        logger.info("tmux_pm_orchestrator_stopped", pm_id=self.pm_id)

    # ------------------------------------------------------------------
    # Bus interface
    # ------------------------------------------------------------------

    async def _on_message(self, message: MeshMessage) -> None:
        self.status = "busy"
        try:
            await self.handle_message(message)
        finally:
            self.status = "idle"

    async def handle_message(self, message: MeshMessage) -> None:
        """Dispatch incoming messages by type."""
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
            logger.debug("orchestrator_ignored_message", msg_type=message.msg_type.value)

    async def send(
        self,
        recipient: str,
        msg_type: MessageType,
        content: str,
        task_id: str | None = None,
        metadata: dict | None = None,
        parent_id: str | None = None,
    ) -> None:
        """Publish a MeshMessage to the bus."""
        msg = MeshMessage(
            sender=self.agent_id,
            recipient=recipient,
            msg_type=msg_type,
            content=content,
            task_id=task_id,
            metadata=metadata or {},
            parent_id=parent_id,
        )
        await self.bus.publish(msg)

    # ------------------------------------------------------------------
    # Outbox polling callback and response waiter
    # ------------------------------------------------------------------

    async def _outbox_callback(self, pm_id: str, response: dict) -> None:
        """Called by HybridBridge when a new outbox response file is found."""
        reply_to = response.get("reply_to", "")
        if reply_to and reply_to in self._pending_responses:
            future = self._pending_responses.pop(reply_to)
            if not future.done():
                future.set_result(response)
            logger.debug("outbox_response_resolved", reply_to=reply_to)
        else:
            logger.debug(
                "outbox_response_unmatched",
                reply_to=reply_to,
                response_id=response.get("id"),
            )

    async def _wait_for_outbox_response(
        self, msg_id: str, timeout: float = 30.0
    ) -> dict | None:
        """Poll for an outbox response whose reply_to matches msg_id.

        Returns parsed response dict or None on timeout.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict] = loop.create_future()
        self._pending_responses[msg_id] = future
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except asyncio.TimeoutError:
            self._pending_responses.pop(msg_id, None)
            logger.warning("outbox_response_timeout", msg_id=msg_id, timeout=timeout)
            return None

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    async def _handle_chat(self, message: MeshMessage) -> None:
        """Delegate NL understanding to tmux PM, execute action locally."""
        conversation_context = message.metadata.get("conversation_context", "")
        active_tasks = self._get_active_tasks_summary()
        workers = self.registry.all_agents()
        available_workers = "\n".join(
            f"- {a.agent_id} ({a.agent_type}): {', '.join(a.capabilities)}"
            for a in workers
            if a.agent_id != "pm"
        ) or "(none)"

        outbox_path = str(self._bridge.outbox_dir(self.pm_id))

        # Generate a placeholder msg_id for correlation; send_to_pm will embed it
        import uuid
        placeholder_id = str(uuid.uuid4())

        prompt = HybridBridge.format_prompt(
            msg_id=placeholder_id,
            msg_type="chat",
            user_message=message.content,
            active_tasks=active_tasks,
            available_workers=available_workers,
            conversation_context=conversation_context,
            outbox_path=outbox_path,
        )
        # Append JSON schema instructions at the end
        prompt += (
            "\n\nClassify the user intent and include a 'structured_data' field with:\n"
            '- New task: {"action": "create_task", "description": "..."}\n'
            '- Approval: {"action": "approve", "task_id": "..."}\n'
            '- Rejection: {"action": "reject", "task_id": "...", "feedback": "..."}\n'
            '- Status query: {"action": "status"}\n'
            '- Cancel: {"action": "cancel", "task_id": "..."}\n'
            '- General chat: {"action": "chat", "response": "..."}\n'
        )

        try:
            msg_id = await self._bridge.send_to_pm(
                pm_id=self.pm_id,
                session_name=self.session_name,
                prompt_text=prompt.replace(placeholder_id, "{MSG_ID}"),
            )

            response = await self._wait_for_outbox_response(msg_id, timeout=30.0)

            if response is None:
                raise RuntimeError("tmux PM did not respond within timeout")

            data: dict[str, Any] = response.get("structured_data", {})
            if not data:
                # Try to parse content as JSON fallback
                try:
                    data = json.loads(response.get("content", "{}"))
                except (json.JSONDecodeError, TypeError):
                    data = {"action": "chat", "response": response.get("content", "")}

            action = data.get("action", "chat")
            response_text = await self._execute_chat_action(action, data, message)

            await self.send(
                recipient=message.sender,
                msg_type=MessageType.CHAT,
                content=response_text,
                parent_id=message.id,
                metadata={"action": action, "original_message": message.content[:200]},
            )

        except Exception as e:
            logger.error("chat_delegation_failed", error=str(e))
            # Fallback: acknowledge gracefully
            await self.send(
                recipient=message.sender,
                msg_type=MessageType.CHAT,
                content="Sorry, I couldn't process that right now.",
                parent_id=message.id,
            )

    async def _execute_chat_action(
        self, action: str, data: dict, original_message: MeshMessage
    ) -> str:
        """Execute the action classified by tmux PM and return response text."""
        if action == "create_task":
            desc = data.get("description", original_message.content)
            task_msg = MeshMessage(
                sender="human",
                recipient="pm",
                msg_type=MessageType.TASK_ASSIGN,
                content=desc,
            )
            await self._handle_new_task(task_msg)
            return f"Task created: {desc[:100]}"

        elif action == "approve":
            task_id = data.get("task_id", "")
            success = await self.approve_task(task_id)
            return f"Task {task_id} approved." if success else f"Cannot approve {task_id}."

        elif action == "reject":
            task_id = data.get("task_id", "")
            feedback = data.get("feedback", "Please revise.")
            success = await self.reject_task(task_id, feedback)
            return f"Task {task_id} rejected." if success else f"Cannot reject {task_id}."

        elif action == "status":
            active = self.tracker.get_active_tasks()
            if active:
                lines = [f"Active tasks ({len(active)}):"]
                for t in active:
                    lines.append(f"  [{t.state.value}] {t.title}")
                return "\n".join(lines)
            return "No active tasks."

        elif action == "cancel":
            task_id = data.get("task_id", "")
            task = self.tracker.get(task_id)
            if task and not task.is_terminal:
                task.transition(TaskState.CANCELLED)
                return f"Task {task_id} cancelled."
            return f"Cannot cancel {task_id}."

        else:
            return data.get("response", "I'm not sure how to help with that.")

    async def _handle_new_task(self, message: MeshMessage) -> None:
        """Decompose via tmux PM and assign subtasks to workers."""
        task = Task(
            title=message.content[:100],
            description=message.content,
        )
        task.transition(TaskState.DECOMPOSING)
        self.tracker.add(task)

        await self.send(
            recipient="broadcast",
            msg_type=MessageType.STATUS_UPDATE,
            content=f"Decomposing task: {task.title}",
            task_id=task.id,
        )

        workers = self.registry.all_agents()
        available_workers_str = "\n".join(
            f"- {a.agent_id} ({a.agent_type}): {', '.join(a.capabilities)}"
            for a in workers
            if a.agent_id != "pm"
        ) or "(none)"

        outbox_path = str(self._bridge.outbox_dir(self.pm_id))
        import uuid
        placeholder_id = str(uuid.uuid4())

        decomp_prompt = HybridBridge.format_prompt(
            msg_id=placeholder_id,
            msg_type="task_decomposition",
            user_message=task.description,
            available_workers=available_workers_str,
            outbox_path=outbox_path,
        )
        decomp_prompt += (
            "\n\nDecompose this task into subtasks. In 'structured_data', provide:\n"
            '{"subtasks": [{"title": "...", "description": "...", "agent_type": "...", "priority": 1}]}\n'
            "Use agent_type values matching the available worker types above.\n"
        )

        subtask_defs: list[dict] = []
        try:
            msg_id = await self._bridge.send_to_pm(
                pm_id=self.pm_id,
                session_name=self.session_name,
                prompt_text=decomp_prompt.replace(placeholder_id, "{MSG_ID}"),
            )
            response = await self._wait_for_outbox_response(msg_id, timeout=60.0)

            if response is not None:
                data = response.get("structured_data", {})
                subtask_defs = data.get("subtasks", [])
                if not subtask_defs:
                    # Try parsing content as JSON fallback
                    try:
                        parsed = json.loads(response.get("content", "{}"))
                        subtask_defs = parsed.get("subtasks", [])
                    except (json.JSONDecodeError, TypeError):
                        pass

        except Exception as e:
            logger.error("decomposition_delegation_failed", error=str(e))

        # Fallback: single subtask assigned to first available worker
        if not subtask_defs:
            logger.warning(
                "decomposition_fallback",
                task_id=task.id,
                hint="tmux PM unresponsive; using single-subtask fallback",
            )
            subtask_defs = [
                {
                    "title": task.title,
                    "description": task.description,
                    "agent_type": "coder",
                    "priority": 1,
                }
            ]

        # Create subtasks and assign
        for subtask_def in sorted(subtask_defs, key=lambda x: x.get("priority", 5)):
            subtask = Task(
                title=subtask_def["title"],
                description=subtask_def.get("description", ""),
                parent_id=task.id,
            )
            task.subtask_ids.append(subtask.id)
            self.tracker.add(subtask)

            agent_type = subtask_def.get("agent_type", "coder")
            workers_for_type = self.registry.discover_by_type(agent_type)

            if workers_for_type:
                worker = workers_for_type[0]
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
                logger.info(
                    "subtask_assigned",
                    subtask_id=subtask.id,
                    worker=worker.agent_id,
                    title=subtask.title,
                )
            else:
                subtask.transition(TaskState.DECOMPOSING)
                subtask.transition(TaskState.FAILED)
                subtask.error = f"No worker of type '{agent_type}' available"
                logger.warning("no_worker_available", agent_type=agent_type)

        task.transition(TaskState.ASSIGNED)
        await self.send(
            recipient="broadcast",
            msg_type=MessageType.STATUS_UPDATE,
            content=f"Task decomposed into {len(subtask_defs)} subtasks and assigned",
            task_id=task.id,
        )

    async def _handle_result(self, message: MeshMessage) -> None:
        """Handle a worker's completed result."""
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

        if task.parent_id:
            siblings = self.tracker.get_subtasks(task.parent_id)
            all_in_review = all(s.state == TaskState.REVIEW for s in siblings)

            if all_in_review and siblings:
                asyncio.create_task(
                    self._run_completion_check(task.parent_id, siblings)
                )

    async def _run_completion_check(
        self, parent_id: str, subtasks: list[Task]
    ) -> None:
        """Broadcast COMPLETION_CHECK to all assigned agents and collect confirmations."""
        assigned_agents: set[str] = set()
        for st in subtasks:
            if st.assigned_to:
                assigned_agents.add(st.assigned_to)

        if not assigned_agents:
            self._approve_subtasks_and_update_parent(parent_id, subtasks)
            return

        check = _PendingCheck(parent_id=parent_id, expected_agents=assigned_agents)
        self._pending_checks[parent_id] = check

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

        try:
            await asyncio.wait_for(
                check.event.wait(), timeout=self.completion_check_timeout
            )
        except asyncio.TimeoutError:
            missing = assigned_agents - set(check.confirmations.keys())
            logger.warning(
                "completion_check_timeout",
                parent_id=parent_id,
                missing_agents=list(missing),
            )
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

        still_working = [
            aid
            for aid, status in check.confirmations.items()
            if status != "confirmed_done"
        ]

        if still_working:
            logger.info(
                "completion_check_not_ready",
                parent_id=parent_id,
                still_working=still_working,
            )
            self._pending_checks.pop(parent_id, None)
        else:
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

        parent = self.tracker.get(parent_id)
        if parent and parent.state == TaskState.ASSIGNED:
            parent.transition(TaskState.IN_PROGRESS)

        self.tracker.update_parent_status(parent_id)
        logger.info("subtasks_approved", parent_id=parent_id)

    async def _handle_completion_confirm(self, message: MeshMessage) -> None:
        """Handle COMPLETION_CONFIRM from a worker agent."""
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
        """Forward worker questions to broadcast for human attention."""
        await self.send(
            recipient="broadcast",
            msg_type=MessageType.QUESTION,
            content=f"Question from {message.sender}: {message.content}",
            task_id=message.task_id,
            parent_id=message.id,
        )

    # ------------------------------------------------------------------
    # Public API (approve/reject) — same interface as PMAgent
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_active_tasks_summary(self) -> str:
        """Build a summary of active tasks for prompt context."""
        active = self.tracker.get_active_tasks()
        if not active:
            return "(no active tasks)"
        lines = []
        for t in active:
            lines.append(f"- [{t.state.value}] {t.title} (id: {t.id})")
        return "\n".join(lines)

    async def _review_reminder_loop(self) -> None:
        """Periodically check for stale reviews and send reminders."""
        while True:
            await asyncio.sleep(60)
            now = time.monotonic()
            timeout_secs = self.review_timeout_minutes * 60
            for task_id, entered_at in list(self._review_timestamps.items()):
                if now - entered_at >= timeout_secs:
                    task = self.tracker.get(task_id)
                    if task and task.state == TaskState.REVIEW:
                        await self.send(
                            recipient="broadcast",
                            msg_type=MessageType.SYSTEM,
                            content=(
                                f"Reminder: Task '{task.title}' has been awaiting review "
                                f"for {self.review_timeout_minutes}+ minutes. "
                                f"Use /approve {task_id} or /reject {task_id}."
                            ),
                            task_id=task_id,
                        )
                        self._review_timestamps[task_id] = now
                    else:
                        del self._review_timestamps[task_id]
