"""Conversation context manager for multi-turn NL interactions."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


@dataclass
class ContextEntry:
    """A single entry in the conversation context."""

    timestamp: datetime
    user_id: int
    user_name: str
    text: str
    is_bot: bool = False


class ConversationContext:
    """Bounded sliding window of messages per chat for PM context.

    Provides formatted context strings for the PM's intent classification prompt.
    Automatically prunes entries older than MAX_AGE_HOURS on access.
    """

    MAX_ENTRIES = 20  # Per chat
    MAX_AGE_HOURS = 24

    def __init__(self) -> None:
        self._chats: dict[int, list[ContextEntry]] = {}

    def add(self, chat_id: int, entry: ContextEntry) -> None:
        """Add an entry to the context for a chat."""
        if chat_id not in self._chats:
            self._chats[chat_id] = []

        self._chats[chat_id].append(entry)

        # Bound to MAX_ENTRIES
        if len(self._chats[chat_id]) > self.MAX_ENTRIES:
            self._chats[chat_id] = self._chats[chat_id][-self.MAX_ENTRIES:]

    def get_recent(self, chat_id: int, limit: int = 10) -> list[ContextEntry]:
        """Get recent entries for a chat, pruning old ones first."""
        self._prune(chat_id)
        entries = self._chats.get(chat_id, [])
        return entries[-limit:]

    def format_for_pm(self, chat_id: int) -> str:
        """Format recent context as a string for the PM's prompt."""
        entries = self.get_recent(chat_id)
        if not entries:
            return ""

        lines = []
        for entry in entries:
            prefix = "[BOT]" if entry.is_bot else f"[{entry.user_name}]"
            time_str = entry.timestamp.strftime("%H:%M")
            lines.append(f"{time_str} {prefix} {entry.text}")

        return "\n".join(lines)

    def _prune(self, chat_id: int) -> None:
        """Remove entries older than MAX_AGE_HOURS."""
        if chat_id not in self._chats:
            return

        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.MAX_AGE_HOURS)
        self._chats[chat_id] = [
            e for e in self._chats[chat_id]
            if e.timestamp > cutoff
        ]
