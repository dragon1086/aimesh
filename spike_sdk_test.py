"""
Phase 0: Claude SDK Spike
Tests two integration patterns for non-blocking asyncio usage:
1. Claude CLI subprocess (for Claude Code capabilities - file editing, bash)
2. Anthropic AsyncAnthropic (for simpler LLM tasks - PM decomposition, analysis)
"""

import asyncio
import json
import time


async def test_claude_cli_subprocess():
    """Pattern 1: Claude CLI via asyncio.create_subprocess_exec

    Use for: Coder agent, Researcher agent (needs Claude Code tools)
    Pros: Full Claude Code capabilities (file read/write, bash, glob, grep)
    Cons: Separate process, slightly higher overhead
    """
    print("\n=== Test 1: Claude CLI Subprocess ===")

    start = time.monotonic()

    proc = await asyncio.create_subprocess_exec(
        "claude",
        "--print",
        "--output-format", "json",
        "--max-turns", "1",
        "--dangerously-skip-permissions",
        "-p", "Reply with exactly: SPIKE_TEST_OK",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await proc.communicate()
    elapsed = time.monotonic() - start

    if proc.returncode == 0:
        try:
            result = json.loads(stdout.decode())
            content = result.get("result", "")
            print(f"  Status: SUCCESS")
            print(f"  Response: {content[:100]}")
            print(f"  Elapsed: {elapsed:.2f}s")
            print(f"  Cost: ${result.get('cost_usd', 'N/A')}")
            return True
        except json.JSONDecodeError:
            print(f"  Status: SUCCESS (plain text)")
            print(f"  Response: {stdout.decode()[:100]}")
            print(f"  Elapsed: {elapsed:.2f}s")
            return True
    else:
        print(f"  Status: FAILED (exit code {proc.returncode})")
        print(f"  Stderr: {stderr.decode()[:200]}")
        return False


async def test_anthropic_async():
    """Pattern 2: Anthropic AsyncAnthropic for direct API calls

    Use for: PM agent (task decomposition, simple LLM reasoning)
    Pros: Native async, no subprocess overhead, cheaper (no tool use)
    Cons: No Claude Code tools (no file editing, bash)
    """
    print("\n=== Test 2: Anthropic AsyncAnthropic ===")

    import anthropic

    start = time.monotonic()
    client = anthropic.AsyncAnthropic()

    response = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=50,
        messages=[{"role": "user", "content": "Reply with exactly: SPIKE_TEST_OK"}],
    )

    elapsed = time.monotonic() - start
    content = response.content[0].text

    print(f"  Status: SUCCESS")
    print(f"  Response: {content[:100]}")
    print(f"  Elapsed: {elapsed:.2f}s")
    print(f"  Model: {response.model}")
    print(f"  Tokens: {response.usage.input_tokens}in / {response.usage.output_tokens}out")

    await client.close()
    return True


async def test_non_blocking():
    """Verify that AsyncAnthropic does NOT block the asyncio event loop.

    Runs a timer concurrently with an API call. If the timer completes
    on schedule (within 0.5s tolerance), the call is non-blocking.
    """
    print("\n=== Test 3: AsyncAnthropic Non-Blocking Verification ===")

    timer_results = []

    async def timer_tick(interval=0.5, ticks=4):
        for i in range(ticks):
            await asyncio.sleep(interval)
            timer_results.append((i + 1, time.monotonic()))

    start = time.monotonic()

    import anthropic
    client = anthropic.AsyncAnthropic()

    timer_task = asyncio.create_task(timer_tick(0.5, 4))
    api_task = asyncio.create_task(
        client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=50,
            messages=[{"role": "user", "content": "Reply with exactly: NON_BLOCK_TEST"}],
        )
    )

    await asyncio.gather(timer_task, api_task)
    await client.close()

    all_on_time = True
    for tick_num, tick_time in timer_results:
        expected = start + (tick_num * 0.5)
        drift = abs(tick_time - expected)
        status = "OK" if drift < 0.5 else "BLOCKED"
        if drift >= 0.5:
            all_on_time = False
        print(f"  Tick {tick_num}: drift={drift:.3f}s [{status}]")

    if all_on_time:
        print("  RESULT: Event loop NOT blocked - async pattern works correctly")
    else:
        print("  RESULT: Event loop WAS BLOCKED - need different pattern")

    return all_on_time


