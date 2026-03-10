"""Shared context store for AI Mesh agents."""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import structlog

logger = structlog.get_logger("context")


@dataclass
class ContextEntry:
    """A single context entry written by an agent."""

    key: str
    value: str
    agent_id: str
    task_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ContextStore:
    """Shared context store using SQLite for persistence and in-memory dict for fast access."""

    def __init__(self, db_path: str = "aimesh.db") -> None:
        self._db_path = db_path
        self._cache: dict[str, ContextEntry] = {}
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Create database tables and load existing context into cache."""
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS context (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                task_id TEXT,
                created_at TEXT NOT NULL
            )
        """)
        await self._db.commit()

        # Load existing entries into cache
        async with self._db.execute("SELECT * FROM context") as cursor:
            async for row in cursor:
                entry = ContextEntry(
                    key=row[0],
                    value=row[1],
                    agent_id=row[2],
                    task_id=row[3],
                    created_at=datetime.fromisoformat(row[4]),
                )
                self._cache[entry.key] = entry

        logger.info("context_initialized", entries=len(self._cache))

    async def write(self, key: str, value: str, agent_id: str, task_id: str | None = None) -> None:
        """Write a context entry (upsert)."""
        entry = ContextEntry(key=key, value=value, agent_id=agent_id, task_id=task_id)
        self._cache[key] = entry

        if self._db:
            await self._db.execute(
                """INSERT OR REPLACE INTO context (key, value, agent_id, task_id, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (key, value, agent_id, task_id, entry.created_at.isoformat()),
            )
            await self._db.commit()

        logger.info("context_written", key=key, agent_id=agent_id, task_id=task_id)

    async def read(self, key: str) -> str | None:
        """Read a context value by key."""
        entry = self._cache.get(key)
        return entry.value if entry else None

    async def query(self, prefix: str | None = None, agent_id: str | None = None,
                    task_id: str | None = None) -> list[ContextEntry]:
        """Query context entries by prefix, agent_id, or task_id."""
        results = []
        for entry in self._cache.values():
            if prefix and not entry.key.startswith(prefix):
                continue
            if agent_id and entry.agent_id != agent_id:
                continue
            if task_id and entry.task_id != task_id:
                continue
            results.append(entry)
        return results

    async def close(self) -> None:
        """Close database connection."""
        if self._db:
            await self._db.close()
            self._db = None
