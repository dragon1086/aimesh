"""TmuxSession — thin async wrapper around tmux CLI commands."""

import asyncio

import structlog

logger = structlog.get_logger(__name__)


class TmuxSession:
    """Stateless async interface to tmux. All methods are static."""

    @staticmethod
    async def spawn(session_name: str, command: str, cwd: str | None = None) -> bool:
        """Create a detached tmux session running *command*.

        Args:
            session_name: Name for the new tmux session.
            command: Shell command to run inside the session.
            cwd: Optional working directory for the session.

        Returns:
            True if tmux exited with code 0, False otherwise.
        """
        args = ["tmux", "new-session", "-d", "-s", session_name]
        if cwd:
            args += ["-c", cwd]
        args.append(command)

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            if proc.returncode != 0:
                logger.error("tmux spawn failed", session=session_name, returncode=proc.returncode)
                return False
            return True
        except Exception as exc:
            logger.error("tmux spawn error", session=session_name, error=str(exc))
            return False

    @staticmethod
    async def send_keys(session_name: str, text: str) -> bool:
        """Send literal keystrokes to a tmux session, then press Enter.

        Uses ``-l`` flag so the text is sent literally without escape
        interpretation.

        Args:
            session_name: Target tmux session name.
            text: Text to send (sent literally).

        Returns:
            True if both tmux calls succeeded, False on any failure.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "tmux", "send-keys", "-t", session_name, "-l", "--", text,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            if proc.returncode != 0:
                logger.error(
                    "tmux send-keys (text) failed",
                    session=session_name,
                    returncode=proc.returncode,
                )
                return False

            proc2 = await asyncio.create_subprocess_exec(
                "tmux", "send-keys", "-t", session_name, "Enter",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc2.wait()
            if proc2.returncode != 0:
                logger.error(
                    "tmux send-keys (Enter) failed",
                    session=session_name,
                    returncode=proc2.returncode,
                )
                return False

            return True
        except Exception as exc:
            logger.error("tmux send-keys error", session=session_name, error=str(exc))
            return False

    @staticmethod
    async def is_alive(session_name: str) -> bool:
        """Check whether a tmux session exists.

        Args:
            session_name: Session name to probe.

        Returns:
            True if the session exists (tmux has-session exit code 0).
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "tmux", "has-session", "-t", session_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            return proc.returncode == 0
        except Exception as exc:
            logger.error("tmux is_alive error", session=session_name, error=str(exc))
            return False

    @staticmethod
    async def kill(session_name: str) -> bool:
        """Kill a tmux session.

        Args:
            session_name: Session to terminate.

        Returns:
            True on success, False on failure.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "tmux", "kill-session", "-t", session_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            if proc.returncode != 0:
                logger.error(
                    "tmux kill-session failed",
                    session=session_name,
                    returncode=proc.returncode,
                )
                return False
            return True
        except Exception as exc:
            logger.error("tmux kill error", session=session_name, error=str(exc))
            return False

    @staticmethod
    async def list_sessions() -> list[str]:
        """Return names of all active tmux sessions.

        Returns:
            List of session name strings, or an empty list if the tmux
            server is not running or any error occurs.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "tmux", "list-sessions", "-F", "#{session_name}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return []
            names = stdout.decode().strip().splitlines()
            return [n for n in names if n]
        except Exception as exc:
            logger.error("tmux list_sessions error", error=str(exc))
            return []
