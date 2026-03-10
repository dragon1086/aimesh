"""Task progress tracking and status aggregation."""

import structlog

from aimesh.core.task import Task, TaskState

logger = structlog.get_logger("tracker")


class TaskTracker:
    """Tracks tasks and aggregates subtask status into parent task status."""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    def add(self, task: Task) -> None:
        """Track a task."""
        self._tasks[task.id] = task

    def get(self, task_id: str) -> Task | None:
        """Get a task by ID."""
        return self._tasks.get(task_id)

    def get_subtasks(self, parent_id: str) -> list[Task]:
        """Get all subtasks for a parent task."""
        return [t for t in self._tasks.values() if t.parent_id == parent_id]

    def get_active_tasks(self) -> list[Task]:
        """Get all non-terminal tasks."""
        return [t for t in self._tasks.values() if not t.is_terminal]

    def get_all_tasks(self) -> list[Task]:
        """Get all tracked tasks."""
        return list(self._tasks.values())

    def update_parent_status(self, parent_id: str) -> TaskState | None:
        """Aggregate subtask status into parent task status.

        Returns the parent's new state, or None if parent not found.

        Rules:
        - All subtasks DONE → parent moves to REVIEW
        - Any subtask FAILED → parent FAILED (if max retries exceeded)
        - Any subtask IN_PROGRESS → parent stays IN_PROGRESS
        - All subtasks CANCELLED → parent CANCELLED
        """
        parent = self._tasks.get(parent_id)
        if not parent:
            return None

        subtasks = self.get_subtasks(parent_id)
        if not subtasks:
            return parent.state

        states = [t.state for t in subtasks]

        if all(s == TaskState.DONE for s in states):
            if parent.state not in {TaskState.DONE, TaskState.REVIEW, TaskState.CANCELLED}:
                parent.transition(TaskState.REVIEW)
                logger.info("parent_moved_to_review", parent_id=parent_id)
        elif all(s == TaskState.CANCELLED for s in states):
            if not parent.is_terminal:
                parent.transition(TaskState.CANCELLED)
        elif any(s == TaskState.FAILED for s in states):
            failed_count = sum(1 for s in states if s == TaskState.FAILED)
            if failed_count == len(states) and not parent.is_terminal:
                parent.transition(TaskState.FAILED)
                logger.error("parent_failed", parent_id=parent_id)

        return parent.state
