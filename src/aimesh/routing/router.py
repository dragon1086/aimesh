"""PM Router — selects the best PM for each message."""

from dataclasses import dataclass
import re
import structlog

logger = structlog.get_logger("routing")


@dataclass
class PMInfo:
    """Information about an available PM for routing decisions."""
    pm_id: str
    identity_keywords: list[str]  # extracted from identity.md
    active_task_count: int
    owned_task_ids: list[str]  # task IDs this PM is responsible for


class PMRouter:
    """Routes messages to the best PM based on routing precedence.

    Routing order:
    1. Explicit @mention of PM name → route to that PM
    2. Task context: message references an active task → route to owning PM
    3. Least-busy PM (fewest active tasks) as fallback
    """

    @staticmethod
    def select_pm(
        message_text: str,
        mention: str | None,
        active_tasks: list[dict],  # [{"id": "task-1", "title": "...", "pm_id": "..."}]
        available_pms: list[PMInfo],
    ) -> str | None:
        """Select the best PM for a message.

        Args:
            message_text: The user's message text.
            mention: Extracted @mention from message (e.g., "marketing-pm"), or None.
            active_tasks: List of active task dicts with id, title, pm_id fields.
            available_pms: List of PMInfo objects for available PMs.

        Returns:
            pm_id of the selected PM, or None if no PMs available.
        """
        if not available_pms:
            return None

        # 1. Explicit @mention routing
        if mention:
            mention_lower = mention.lower()
            for pm in available_pms:
                if pm.pm_id.lower() == mention_lower:
                    logger.info("route_by_mention", pm_id=pm.pm_id, mention=mention)
                    return pm.pm_id

        # 2. Task context routing — check if message references a task ID or title
        if active_tasks:
            for task in active_tasks:
                task_id = task.get("id", "")
                task_title = task.get("title", "")
                task_pm = task.get("pm_id", "")

                # Check if task ID or title appears in message
                if task_id and task_id in message_text:
                    if task_pm:
                        logger.info("route_by_task_id", pm_id=task_pm, task_id=task_id)
                        return task_pm

                if task_title and len(task_title) > 3:
                    # Fuzzy: check if significant words from title appear in message
                    title_words = set(task_title.lower().split())
                    msg_words = set(message_text.lower().split())
                    overlap = title_words & msg_words
                    # If more than half the title words appear, route to owning PM
                    if len(overlap) > len(title_words) / 2:
                        if task_pm:
                            logger.info("route_by_task_context", pm_id=task_pm, task_title=task_title)
                            return task_pm

        # 3. Fallback: least-busy PM (fewest active tasks)
        least_busy = min(available_pms, key=lambda p: p.active_task_count)
        logger.info("route_by_load_balance", pm_id=least_busy.pm_id, task_count=least_busy.active_task_count)
        return least_busy.pm_id

    @staticmethod
    def extract_mention(text: str) -> str | None:
        """Extract @mention from message text.

        Returns the mentioned name (without @) or None.
        """
        match = re.search(r"@(\S+)", text)
        if match:
            return match.group(1)
        return None
