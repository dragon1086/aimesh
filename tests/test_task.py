"""Tests for Task state machine."""

import pytest

from aimesh.core.task import Task, TaskState


def test_valid_transitions():
    """Test all valid state transitions."""
    transitions = [
        (TaskState.CREATED, TaskState.DECOMPOSING),
        (TaskState.DECOMPOSING, TaskState.ASSIGNED),
        (TaskState.ASSIGNED, TaskState.IN_PROGRESS),
        (TaskState.IN_PROGRESS, TaskState.REVIEW),
        (TaskState.REVIEW, TaskState.DONE),
        (TaskState.REVIEW, TaskState.REWORK),
        (TaskState.REWORK, TaskState.IN_PROGRESS),
    ]
    for from_state, to_state in transitions:
        task = Task(title="Test", description="Test task")
        task.state = from_state
        task.transition(to_state)
        assert task.state == to_state


def test_cancelled_from_all_active_states():
    """CANCELLED should be reachable from all active states."""
    active_states = [
        TaskState.CREATED,
        TaskState.DECOMPOSING,
        TaskState.ASSIGNED,
        TaskState.IN_PROGRESS,
        TaskState.REVIEW,
        TaskState.REWORK,
    ]
    for state in active_states:
        task = Task(title="Test", description="Test")
        task.state = state
        task.transition(TaskState.CANCELLED)
        assert task.state == TaskState.CANCELLED


def test_assigned_to_failed():
    """Worker rejects or times out before starting."""
    task = Task(title="Test", description="Test")
    task.state = TaskState.ASSIGNED
    task.transition(TaskState.FAILED)
    assert task.state == TaskState.FAILED


def test_decomposing_to_failed():
    task = Task(title="Test", description="Test")
    task.state = TaskState.DECOMPOSING
    task.transition(TaskState.FAILED)
    assert task.state == TaskState.FAILED


def test_in_progress_to_failed():
    task = Task(title="Test", description="Test")
    task.state = TaskState.IN_PROGRESS
    task.transition(TaskState.FAILED)
    assert task.state == TaskState.FAILED


def test_rework_to_failed():
    task = Task(title="Test", description="Test")
    task.state = TaskState.REWORK
    task.transition(TaskState.FAILED)
    assert task.state == TaskState.FAILED


def test_invalid_transition_raises():
    """Invalid transitions should raise ValueError."""
    task = Task(title="Test", description="Test")
    # CREATED -> DONE is not valid
    with pytest.raises(ValueError, match="Invalid transition"):
        task.transition(TaskState.DONE)


def test_invalid_transition_from_terminal():
    """Terminal states should not allow any transition."""
    for terminal_state in [TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED]:
        task = Task(title="Test", description="Test")
        task.state = terminal_state
        with pytest.raises(ValueError):
            task.transition(TaskState.CREATED)


def test_transition_updates_timestamp():
    task = Task(title="Test", description="Test")
    original = task.updated_at
    task.transition(TaskState.DECOMPOSING)
    assert task.updated_at >= original


def test_is_terminal():
    task = Task(title="Test", description="Test")
    assert not task.is_terminal
    task.state = TaskState.DONE
    assert task.is_terminal
    task.state = TaskState.FAILED
    assert task.is_terminal
    task.state = TaskState.CANCELLED
    assert task.is_terminal


def test_task_serialization():
    task = Task(title="Build API", description="Build a REST API", assigned_to="coder-1")
    data = task.to_dict()
    assert data["title"] == "Build API"
    assert data["assigned_to"] == "coder-1"
    assert data["state"] == "created"
