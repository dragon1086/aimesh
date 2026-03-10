"""Tests for keyword pre-filter (Phase 2a)."""

from aimesh.core.task import Task, TaskState
from aimesh.nl.prefilter import PrefilterResult, prefilter_message


def _make_review_task(title: str = "Test Task") -> Task:
    """Create a task in REVIEW state."""
    t = Task(title=title, description=title)
    t.transition(TaskState.DECOMPOSING)
    t.transition(TaskState.ASSIGNED)
    t.transition(TaskState.IN_PROGRESS)
    t.transition(TaskState.REVIEW)
    return t


def _make_in_progress_task(title: str = "WIP Task") -> Task:
    """Create a task in IN_PROGRESS state."""
    t = Task(title=title, description=title)
    t.transition(TaskState.DECOMPOSING)
    t.transition(TaskState.ASSIGNED)
    t.transition(TaskState.IN_PROGRESS)
    return t


def test_korean_approve_with_single_review_task():
    """'승인' with 1 REVIEW task resolves target_task_id."""
    task = _make_review_task()
    result = prefilter_message("승인", [task])
    assert result is not None
    assert result.action == "approve"
    assert result.target_task_id == task.id


def test_english_approve():
    """'approve' matches approve pattern."""
    task = _make_review_task()
    result = prefilter_message("approve", [task])
    assert result is not None
    assert result.action == "approve"
    assert result.target_task_id == task.id


def test_korean_status():
    """'상태' matches status pattern."""
    result = prefilter_message("상태", [])
    assert result is not None
    assert result.action == "status"


def test_english_status():
    """'status' matches status pattern."""
    result = prefilter_message("status", [])
    assert result is not None
    assert result.action == "status"


def test_korean_cancel():
    """'취소' matches cancel pattern."""
    result = prefilter_message("취소", [])
    assert result is not None
    assert result.action == "cancel"


def test_no_match_natural_language():
    """Natural language task request returns None."""
    result = prefilter_message("로그인 만들어줘", [])
    assert result is None


def test_no_match_partial():
    """Partial match like 'approve this feature' returns None."""
    result = prefilter_message("approve this feature", [])
    assert result is None


def test_ambiguous_two_review_tasks():
    """'승인' with 2 REVIEW tasks returns None (let PM disambiguate)."""
    task1 = _make_review_task("Task 1")
    task2 = _make_review_task("Task 2")
    result = prefilter_message("승인", [task1, task2])
    assert result is None


def test_approve_no_review_tasks_no_active():
    """'approve' with no active tasks returns result (no tasks to check)."""
    result = prefilter_message("approve", [])
    assert result is not None
    assert result.action == "approve"
    assert result.target_task_id is None


def test_reject_korean():
    """'다시 해' matches reject pattern."""
    task = _make_review_task()
    result = prefilter_message("다시 해", [task])
    assert result is not None
    assert result.action == "reject"
    assert result.target_task_id == task.id


def test_case_insensitive():
    """Matching is case-insensitive."""
    result = prefilter_message("STATUS", [])
    assert result is not None
    assert result.action == "status"


def test_whitespace_stripped():
    """Leading/trailing whitespace is stripped."""
    result = prefilter_message("  status  ", [])
    assert result is not None
    assert result.action == "status"


def test_empty_string():
    """Empty string returns None."""
    result = prefilter_message("", [])
    assert result is None


def test_mixed_active_tasks_one_review():
    """Approve with 1 REVIEW + 1 IN_PROGRESS resolves to the REVIEW task."""
    review = _make_review_task("Review Me")
    wip = _make_in_progress_task("Still Working")
    result = prefilter_message("승인", [review, wip])
    assert result is not None
    assert result.action == "approve"
    assert result.target_task_id == review.id


def test_message_type_chat_serialization():
    """MessageType.CHAT serializes and deserializes correctly."""
    from aimesh.core.message import MeshMessage, MessageType

    msg = MeshMessage(
        sender="user", recipient="pm",
        msg_type=MessageType.CHAT,
        content="로그인 만들어줘",
    )
    data = msg.to_dict()
    assert data["msg_type"] == "chat"

    restored = MeshMessage.from_dict(data)
    assert restored.msg_type == MessageType.CHAT
    assert restored.content == "로그인 만들어줘"
