"""SQLite-based message dedup for multi-team shared group chats.

Ensures only one bot responds to non-@mentioned messages.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class MessageDedup:
    """Atomic message claiming via SQLite for multi-bot dedup.

    Uses INSERT OR IGNORE on message_id PRIMARY KEY for atomic claims.
    Safe for concurrent access from multiple processes (SQLite WAL mode).
    """

    def __init__(self, db_path: str = "data/message_dedup.db") -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS claimed_messages (
                message_id INTEGER PRIMARY KEY,
                bot_username TEXT NOT NULL,
                claimed_at TEXT NOT NULL
            )
        """)
        self._conn.commit()

    def try_claim(self, message_id: int, bot_username: str) -> bool:
        """Try to claim a message. Returns True if this bot claimed it, False if already claimed."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            self._conn.execute(
                "INSERT OR IGNORE INTO claimed_messages (message_id, bot_username, claimed_at) "
                "VALUES (?, ?, ?)",
                (message_id, bot_username, now),
            )
            self._conn.commit()

            # Check if we own the claim
            row = self._conn.execute(
                "SELECT bot_username FROM claimed_messages WHERE message_id = ?",
                (message_id,),
            ).fetchone()
            return row is not None and row[0] == bot_username
        except sqlite3.Error:
            return False

    def release(self, message_id: int) -> None:
        """Release a claim (e.g., message not relevant to this team)."""
        self._conn.execute(
            "DELETE FROM claimed_messages WHERE message_id = ?",
            (message_id,),
        )
        self._conn.commit()

    def is_claimed(self, message_id: int) -> bool:
        """Check if a message has been claimed by any bot."""
        row = self._conn.execute(
            "SELECT 1 FROM claimed_messages WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        return row is not None

    def cleanup_old(self, max_age_hours: int = 24) -> int:
        """Remove claims older than max_age_hours. Returns count removed."""
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
        cursor = self._conn.execute(
            "DELETE FROM claimed_messages WHERE claimed_at < ?",
            (cutoff,),
        )
        self._conn.commit()
        return cursor.rowcount

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
