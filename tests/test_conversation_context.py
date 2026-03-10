"""Tests for ConversationContext (Phase 2a)."""

from datetime import datetime, timedelta, timezone

from aimesh.nl.context import ContextEntry, ConversationContext


def _make_entry(text: str, user_name: str = "user1", user_id: int = 1,
                is_bot: bool = False, timestamp: datetime | None = None) -> ContextEntry:
    return ContextEntry(
        timestamp=timestamp or datetime.now(timezone.utc),
        user_id=user_id,
        user_name=user_name,
        text=text,
        is_bot=is_bot,
    )


def test_add_and_get_recent():
    """Basic add and retrieve."""
    ctx = ConversationContext()
    ctx.add(1, _make_entry("hello"))
    ctx.add(1, _make_entry("world"))

    entries = ctx.get_recent(1)
    assert len(entries) == 2
    assert entries[0].text == "hello"
    assert entries[1].text == "world"


def test_bounding_at_max_entries():
    """Adding > MAX_ENTRIES keeps only the last MAX_ENTRIES."""
    ctx = ConversationContext()
    for i in range(25):
        ctx.add(1, _make_entry(f"msg-{i}"))

    entries = ctx.get_recent(1, limit=100)
    assert len(entries) <= ConversationContext.MAX_ENTRIES
    # Should have the last 20 messages
    assert entries[0].text == "msg-5"
    assert entries[-1].text == "msg-24"


def test_pruning_old_entries():
    """Entries older than MAX_AGE_HOURS are pruned on access."""
    ctx = ConversationContext()
    old_time = datetime.now(timezone.utc) - timedelta(hours=25)
    recent_time = datetime.now(timezone.utc)

    ctx.add(1, _make_entry("old message", timestamp=old_time))
    ctx.add(1, _make_entry("new message", timestamp=recent_time))

    entries = ctx.get_recent(1)
    assert len(entries) == 1
    assert entries[0].text == "new message"


def test_chat_isolation():
    """Entries for different chat IDs are isolated."""
    ctx = ConversationContext()
    ctx.add(1, _make_entry("chat1 msg"))
    ctx.add(2, _make_entry("chat2 msg"))

    assert len(ctx.get_recent(1)) == 1
    assert len(ctx.get_recent(2)) == 1
    assert ctx.get_recent(1)[0].text == "chat1 msg"
    assert ctx.get_recent(2)[0].text == "chat2 msg"


def test_format_for_pm_empty():
    """Empty context returns empty string."""
    ctx = ConversationContext()
    assert ctx.format_for_pm(999) == ""


def test_format_for_pm_output():
    """Format includes user names and text."""
    ctx = ConversationContext()
    ctx.add(1, _make_entry("hello", user_name="Alice"))
    ctx.add(1, _make_entry("bot reply", user_name="bot", is_bot=True))

    output = ctx.format_for_pm(1)
    assert "[Alice]" in output
    assert "[BOT]" in output
    assert "hello" in output
    assert "bot reply" in output


def test_get_recent_limit():
    """Limit parameter restricts output."""
    ctx = ConversationContext()
    for i in range(10):
        ctx.add(1, _make_entry(f"msg-{i}"))

    entries = ctx.get_recent(1, limit=3)
    assert len(entries) == 3
    assert entries[0].text == "msg-7"
