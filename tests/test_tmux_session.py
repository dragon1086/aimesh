"""Tests for TmuxSession async wrapper."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from aimesh.tmux.session import TmuxSession


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_proc(returncode: int = 0, stdout: bytes = b"") -> MagicMock:
    """Return a mock subprocess object with configurable returncode and stdout."""
    proc = MagicMock()
    proc.returncode = returncode
    # wait() is awaited, communicate() is awaited
    proc.wait = AsyncMock(return_value=None)
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    return proc


# ---------------------------------------------------------------------------
# spawn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_success():
    proc = make_proc(returncode=0)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)) as mock_exec:
        result = await TmuxSession.spawn("mysession", "python worker.py")

    assert result is True
    mock_exec.assert_called_once_with(
        "tmux", "new-session", "-d", "-s", "mysession", "python worker.py",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


@pytest.mark.asyncio
async def test_spawn_success_with_cwd():
    proc = make_proc(returncode=0)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)) as mock_exec:
        result = await TmuxSession.spawn("mysession", "python worker.py", cwd="/tmp/work")

    assert result is True
    mock_exec.assert_called_once_with(
        "tmux", "new-session", "-d", "-s", "mysession", "-c", "/tmp/work", "python worker.py",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


@pytest.mark.asyncio
async def test_spawn_failure():
    proc = make_proc(returncode=1)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        result = await TmuxSession.spawn("mysession", "bad-command")

    assert result is False


# ---------------------------------------------------------------------------
# send_keys
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_keys_success():
    proc = make_proc(returncode=0)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)) as mock_exec:
        result = await TmuxSession.send_keys("mysession", "hello world")

    assert result is True
    assert mock_exec.call_count == 2
    # First call sends text with -l flag
    first_call = mock_exec.call_args_list[0]
    assert first_call == call(
        "tmux", "send-keys", "-t", "mysession", "-l", "--", "hello world",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # Second call sends Enter
    second_call = mock_exec.call_args_list[1]
    assert second_call == call(
        "tmux", "send-keys", "-t", "mysession", "Enter",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


@pytest.mark.asyncio
async def test_send_keys_first_call_fails():
    proc_fail = make_proc(returncode=1)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc_fail)) as mock_exec:
        result = await TmuxSession.send_keys("mysession", "hello")

    assert result is False
    # Should not proceed to the Enter call after the first failure
    assert mock_exec.call_count == 1


# ---------------------------------------------------------------------------
# is_alive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_alive_true():
    proc = make_proc(returncode=0)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)) as mock_exec:
        result = await TmuxSession.is_alive("mysession")

    assert result is True
    mock_exec.assert_called_once_with(
        "tmux", "has-session", "-t", "mysession",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


@pytest.mark.asyncio
async def test_is_alive_false():
    proc = make_proc(returncode=1)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        result = await TmuxSession.is_alive("mysession")

    assert result is False


# ---------------------------------------------------------------------------
# kill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_success():
    proc = make_proc(returncode=0)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)) as mock_exec:
        result = await TmuxSession.kill("mysession")

    assert result is True
    mock_exec.assert_called_once_with(
        "tmux", "kill-session", "-t", "mysession",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


@pytest.mark.asyncio
async def test_kill_failure():
    proc = make_proc(returncode=1)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        result = await TmuxSession.kill("nosuchsession")

    assert result is False


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sessions():
    proc = make_proc(returncode=0, stdout=b"sess1\nsess2\n")
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)) as mock_exec:
        result = await TmuxSession.list_sessions()

    assert result == ["sess1", "sess2"]
    mock_exec.assert_called_once_with(
        "tmux", "list-sessions", "-F", "#{session_name}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


@pytest.mark.asyncio
async def test_list_sessions_no_server():
    proc = make_proc(returncode=1, stdout=b"")
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        result = await TmuxSession.list_sessions()

    assert result == []


@pytest.mark.asyncio
async def test_list_sessions_exception():
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=OSError("no tmux"))):
        result = await TmuxSession.list_sessions()

    assert result == []
