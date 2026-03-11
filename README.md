# AI Mesh

Multi-Agent Orchestration via Telegram. AI agents coordinate autonomously to complete tasks, with a human providing direction through a Telegram group chat.

## Architecture

```
Telegram Group Chat
        │
   TelegramBot (NL + Commands)
        │
   Orchestrator (Python)
        │
   ┌────┴────┐
   PM (tmux)  Workers (tmux)
   │          │
   Claude Code / Codex / Gemini
```

- **PM Agent**: Runs as a real CLI process (Claude Code, Codex, or Gemini) in a tmux session. Decomposes tasks, assigns to workers, tracks progress.
- **Worker Agents**: Coder and Researcher agents that implement code and perform analysis.
- **Orchestrator**: Python process that manages PM lifecycle, message routing, and task state.
- **3-Layer Memory**: Persistent identity (L3), accumulated knowledge (L2), daily episodes (L1), working context (L0).
- **Telegram Display**: Shows all agent activity in a group chat.

## Prerequisites

- Python 3.12+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (`claude` command) and/or [Codex CLI](https://github.com/openai/codex) (`codex` command)
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Anthropic API Key (only needed for legacy `anthropic` engine mode)

## Quick Start

```bash
# 1. Clone
git clone <repo-url>
cd aimesh

# 2. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3. Install
pip install -e ".[dev]"

# 4. Run (setup wizard starts automatically on first run)
python -m aimesh.main
```

The CLI setup wizard will guide you through configuring your bot token, API keys, and group chat.

## Manual Setup

If you prefer manual configuration:

```bash
# Copy and edit environment config
cp .env.example .env
# Edit .env with your tokens
```

### Environment Variables (.env)

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | Bot token from @BotFather |
| `TELEGRAM_GROUP_CHAT_ID` | Yes | Group chat ID for agent display |
| `ADMIN_USER_IDS` | Yes | Comma-separated admin Telegram user IDs |
| `ANTHROPIC_API_KEY` | Only for `anthropic` engine | API key for legacy API mode |
| `MAX_BUDGET_PER_TASK_USD` | No | Max spend per task (default: $5) |

### Organization Config (orgs/_default/config.yaml)

```yaml
org_id: default
org_name: "My Team"

telegram:
  group_chat_id: -100XXXXXXXXXX
  admin_user_ids: [123456789]

pm:
  engine: "claude_code"  # claude_code | codex | gemini | anthropic (legacy API)
  model: "claude-sonnet-4-20250514"

engine_config:
  claude_code: "claude --dangerously-skip-permissions"
  codex: "codex --full-auto"
  gemini: "gemini-cli"

agents:
  - id: "coder-1"
    type: "coder"
    soul_file: "souls/coder.md"
    capabilities: ["code", "implement", "fix", "refactor"]
  - id: "researcher-1"
    type: "researcher"
    soul_file: "souls/researcher.md"
    capabilities: ["research", "analyze", "compare"]

workspace_path: "./workspace"
```

### Choosing an Engine

| Engine | CLI Required | Description |
|--------|-------------|-------------|
| `claude_code` | `claude` | Claude Code — full tool access, file editing, bash |
| `codex` | `codex` | OpenAI Codex CLI — full-auto mode |
| `gemini` | `gemini-cli` | Google Gemini CLI |
| `anthropic` | None (API) | Legacy API-only mode (no tmux, stateless) |

Set `pm.engine` in your org config. The PM runs as a real CLI process in a tmux session, with persistent memory across sessions. Workers follow the same engine.

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/task <description>` | Submit a new task |
| `/status` | Show active tasks |
| `/agents` | Show registered agents |
| `/approve <task_id>` | Approve a completed task |
| `/reject <task_id> [feedback]` | Reject and request rework |
| `/cancel <task_id>` | Cancel a task |

You can also chat naturally — the NL handler routes messages to the PM automatically.

## Development

```bash
# Run tests (240 tests)
pytest tests/ -v

# Lint
ruff check src/
```

## Project Structure

```
src/aimesh/
  main.py              # Entry point, startup/shutdown
  config.py            # Pydantic settings + org config
  core/
    bus.py             # AbstractMessageBus + AsyncioMessageBus
    message.py         # MeshMessage protocol
    task.py            # Task state machine
    context.py         # Shared context store
    registry.py        # Agent registry
    logging.py         # Structured logging (structlog)
  agents/
    base.py            # BaseAgent ABC
    pm.py              # PM agent (legacy API mode)
    coder.py           # Coder agent
    researcher.py      # Researcher agent
    executor.py        # LLM execution wrappers
  tmux/
    session.py         # Async tmux CLI wrapper
    protocol.py        # File outbox protocol (atomic JSON)
    bridge.py          # Hybrid IPC (send-keys + file outbox)
    orchestrator.py    # TmuxPMOrchestrator (v3 PM)
    lifecycle.py       # Session state machine
  memory/
    manager.py         # 3-layer memory (L0-L3)
    templates.py       # Default identity template
  routing/
    router.py          # Multi-PM routing (@mention, task context, least-busy)
  tasks/
    decomposer.py      # Task decomposition
    tracker.py         # Progress tracking
    consensus.py       # Verification + completion
  telegram/
    bot.py             # Telegram bot setup
    handlers.py        # Command handlers
    nl_handler.py      # Natural language message handler
    formatter.py       # Persona prefix formatting
    display.py         # Rate-limited display layer
  nl/
    classifier.py      # Intent classification
    context.py         # Conversation context
  cli/
    wizard.py          # First-run setup wizard
orgs/
  _default/
    config.yaml        # Organization config
    soul.md            # Default PM personality
    souls/             # Per-agent personality files
```
