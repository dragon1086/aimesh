from pathlib import Path
from datetime import datetime, timezone, timedelta
import structlog

logger = structlog.get_logger("memory")


class MemoryManager:
    """Manages 3-layer memory for PM agents.

    Layer structure:
    - L3 (Identity): data/pm/{pm_id}/identity.md — permanent, set by admin
    - L2 (Knowledge): data/pm/{pm_id}/knowledge.md — accumulated facts/decisions
    - L1 (Episodes): data/pm/{pm_id}/episodes/YYYY-MM-DD.md — daily rolling summaries
    - L0 (Active Context): data/pm/{pm_id}/active_context.md — current session state
    """

    MAX_KNOWLEDGE_CHARS = 8000  # ~2k tokens cap for L2
    EPISODE_KEEP_DAYS = 7

    def __init__(self, data_dir: str | Path = "data/pm") -> None:
        self._data_dir = Path(data_dir)

    def pm_dir(self, pm_id: str) -> Path:
        return self._data_dir / pm_id

    def initialize_pm(self, pm_id: str, identity_content: str) -> None:
        """Create directory structure and L3 identity file."""
        pm_path = self.pm_dir(pm_id)
        pm_path.mkdir(parents=True, exist_ok=True)
        (pm_path / "episodes").mkdir(exist_ok=True)

        # L3 - Identity (permanent)
        identity_path = pm_path / "identity.md"
        identity_path.write_text(identity_content, encoding="utf-8")

        # L2 - Knowledge (starts empty)
        knowledge_path = pm_path / "knowledge.md"
        if not knowledge_path.exists():
            knowledge_path.write_text("# Accumulated Knowledge\n\n", encoding="utf-8")

        # L0 - Active Context (starts empty)
        context_path = pm_path / "active_context.md"
        if not context_path.exists():
            context_path.write_text("", encoding="utf-8")

        logger.info("pm_memory_initialized", pm_id=pm_id)

    def build_claude_md(self, pm_id: str, outbox_path: str) -> str:
        """Compose CLAUDE.md from L3 + L2 + latest L1 + outbox instructions.

        NOTE: Does NOT include dynamic state (active tasks, workers).
        Dynamic context is injected per-message via send-keys prompts.
        """
        pm_path = self.pm_dir(pm_id)
        parts = []

        # L3 Identity
        identity_file = pm_path / "identity.md"
        if identity_file.exists():
            parts.append("# Identity\n")
            parts.append(identity_file.read_text(encoding="utf-8").strip())
            parts.append("")

        # L2 Knowledge
        knowledge_file = pm_path / "knowledge.md"
        if knowledge_file.exists():
            content = knowledge_file.read_text(encoding="utf-8").strip()
            if content and content != "# Accumulated Knowledge":
                parts.append("# Knowledge\n")
                parts.append(content)
                parts.append("")

        # Latest L1 Episode
        episodes_dir = pm_path / "episodes"
        if episodes_dir.exists():
            episode_files = sorted(episodes_dir.glob("*.md"), reverse=True)
            if episode_files:
                latest = episode_files[0]
                content = latest.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(f"# Recent Context ({latest.stem})\n")
                    parts.append(content)
                    parts.append("")

        # Outbox instructions (static)
        parts.append("# Response Protocol\n")
        parts.append(f"When you need to respond, write a JSON file to {outbox_path} using the Write tool.")
        parts.append("File name: use a unique name like response-001.json\n")
        parts.append("Required JSON format:")
        parts.append('{"id": "unique-id", "reply_to": "msg-id-from-prompt", "type": "chat_response", "content": "your response", "structured_data": {}, "timestamp": "ISO8601"}')
        parts.append("")

        return "\n".join(parts)

    def append_l0(self, pm_id: str, summary: str) -> None:
        """Append an interaction summary to L0 active context."""
        context_path = self.pm_dir(pm_id) / "active_context.md"
        with open(context_path, "a", encoding="utf-8") as f:
            f.write(f"\n{summary}\n")

    def flush_l0_to_l1(self, pm_id: str) -> Path | None:
        """Move L0 content to a dated L1 episode file. Clear L0.

        Returns path of created episode file, or None if L0 was empty.
        """
        pm_path = self.pm_dir(pm_id)
        l0_path = pm_path / "active_context.md"

        content = l0_path.read_text(encoding="utf-8").strip() if l0_path.exists() else ""
        if not content:
            return None

        # Write to dated episode file
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        episodes_dir = pm_path / "episodes"
        episodes_dir.mkdir(exist_ok=True)
        episode_path = episodes_dir / f"{today}.md"

        # Append if episode file already exists for today
        if episode_path.exists():
            existing = episode_path.read_text(encoding="utf-8")
            episode_path.write_text(f"{existing}\n\n---\n\n{content}", encoding="utf-8")
        else:
            episode_path.write_text(content, encoding="utf-8")

        # Clear L0
        l0_path.write_text("", encoding="utf-8")

        logger.info("l0_flushed_to_l1", pm_id=pm_id, episode=episode_path.name)
        return episode_path

    def cleanup_old_episodes(self, pm_id: str, keep_days: int = 7) -> int:
        """Remove episode files older than keep_days. Returns count removed."""
        episodes_dir = self.pm_dir(pm_id) / "episodes"
        if not episodes_dir.exists():
            return 0

        cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        removed = 0

        for episode_file in episodes_dir.glob("*.md"):
            # Filename is YYYY-MM-DD.md
            if episode_file.stem < cutoff_str:
                episode_file.unlink()
                removed += 1

        if removed:
            logger.info("episodes_cleaned", pm_id=pm_id, removed=removed)
        return removed

    def get_identity(self, pm_id: str) -> str:
        """Read L3 identity content."""
        identity_path = self.pm_dir(pm_id) / "identity.md"
        if identity_path.exists():
            return identity_path.read_text(encoding="utf-8")
        return ""
