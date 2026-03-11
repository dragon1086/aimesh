"""Tests for PMRouter routing logic."""

import pytest
from aimesh.routing.router import PMInfo, PMRouter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_pm(pm_id: str, task_count: int = 0, owned: list[str] | None = None) -> PMInfo:
    return PMInfo(
        pm_id=pm_id,
        identity_keywords=[],
        active_task_count=task_count,
        owned_task_ids=owned or [],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_mention_routing():
    """@marketing-pm mention routes directly to that PM."""
    pms = [make_pm("dev-pm"), make_pm("marketing-pm")]
    result = PMRouter.select_pm(
        message_text="@marketing-pm can you check the campaign?",
        mention="marketing-pm",
        active_tasks=[],
        available_pms=pms,
    )
    assert result == "marketing-pm"


def test_task_context_routing():
    """Message containing task ID routes to the PM that owns that task."""
    pms = [make_pm("dev-pm"), make_pm("design-pm")]
    tasks = [{"id": "task-123", "title": "Login page", "pm_id": "dev-pm"}]
    result = PMRouter.select_pm(
        message_text="Any update on task-123?",
        mention=None,
        active_tasks=tasks,
        available_pms=pms,
    )
    assert result == "dev-pm"


def test_least_busy_fallback():
    """With no mention and no task context, routes to PM with fewest tasks."""
    pms = [make_pm("pm1", task_count=3), make_pm("pm2", task_count=1)]
    result = PMRouter.select_pm(
        message_text="General question here",
        mention=None,
        active_tasks=[],
        available_pms=pms,
    )
    assert result == "pm2"


def test_no_pms_returns_none():
    """Returns None when no PMs are available."""
    result = PMRouter.select_pm(
        message_text="hello",
        mention=None,
        active_tasks=[],
        available_pms=[],
    )
    assert result is None


def test_multiple_pms_tiebreak():
    """Two PMs with identical task counts — first one (min) is returned deterministically."""
    pms = [make_pm("alpha-pm", task_count=0), make_pm("beta-pm", task_count=0)]
    result = PMRouter.select_pm(
        message_text="something neutral",
        mention=None,
        active_tasks=[],
        available_pms=pms,
    )
    # min() returns the first minimum element in insertion order
    assert result == "alpha-pm"


def test_extract_mention():
    """@team1_bot status → returns 'team1_bot'."""
    result = PMRouter.extract_mention("@team1_bot status")
    assert result == "team1_bot"


def test_extract_mention_none():
    """Message with no @ returns None."""
    result = PMRouter.extract_mention("no mention here")
    assert result is None


def test_task_title_matching():
    """Message 'auth module progress?' matches task titled 'Build auth module'."""
    pms = [make_pm("backend-pm"), make_pm("frontend-pm")]
    tasks = [{"id": "task-42", "title": "Build auth module", "pm_id": "backend-pm"}]
    result = PMRouter.select_pm(
        message_text="auth module progress?",
        mention=None,
        active_tasks=tasks,
        available_pms=pms,
    )
    assert result == "backend-pm"
