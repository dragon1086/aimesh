"""Tests for the 3-layer MemoryManager."""
import pytest
from pathlib import Path

from aimesh.memory.manager import MemoryManager


IDENTITY = "# Test PM\n\n## Role\nYou are a test PM.\n"


@pytest.fixture
def mm(tmp_path):
    return MemoryManager(data_dir=tmp_path / "data/pm")


def test_initialize_creates_directory_structure(mm, tmp_path):
    mm.initialize_pm("alpha", IDENTITY)
    pm_dir = mm.pm_dir("alpha")
    assert (pm_dir / "identity.md").exists()
    assert (pm_dir / "knowledge.md").exists()
    assert (pm_dir / "episodes").is_dir()
    assert (pm_dir / "active_context.md").exists()


def test_initialize_writes_identity(mm):
    mm.initialize_pm("alpha", IDENTITY)
    content = (mm.pm_dir("alpha") / "identity.md").read_text(encoding="utf-8")
    assert content == IDENTITY


def test_build_claude_md_includes_layers(mm):
    mm.initialize_pm("alpha", IDENTITY)
    knowledge_path = mm.pm_dir("alpha") / "knowledge.md"
    knowledge_path.write_text("# Accumulated Knowledge\n\nDecision: use Python.", encoding="utf-8")

    result = mm.build_claude_md("alpha", "/tmp/outbox")

    assert "# Identity" in result
    assert "Test PM" in result
    assert "# Knowledge" in result
    assert "Decision: use Python." in result


def test_build_claude_md_includes_outbox_instructions(mm):
    mm.initialize_pm("alpha", IDENTITY)
    result = mm.build_claude_md("alpha", "/tmp/my_outbox")

    assert "/tmp/my_outbox" in result
    assert "response-001.json" in result
    assert "chat_response" in result


def test_build_claude_md_no_dynamic_state(mm):
    mm.initialize_pm("alpha", IDENTITY)
    result = mm.build_claude_md("alpha", "/tmp/outbox")

    assert "ACTIVE TASKS" not in result
    assert "AVAILABLE WORKERS" not in result


def test_append_l0(mm):
    mm.initialize_pm("alpha", IDENTITY)
    mm.append_l0("alpha", "Summary A")
    mm.append_l0("alpha", "Summary B")

    content = (mm.pm_dir("alpha") / "active_context.md").read_text(encoding="utf-8")
    assert "Summary A" in content
    assert "Summary B" in content


def test_flush_l0_to_l1(mm):
    mm.initialize_pm("alpha", IDENTITY)
    mm.append_l0("alpha", "Session data here")

    episode_path = mm.flush_l0_to_l1("alpha")

    assert episode_path is not None
    assert episode_path.exists()
    assert "Session data here" in episode_path.read_text(encoding="utf-8")

    # L0 should be cleared
    l0_content = (mm.pm_dir("alpha") / "active_context.md").read_text(encoding="utf-8")
    assert l0_content.strip() == ""


def test_flush_l0_to_l1_empty(mm):
    mm.initialize_pm("alpha", IDENTITY)
    result = mm.flush_l0_to_l1("alpha")
    assert result is None


def test_cleanup_old_episodes(mm):
    mm.initialize_pm("alpha", IDENTITY)
    episodes_dir = mm.pm_dir("alpha") / "episodes"

    # Create old episode files
    old_files = ["2020-01-01.md", "2020-06-15.md"]
    for name in old_files:
        (episodes_dir / name).write_text("old content", encoding="utf-8")

    # Create a recent episode (today)
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    recent = episodes_dir / f"{today}.md"
    recent.write_text("recent content", encoding="utf-8")

    removed = mm.cleanup_old_episodes("alpha", keep_days=7)

    assert removed == 2
    assert not (episodes_dir / "2020-01-01.md").exists()
    assert not (episodes_dir / "2020-06-15.md").exists()
    assert recent.exists()


def test_get_identity(mm):
    mm.initialize_pm("alpha", IDENTITY)
    result = mm.get_identity("alpha")
    assert result == IDENTITY


def test_get_identity_missing(mm):
    result = mm.get_identity("nonexistent")
    assert result == ""
