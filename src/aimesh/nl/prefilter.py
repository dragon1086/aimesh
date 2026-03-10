"""Keyword pre-filter for unambiguous NL commands.

Handles exact-match patterns only. Everything else goes to PM for LLM classification.
"""

from dataclasses import dataclass
from typing import Literal

from aimesh.core.task import Task, TaskState


# Exact-match keyword patterns (lowercase, stripped)
APPROVE_PATTERNS = frozenset({
    "approve", "승인", "승인해", "lgtm", "ok", "좋아", "ㅇㅇ",
})
REJECT_PATTERNS = frozenset({
    "reject", "다시 해", "다시해", "리젝",
})
STATUS_PATTERNS = frozenset({
    "status", "상태", "뭐해", "뭐하고 있어",
})
CANCEL_PATTERNS = frozenset({
    "cancel", "취소",
})


@dataclass
class PrefilterResult:
    """Result of keyword pre-filter matching."""

    action: Literal["approve", "reject", "status", "cancel"]
    target_task_id: str | None = None  # Resolved if exactly 1 task in REVIEW
    feedback: str | None = None  # For reject with inline feedback


def _find_single_review_task(active_tasks: list[Task]) -> str | None:
    """Find a single task in REVIEW state, or None if 0 or 2+."""
    review_tasks = [t for t in active_tasks if t.state == TaskState.REVIEW]
    if len(review_tasks) == 1:
        return review_tasks[0].id
    return None


def prefilter_message(text: str, active_tasks: list[Task]) -> PrefilterResult | None:
    """Check if text exactly matches a known command pattern.

    Returns PrefilterResult for unambiguous matches, None otherwise.
    Only exact matches (after strip/lower) are considered — partial matches
    like "approve this feature" return None and go to PM for classification.

    For approve/reject: if exactly 1 task is in REVIEW, auto-resolves target_task_id.
    If 0 or 2+ tasks in REVIEW, returns None to let PM disambiguate.
    """
    normalized = text.strip().lower()

    if not normalized:
        return None

    if normalized in APPROVE_PATTERNS:
        task_id = _find_single_review_task(active_tasks)
        if task_id is None and active_tasks:
            # Ambiguous — let PM handle
            return None
        return PrefilterResult(action="approve", target_task_id=task_id)

    if normalized in REJECT_PATTERNS:
        task_id = _find_single_review_task(active_tasks)
        if task_id is None and active_tasks:
            return None
        return PrefilterResult(action="reject", target_task_id=task_id)

    if normalized in STATUS_PATTERNS:
        return PrefilterResult(action="status")

    if normalized in CANCEL_PATTERNS:
        return PrefilterResult(action="cancel")

    return None
