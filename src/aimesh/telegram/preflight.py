"""Pre-flight checks for AI Mesh setup wizard."""

import asyncio
import shutil
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger("preflight")


@dataclass
class CheckResult:
    """Result of a single pre-flight check."""
    name: str
    passed: bool
    version: str = ""
    details: str = ""
    critical: bool = False  # If True, blocks setup wizard


@dataclass
class PreflightResult:
    """Aggregate result of all pre-flight checks."""
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def critical_passed(self) -> bool:
        """All critical checks passed."""
        return all(c.passed for c in self.checks if c.critical)

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def format_display(self) -> str:
        """Format results for Telegram display."""
        lines = ["*Pre-flight Checks:*\n"]
        for check in self.checks:
            icon = "PASS" if check.passed else ("FAIL" if check.critical else "WARN")
            version_str = f" (v{check.version})" if check.version else ""
            lines.append(f"[{icon}] {check.name}{version_str}")
            if not check.passed and check.details:
                lines.append(f"  {check.details}")

        if self.critical_passed:
            lines.append("\nReady to proceed with setup.")
        else:
            lines.append("\nCritical checks failed. Please fix before continuing.")

        return "\n".join(lines)


async def _run_command(cmd: str) -> tuple[bool, str]:
    """Run a shell command and return (success, output)."""
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        output = (stdout or b"").decode().strip()
        return proc.returncode == 0, output
    except asyncio.TimeoutError:
        return False, "Command timed out"
    except Exception as e:
        return False, str(e)


async def check_claude_cli() -> CheckResult:
    """Check if Claude Code CLI is installed."""
    # Check if claude is on PATH
    claude_path = shutil.which("claude")
    if not claude_path:
        return CheckResult(
            name="Claude Code CLI",
            passed=False,
            details="Not found. Install: npm install -g @anthropic-ai/claude-code",
            critical=True,
        )

    success, output = await _run_command("claude --version")
    if success:
        # Extract version from output
        version = output.split("\n")[0].strip()
        return CheckResult(name="Claude Code CLI", passed=True, version=version)

    return CheckResult(
        name="Claude Code CLI",
        passed=False,
        details=f"Found at {claude_path} but --version failed: {output[:100]}",
        critical=True,
    )


async def check_codex_cli() -> CheckResult:
    """Check if Codex CLI is installed (optional)."""
    codex_path = shutil.which("codex")
    if not codex_path:
        return CheckResult(
            name="Codex CLI",
            passed=False,
            details="Not found (optional — Codex support is Phase 2)",
            critical=False,
        )

    success, output = await _run_command("codex --version")
    version = output.split("\n")[0].strip() if success else ""
    return CheckResult(name="Codex CLI", passed=success, version=version, critical=False)


async def check_mcp_plugins() -> CheckResult:
    """List installed MCP plugins/servers."""
    # Try `claude mcp list` or similar
    success, output = await _run_command("claude mcp list 2>/dev/null || echo 'No MCP plugins found'")

    if success and output and "No MCP" not in output:
        # Count plugins
        plugin_lines = [l for l in output.strip().split("\n") if l.strip()]
        return CheckResult(
            name="MCP Plugins",
            passed=True,
            details=f"{len(plugin_lines)} plugin(s) found",
            version=str(len(plugin_lines)),
        )

    return CheckResult(
        name="MCP Plugins",
        passed=True,  # Not having plugins is OK
        details="No MCP plugins detected",
    )


async def check_python_version() -> CheckResult:
    """Check Python version meets requirements."""
    import sys
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    passed = sys.version_info >= (3, 12)
    return CheckResult(
        name="Python",
        passed=passed,
        version=version,
        details="" if passed else "Requires Python 3.12+",
        critical=True,
    )


async def run_preflight_checks() -> PreflightResult:
    """Run all pre-flight checks."""
    checks = await asyncio.gather(
        check_python_version(),
        check_claude_cli(),
        check_codex_cli(),
        check_mcp_plugins(),
    )
    result = PreflightResult(checks=list(checks))

    for check in result.checks:
        logger.info(
            "preflight_check",
            name=check.name,
            passed=check.passed,
            version=check.version,
            critical=check.critical,
        )

    return result
