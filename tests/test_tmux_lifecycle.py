"""Tests for SessionLifecycle state machine."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from aimesh.tmux.lifecycle import SessionLifecycle, SessionState


def make_lifecycle(
    spawn_fn=None,
    kill_fn=None,
    ping_fn=None,
    flush_fn=None,
    max_session_hours=4.0,
) -> SessionLifecycle:
    return SessionLifecycle(
        pm_id="test-pm",
        spawn_fn=spawn_fn or AsyncMock(return_value=True),
        kill_fn=kill_fn or AsyncMock(return_value=True),
        ping_fn=ping_fn or AsyncMock(return_value=True),
        flush_fn=flush_fn or AsyncMock(return_value=None),
        max_session_hours=max_session_hours,
    )


# ---------------------------------------------------------------------------
# 1. Initial state
# ---------------------------------------------------------------------------

def test_initial_state_is_starting():
    lc = make_lifecycle()
    assert lc.state == SessionState.STARTING


# ---------------------------------------------------------------------------
# 2. initialize() success path
# ---------------------------------------------------------------------------

async def test_initialize_success():
    spawn = AsyncMock(return_value=True)
    ping = AsyncMock(return_value=True)
    lc = make_lifecycle(spawn_fn=spawn, ping_fn=ping)

    result = await lc.initialize()

    assert result is True
    assert lc.state == SessionState.ACTIVE
    spawn.assert_awaited_once()
    ping.assert_awaited_once()


# ---------------------------------------------------------------------------
# 3. spawn fails twice, succeeds on third attempt
# ---------------------------------------------------------------------------

async def test_initialize_spawn_failure_retries():
    spawn = AsyncMock(side_effect=[False, False, True])
    ping = AsyncMock(return_value=True)
    lc = make_lifecycle(spawn_fn=spawn, ping_fn=ping)

    result = await lc.initialize()

    assert result is True
    assert lc.state == SessionState.ACTIVE
    assert spawn.await_count == 3
    ping.assert_awaited_once()


# ---------------------------------------------------------------------------
# 4. All retries exhausted → FAILED
# ---------------------------------------------------------------------------

async def test_initialize_all_retries_fail():
    spawn = AsyncMock(return_value=False)
    lc = make_lifecycle(spawn_fn=spawn)

    result = await lc.initialize()

    assert result is False
    assert lc.state == SessionState.FAILED
    assert spawn.await_count == SessionLifecycle.MAX_START_RETRIES


# ---------------------------------------------------------------------------
# 5. Full rotation cycle
# ---------------------------------------------------------------------------

async def test_trigger_rotation_full_cycle():
    spawn = AsyncMock(return_value=True)
    kill = AsyncMock(return_value=True)
    ping = AsyncMock(return_value=True)
    flush = AsyncMock(return_value=None)

    lc = make_lifecycle(spawn_fn=spawn, kill_fn=kill, ping_fn=ping, flush_fn=flush)

    # First initialize to reach ACTIVE
    await lc.initialize()
    assert lc.state == SessionState.ACTIVE

    result = await lc.trigger_rotation()

    assert result is True
    assert lc.state == SessionState.ACTIVE
    # kill called once during rotation, spawn called twice (init + re-init)
    kill.assert_awaited_once()
    flush.assert_awaited_once()
    assert spawn.await_count == 2


# ---------------------------------------------------------------------------
# 6. Message buffering during rotation
# ---------------------------------------------------------------------------

async def test_message_buffering_during_rotation():
    lc = make_lifecycle()

    lc.buffer_message("msg-1", "hello")
    lc.buffer_message("msg-2", "world")

    buffered = lc.get_and_clear_buffer()

    assert buffered == [("msg-1", "hello"), ("msg-2", "world")]
    # Buffer cleared after retrieval
    assert lc.get_and_clear_buffer() == []


# ---------------------------------------------------------------------------
# 7. is_accepting only in ACTIVE state
# ---------------------------------------------------------------------------

async def test_is_accepting_only_when_active():
    lc = make_lifecycle()

    # STARTING
    assert lc.state == SessionState.STARTING
    assert lc.is_accepting is False

    await lc.initialize()
    assert lc.state == SessionState.ACTIVE
    assert lc.is_accepting is True

    # Manually set other states
    lc._state = SessionState.DRAINING
    assert lc.is_accepting is False

    lc._state = SessionState.ROTATING
    assert lc.is_accepting is False

    lc._state = SessionState.FAILED
    assert lc.is_accepting is False


# ---------------------------------------------------------------------------
# 8. should_rotate after max hours
# ---------------------------------------------------------------------------

async def test_should_rotate_after_max_hours():
    lc = make_lifecycle(max_session_hours=1.0)
    await lc.initialize()
    assert lc.state == SessionState.ACTIVE

    # Backdate session start by more than 1 hour
    loop_time = asyncio.get_event_loop().time()
    lc._session_start_time = loop_time - 3601.0

    assert lc.should_rotate() is True


async def test_should_rotate_false_before_max_hours():
    lc = make_lifecycle(max_session_hours=4.0)
    await lc.initialize()

    # Just started: should not rotate
    assert lc.should_rotate() is False


async def test_should_rotate_false_when_not_active():
    lc = make_lifecycle()
    # Still STARTING, start time is 0
    lc._session_start_time = asyncio.get_event_loop().time() - 99999.0
    assert lc.should_rotate() is False


# ---------------------------------------------------------------------------
# 9. DRAINING waits for in-flight message, then proceeds
# ---------------------------------------------------------------------------

async def test_drain_waits_for_in_flight():
    spawn = AsyncMock(return_value=True)
    kill = AsyncMock(return_value=True)
    ping = AsyncMock(return_value=True)
    flush = AsyncMock(return_value=None)

    lc = make_lifecycle(spawn_fn=spawn, kill_fn=kill, ping_fn=ping, flush_fn=flush)
    await lc.initialize()

    # Simulate an in-flight message
    lc.mark_in_flight()
    assert not lc._in_flight_event.is_set()

    # Schedule the response acknowledgement after a short delay
    async def respond_after_delay():
        await asyncio.sleep(0.05)
        lc.mark_response_received()

    asyncio.create_task(respond_after_delay())

    result = await lc.trigger_rotation()

    assert result is True
    assert lc.state == SessionState.ACTIVE
    # in-flight event should be set again after rotation
    assert lc._in_flight_event.is_set()