async def test_claude_cli_non_blocking():
    """Verify Claude CLI subprocess doesn't block event loop."""
    print("\n=== Test 4: Claude CLI Non-Blocking Verification ===")

    timer_results = []

    async def timer_tick(interval=0.5, ticks=4):
        for i in range(ticks):
            await asyncio.sleep(interval)
            timer_results.append((i + 1, time.monotonic()))

    start = time.monotonic()

    timer_task = asyncio.create_task(timer_tick(0.5, 4))
    cli_task = asyncio.create_task(_run_claude_cli("Reply with exactly: NON_BLOCK_CLI"))

    await asyncio.gather(timer_task, cli_task)

    all_on_time = True
    for tick_num, tick_time in timer_results:
        expected = start + (tick_num * 0.5)
        drift = abs(tick_time - expected)
        status = "OK" if drift < 0.5 else "BLOCKED"
        if drift >= 0.5:
            all_on_time = False
        print(f"  Tick {tick_num}: drift={drift:.3f}s [{status}]")

    if all_on_time:
        print("  RESULT: Claude CLI subprocess does NOT block event loop")
    else:
        print("  RESULT: Claude CLI subprocess BLOCKS event loop")

    return all_on_time


async def _run_claude_cli(prompt: str) -> str:
    """Run claude CLI as async subprocess and return result."""
    proc = await asyncio.create_subprocess_exec(
        "claude",
        "--print",
        "--output-format", "json",
        "--max-turns", "1",
        "--dangerously-skip-permissions",
        "-p", prompt,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode == 0:
        try:
            return json.loads(stdout.decode()).get("result", "")
        except json.JSONDecodeError:
            return stdout.decode()
    return f"ERROR: {stderr.decode()[:200]}"


async def main():
    print("=" * 60)
    print("AI Mesh - Phase 0: Claude SDK Spike")
    print("=" * 60)

    results = {}

    # Test 1: Claude CLI subprocess
    try:
        results["claude_cli"] = await test_claude_cli_subprocess()
    except Exception as e:
        print(f"  FAILED: {e}")
        results["claude_cli"] = False

    # Test 2: Anthropic AsyncAnthropic
    try:
        results["anthropic_async"] = await test_anthropic_async()
    except Exception as e:
        print(f"  FAILED: {e}")
        results["anthropic_async"] = False

    # Test 3: Non-blocking verification (AsyncAnthropic)
    try:
        results["async_non_blocking"] = await test_non_blocking()
    except Exception as e:
        print(f"  FAILED: {e}")
        results["async_non_blocking"] = False

    # Test 4: Non-blocking verification (Claude CLI)
    try:
        results["cli_non_blocking"] = await test_claude_cli_non_blocking()
    except Exception as e:
        print(f"  FAILED: {e}")
        results["cli_non_blocking"] = False

    # Summary
    print("\n" + "=" * 60)
    print("SPIKE RESULTS SUMMARY")
    print("=" * 60)
    for test_name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {test_name}")

    all_passed = all(results.values())
    print(f"\n  Overall: {'ALL TESTS PASSED' if all_passed else 'SOME TESTS FAILED'}")

    if all_passed:
        print("\n  RECOMMENDED PATTERN:")
        print("  - PM Agent: anthropic.AsyncAnthropic (native async, cheaper)")
        print("  - Coder/Researcher: asyncio.create_subprocess_exec + claude CLI")
        print("  - Both patterns verified non-blocking in asyncio event loop")

    return all_passed


if __name__ == "__main__":
    asyncio.run(main())
