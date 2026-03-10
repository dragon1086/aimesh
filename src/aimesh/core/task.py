"""Task model and state machine for AI Mesh."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4


class TaskState(Enum):
    """States in the task lifecycle."""

    CREATED = "created"
    DECOMPOSING = "decomposing"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"
    FAILED = "failed"
    REWORK = "rework"
    CANCELLED = "cancelled"


# Valid state transitions
VALID_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.CREATED: {TaskState.DECOMPOSING, TaskState.CANCELLED},
    TaskState.DECOMPOSING: {TaskState.ASSIGNED, TaskState.FAILED, TaskState.CANCELLED},
    TaskState.ASSIGNED: {TaskState.IN_PROGRESS, TaskState.FAILED, TaskState.CANCELLED},
    TaskState.IN_PROGRESS: {TaskState.REVIEW, TaskState.FAILED, TaskState.CANCELLED},
    TaskState.REVIEW: {TaskState.DONE, TaskState.REWORK, TaskState.CANCELLED},
    TaskState.REWORK: {TaskState.IN_PROGRESS, TaskState.FAILED, TaskState.CANCELLED},
    # Terminal states: no transitions out
    TaskState.DONE: set(),
    TaskState.FAILED: set(),
    TaskState.CANCELLED: set(),
}


@dataclass
class Task:
    """A task in the AI Mesh system."""

    title: str
    description: str
    id: str = field(default_factory=lambda: str(uuid4()))
    state: TaskState = field(default=TaskState.CREATED)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    assigned_to: str | None = None
    parent_id: str | None = None
    subtask_ids: list[str] = field(default_factory=list)
    result: str | None = None
    error: str | None = None
    metadata: dict = field(default_factory=dict)
    retry_count: int = 0

    def transition(self, new_state: TaskState) -> None:
        """Transition to a new state. Raises ValueError if invalid."""
        valid = VALID_TRANSITIONS.get(self.state, set())
        if new_state not in valid:
            raise ValueError(
                f"Invalid transition: {self.state.value} -> {new_state.value}. "
                f"Valid targets: {[s.value for s in valid]}"
            )
        self.state = new_state
        self.updated_at = datetime.now(timezone.utc)

    @property
    def is_terminal(self) -> bool:
        """Check if the task is in a terminal state."""
        return self.state in {TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED}

    def to_dict(self) -> dict:
        """Serialize task to a dictionary."""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "assigned_to": self.assigned_to,
            "parent_id": self.parent_id,
            "subtask_ids": self.subtask_ids,
            "result": self.result,
            "error": self.error,
            "metadata": self.metadata,
            "retry_count": self.retry_count,
        }
