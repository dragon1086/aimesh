# AI Mesh

Multi-Agent Orchestration via Telegram. A system where AI agents coordinate autonomously to complete tasks, with a human providing direction through a Telegram group chat.

## Architecture

- **PM Agent**: Decomposes tasks, assigns to workers, tracks progress
- **Coder Agent**: Implements code via Claude Code CLI (branch-per-task)
- **Researcher Agent**: Performs analysis and research via Claude Code CLI
- **Telegram Display**: Shows all agent activity in a group chat with persona prefixes
- **Internal Message Bus**: asyncio-based inter-agent communication (AbstractMessageBus ABC)

## Prerequisites

- Python 3.12+
- Claude Code CLI (`claude` command available)
- Telegram Bot Token (from @BotFather)
- Anthropic API Key (for PM agent)

## Setup

```bash
# Clone and install
git clone <repo-url>
cd aimesh
pyenv local 3.12.10  # or your Python 3.12+ version
pip install -e ".[dev]"

# Configure
cp .env.example .env
# Edit .env with your tokens and settings

# Run
python -m aimesh.main
```

## Configuration (.env)

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `ANTHROPIC_API_KEY` | API key for PM agent |
| `TELEGRAM_GROUP_CHAT_ID` | Group chat ID for agent display |
| `ADMIN_USER_IDS` | Comma-separated admin user IDs |
| `MAX_BUDGET_PER_TASK_USD` | Max spend per task (default: $5) |

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/task <description>` | Submit a new task |
| `/status` | Show active tasks |
| `/agents` | Show registered agents |
| `/approve <task_id>` | Approve a completed task |
| `/reject <task_id> [feedback]` | Reject and request rework |
| `/cancel <task_id>` | Cancel a task |

## Development

```bash
# Run tests
pytest tests/ -v

# Lint
ruff check src/

# Add a new agent type
# See docs/agent-development.md
```

## Project Structure

```
src/aimesh/
  main.py              # Entry point, startup/shutdown
  config.py            # Pydantic settings
  core/
    bus.py             # AbstractMessageBus + AsyncioMessageBus
    message.py         # MeshMessage protocol
    task.py            # Task state machine
    context.py         # Shared context store
    registry.py        # Agent registry
    logging.py         # Structured logging (structlog)
  agents/
    base.py            # BaseAgent ABC
    pm.py              # PM agent
    coder.py           # Coder agent
    researcher.py      # Researcher agent
    executor.py        # LLM execution wrappers
  tasks/
    decomposer.py      # Task decomposition
    tracker.py         # Progress tracking
    consensus.py       # Verification + completion
  telegram/
    bot.py             # Telegram bot setup
    handlers.py        # Command handlers
    formatter.py       # Persona prefix formatting
    display.py         # Rate-limited display layer
```
