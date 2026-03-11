import asyncio
import enum
from typing import Callable, Awaitable
import structlog

logger = structlog.get_logger("tmux.lifecycle")


class SessionState(enum.Enum):
    STARTING = "starting"
    ACTIVE = "active"
    DRAINING = "draining"
    ROTATING = "rotating"
    FAILED = "failed"


class SessionLifecycle:
    """Manages session rotation for a tmux PM process.

    Handles automatic session rotation when context fills up,
    with message buffering during rotation to prevent message loss.
    """

    STARTING_TIMEOUT = 60.0  # seconds
    DRAINING_TIMEOUT = 30.0
    ROTATING_TIMEOUT = 30.0
    MAX_START_RETRIES = 3
    DEFAULT_MAX_SESSION_HOURS = 4.0

    def __init__(
        self,
        pm_id: str,
        spawn_fn: Callable[[], Awaitable[bool]],    # spawns tmux session
        kill_fn: Callable[[], Awaitable[bool]],      # kills tmux session
        ping_fn: Callable[[], Awaitable[bool]],      # sends ping, returns True if pong received in outbox
        flush_fn: Callable[[], Awaitable[None]],     # flushes memory (L0→L1, rebuild CLAUDE.md)
        max_session_hours: float = 4.0,
    ) -> None:
        self._pm_id = pm_id
        self._spawn_fn = spawn_fn
        self._kill_fn = kill_fn
        self._ping_fn = ping_fn
        self._flush_fn = flush_fn
        self._max_session_hours = max_session_hours

        self._state = SessionState.STARTING
        self._message_buffer: list[tuple] = []  # buffered (msg_id, prompt) tuples during rotation
        self._session_start_time: float = 0.0
        self._in_flight_event = asyncio.Event()  # set when no in-flight messages
        self._in_flight_event.set()
        self._rotation_task: asyncio.Task | None = None

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def is_accepting(self) -> bool:
        """True if PM can accept new messages."""
        return self._state == SessionState.ACTIVE

    def buffer_message(self, msg_id: str, prompt: str) -> None:
        """Buffer a message during rotation. Replayed after ACTIVE."""
        self._message_buffer.append((msg_id, prompt))
        logger.info("message_buffered", pm_id=self._pm_id, msg_id=msg_id, buffer_size=len(self._message_buffer))

    def get_and_clear_buffer(self) -> list[tuple]:
        """Return buffered messages and clear the buffer."""
        messages = list(self._message_buffer)
        self._message_buffer.clear()
        return messages

    def mark_in_flight(self) -> None:
        """Mark that a message is being processed by PM."""
        self._in_flight_event.clear()

    def mark_response_received(self) -> None:
        """Mark that PM has responded to in-flight message."""
        self._in_flight_event.set()

    async def initialize(self) -> bool:
        """Run the STARTING state: spawn PM, wait for readiness.

        Returns True if PM became ACTIVE, False if FAILED.
        """
        self._state = SessionState.STARTING
        logger.info("session_starting", pm_id=self._pm_id)

        for attempt in range(1, self.MAX_START_RETRIES + 1):
            try:
                success = await self._spawn_fn()
                if not success:
                    logger.warning("spawn_failed", pm_id=self._pm_id, attempt=attempt)
                    continue

                # Wait for PM to become responsive
                try:
                    ready = await asyncio.wait_for(
                        self._ping_fn(),
                        timeout=self.STARTING_TIMEOUT,
                    )
                    if ready:
                        self._state = SessionState.ACTIVE
                        self._session_start_time = asyncio.get_event_loop().time()
                        logger.info("session_active", pm_id=self._pm_id, attempt=attempt)
                        return True
                except asyncio.TimeoutError:
                    logger.warning("ping_timeout", pm_id=self._pm_id, attempt=attempt)
                    await self._kill_fn()
            except Exception as e:
                logger.error("start_error", pm_id=self._pm_id, attempt=attempt, error=str(e))

        self._state = SessionState.FAILED
        logger.error("session_failed", pm_id=self._pm_id)
        return False

    async def trigger_rotation(self) -> bool:
        """Trigger session rotation: ACTIVE → DRAINING → ROTATING → STARTING.

        Returns True if rotation completed successfully.
        """
        if self._state != SessionState.ACTIVE:
            logger.warning("rotation_skipped", pm_id=self._pm_id, state=self._state.value)
            return False

        # DRAINING: wait for in-flight responses
        self._state = SessionState.DRAINING
        logger.info("session_draining", pm_id=self._pm_id)

        try:
            await asyncio.wait_for(
                self._in_flight_event.wait(),
                timeout=self.DRAINING_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("drain_timeout", pm_id=self._pm_id)

        # ROTATING: kill session, flush memory, spawn new
        self._state = SessionState.ROTATING
        logger.info("session_rotating", pm_id=self._pm_id)

        try:
            await asyncio.wait_for(self._kill_fn(), timeout=self.ROTATING_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("kill_timeout", pm_id=self._pm_id)

        try:
            await self._flush_fn()
        except Exception as e:
            logger.error("flush_error", pm_id=self._pm_id, error=str(e))

        # Back to STARTING
        return await self.initialize()

    def should_rotate(self) -> bool:
        """Check if session has exceeded max duration."""
        if self._state != SessionState.ACTIVE:
            return False
        elapsed = asyncio.get_event_loop().time() - self._session_start_time
        return elapsed > (self._max_session_hours * 3600)
