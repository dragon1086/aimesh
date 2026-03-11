import asyncio
from pathlib import Path
from typing import Callable, Awaitable
import structlog
from aimesh.tmux.session import TmuxSession
from aimesh.tmux.protocol import poll_outbox

logger = structlog.get_logger("tmux.bridge")


class HybridBridge:
    """Hybrid IPC bridge: tmux send-keys for input, file outbox for output.

    The orchestrator drives timing — Claude Code processes prompts as normal turns.
    PM writes structured JSON responses to file outbox using its native Write tool.
    """

    def __init__(self, session: TmuxSession, data_dir: str | Path) -> None:
        self._session = session
        self._data_dir = Path(data_dir)
        self._polling_task: asyncio.Task | None = None
        self._poll_interval: float = 1.0  # seconds

    def outbox_dir(self, pm_id: str) -> Path:
        return self._data_dir / pm_id / "outbox"

    async def send_to_pm(self, pm_id: str, session_name: str, prompt_text: str) -> str:
        """Send a structured prompt to a PM via tmux send-keys.

        Returns a message ID for correlation.
        """
        import uuid
        msg_id = str(uuid.uuid4())
        # The prompt includes the msg_id so PM can use it in reply_to
        full_prompt = prompt_text.replace("{MSG_ID}", msg_id)
        await self._session.send_keys(session_name, full_prompt)
        logger.info("sent_to_pm", pm_id=pm_id, msg_id=msg_id)
        return msg_id

    async def start_outbox_polling(
        self, pm_ids: list[str], callback: Callable[[str, dict], Awaitable[None]]
    ) -> None:
        """Start async polling loop for all PM outbox directories."""
        self._polling_task = asyncio.create_task(
            self._poll_loop(pm_ids, callback)
        )

    async def _poll_loop(
        self, pm_ids: list[str], callback: Callable[[str, dict], Awaitable[None]]
    ) -> None:
        """Poll all PM outboxes every poll_interval seconds."""
        while True:
            try:
                for pm_id in pm_ids:
                    outbox = self.outbox_dir(pm_id)
                    if not outbox.exists():
                        continue
                    responses = poll_outbox(outbox)
                    for response in responses:
                        try:
                            await callback(pm_id, response)
                        except Exception as e:
                            logger.error("callback_error", pm_id=pm_id, error=str(e))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("poll_error", error=str(e))
            await asyncio.sleep(self._poll_interval)

    async def stop_outbox_polling(self) -> None:
        """Cancel the polling loop cleanly."""
        if self._polling_task and not self._polling_task.done():
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            self._polling_task = None
            logger.info("outbox_polling_stopped")

    @staticmethod
    def format_prompt(
        msg_id: str,
        msg_type: str,
        user_message: str,
        active_tasks: str = "",
        available_workers: str = "",
        conversation_context: str = "",
        outbox_path: str = "",
    ) -> str:
        """Build structured prompt for send-keys injection."""
        parts = [f"[AIMESH-MSG id={msg_id} type={msg_type}]", ""]
        if active_tasks:
            parts.extend(["ACTIVE TASKS:", active_tasks, ""])
        if available_workers:
            parts.extend(["AVAILABLE WORKERS:", available_workers, ""])
        if conversation_context:
            parts.extend(["CONVERSATION CONTEXT:", conversation_context, ""])
        parts.extend([f"NEW MESSAGE:", user_message, ""])
        if outbox_path:
            parts.extend([
                "---",
                f'Respond by writing a JSON file to {outbox_path} using the Write tool.',
                f'Use format: {{"id": "response-id", "reply_to": "{msg_id}", "type": "chat_response", "content": "your response", "structured_data": {{}}, "timestamp": "ISO8601"}}',
            ])
        return "\n".join(parts)
