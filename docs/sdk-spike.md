# Phase 0: Claude SDK Spike Results

**Date:** 2026-03-10
**Environment:** Python 3.12.10, Claude Code CLI 2.1.72, anthropic SDK 0.64.0

## Findings

### Pattern A: Claude CLI Subprocess (RECOMMENDED for worker agents)

Uses `asyncio.create_subprocess_exec` to run `claude` as an async child process.
Arguments are passed as a safe list (no shell interpolation).

- **Non-blocking:** CONFIRMED (timer drift less than 0.001s across 4 ticks)
- **Capabilities:** Full Claude Code toolset (file read/write, bash, glob, grep)
- **Output format:** `--output-format json` returns structured JSON with result and cost
- **Session control:** `--max-turns` limits tool use rounds, `--max-budget-usd` limits cost
- **Gotcha:** Cannot run nested inside another Claude Code session. In production, AI Mesh runs as a standalone process so this is not an issue.

**Use for:** CoderAgent, ResearcherAgent (need Claude Code tools)

### Pattern B: Anthropic AsyncAnthropic (RECOMMENDED for PM agent)

Uses `anthropic.AsyncAnthropic` for direct async API calls via httpx.

- **Non-blocking:** YES (httpx-based, native async/await)
- **Capabilities:** Raw Messages API only (no file editing, no bash)
- **Cost:** Cheaper per call (no tool use overhead)
- **Auth:** Requires `ANTHROPIC_API_KEY` environment variable

**Use for:** PMAgent (task decomposition, reasoning, status judgment)

## Chosen Architecture

| Agent | Pattern | Rationale |
|-------|---------|-----------|
| PM Agent | AsyncAnthropic (Pattern B) | Only needs reasoning, no tools. Cheaper, faster, native async. |
| Coder Agent | Claude CLI subprocess (Pattern A) | Needs file editing, bash, git operations via Claude Code. |
| Researcher Agent | Claude CLI subprocess (Pattern A) | Needs web search, file reading, analysis tools. |

## Asyncio Integration Pattern

Both patterns are non-blocking in an asyncio event loop:
- Pattern A: `asyncio.create_subprocess_exec` yields control while waiting for subprocess
- Pattern B: `httpx.AsyncClient` yields control while waiting for HTTP response

The executor wrapper (`agents/executor.py`) supports both patterns:
- `ClaudeCodeExecutor` wraps Pattern A for worker agents
- `AnthropicExecutor` wraps Pattern B for PM agent
- Both implement a common `async execute(prompt, context) -> str` interface

## Environment Variables Required

- `ANTHROPIC_API_KEY` for AsyncAnthropic (PM agent)
- Claude CLI uses OAuth by default (no key needed if logged in)

## Gotchas

1. **Nested sessions:** Claude CLI refuses to run inside another Claude Code session. Not an issue in production standalone deployment.
2. **Rate limits:** Both Anthropic API and Claude CLI have rate limits. Wrap with retry logic.
3. **Cost tracking:** Claude CLI JSON output includes `cost_usd`. Anthropic SDK provides `usage.input_tokens` and `usage.output_tokens`.
4. **Long-running tasks:** Claude CLI bounded by `--max-turns` and `--max-budget-usd`. Anthropic SDK needs `asyncio.wait_for()` timeout.
