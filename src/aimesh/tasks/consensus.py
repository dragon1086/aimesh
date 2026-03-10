"""Verification and completion flow for AI Mesh.

v1 strategy:
- Coder output: automated checks (pytest, ruff, mypy on task branch)
- Researcher output: PM judgment + human approval
- Verifier interface accepts list of Callable for v2 extensibility
"""

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger("consensus")

# Type for a verifier function: takes branch name, returns VerificationResult
Verifier = Callable[[str], Coroutine[Any, Any, "VerificationResult"]]


@dataclass
class VerificationResult:
    """Result from an automated verification step."""

    name: str
    passed: bool
    output: str
    details: str = ""


async def run_command_check(branch: str, command: str, name: str) -> VerificationResult:
    """Run a shell command on a branch and return pass/fail result."""
    try:
        # Checkout the branch first, then run the command
        full_cmd = f"git checkout {branch} 2>/dev/null; {command}"
        proc = await asyncio.create_subprocess_shell(
            full_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        output = stdout.decode() + stderr.decode()
        passed = proc.returncode == 0

        return VerificationResult(
            name=name,
            passed=passed,
            output=output[:1000],
            details=f"Exit code: {proc.returncode}",
        )
    except asyncio.TimeoutError:
        return VerificationResult(
            name=name, passed=False, output="Command timed out after 120s", details="timeout"
        )
    except Exception as e:
        return VerificationResult(
            name=name, passed=False, output=str(e), details="exception"
        )


def default_coder_verifiers() -> list[Verifier]:
    """Default verification steps for coder output."""

    async def check_pytest(branch: str) -> VerificationResult:
        return await run_command_check(branch, "python3 -m pytest --tb=short -q", "pytest")

    async def check_ruff(branch: str) -> VerificationResult:
        return await run_command_check(branch, "python3 -m ruff check .", "ruff")

    async def check_mypy(branch: str) -> VerificationResult:
        return await run_command_check(branch, "python3 -m mypy --ignore-missing-imports .", "mypy")

    return [check_pytest, check_ruff, check_mypy]


async def verify_coder_output(
    branch: str,
    verifiers: list[Verifier] | None = None,
) -> list[VerificationResult]:
    """Run all verification checks on a coder's task branch.

    Returns list of VerificationResult (one per verifier).
    """
    if verifiers is None:
        verifiers = default_coder_verifiers()

    results = []
    for verifier in verifiers:
        result = await verifier(branch)
        results.append(result)
        logger.info(
            "verification_step",
            name=result.name,
            passed=result.passed,
            branch=branch,
        )

    return results


def format_verification_summary(results: list[VerificationResult]) -> str:
    """Format verification results for display."""
    lines = ["*Verification Results:*\n"]
    all_passed = True
    for r in results:
        icon = "PASS" if r.passed else "FAIL"
        lines.append(f"[{icon}] {r.name}")
        if not r.passed:
            all_passed = False
            # Include first 200 chars of output for failed checks
            lines.append(f"  Output: {r.output[:200]}")

    status = "All checks passed" if all_passed else "Some checks failed"
    lines.append(f"\nOverall: {status}")
    return "\n".join(lines)
