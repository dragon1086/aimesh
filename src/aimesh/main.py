"""AI Mesh entry point — startup orchestration and shutdown sequencing."""

import asyncio
import os
import signal
import sys

import structlog

from aimesh.config import MeshSettings, load_org_config, ORGS_DIR
from aimesh.core.bus import AsyncioMessageBus
from aimesh.core.context import ContextStore
from aimesh.core.message import MeshMessage, MessageType
from aimesh.core.logging import setup_logging
from aimesh.core.registry import AgentRegistry
from aimesh.agents.executor import AnthropicExecutor, ToolUsingExecutor
from aimesh.agents.factory import AgentFactory
from aimesh.agents.pm import PMAgent
from aimesh.agents.pm_tools import PM_TOOL_SCHEMAS, PMToolHandlers, create_pm_dispatcher
from aimesh.tasks.tracker import TaskTracker
from aimesh.telegram.bot import TelegramBot
from aimesh.telegram.display import TelegramDisplay
from aimesh.telegram.handlers import CommandHandlers
from aimesh.telegram.setup_wizard import SetupWizard

logger = structlog.get_logger("main")


async def run() -> None:
    """Main entry point for AI Mesh."""
    setup_logging()
    logger.info("aimesh_starting")

    # First-run detection: no .env and no env var → trigger CLI wizard
    from pathlib import Path

    if not Path(".env").exists() and not os.environ.get("TELEGRAM_BOT_TOKEN"):
        logger.info("no_config_detected", hint="Starting CLI setup wizard")
        print("No configuration found. Starting setup wizard...")
        from aimesh.cli.wizard import CLISetupWizard

        wizard = CLISetupWizard()
        await wizard.run()
        print("Setup complete. Continuing startup...\n")

    # Load config
    settings = MeshSettings()
    org_id = os.environ.get("AIMESH_ORG_ID", "_default")
    org_config = load_org_config(org_id)
    logger.info("org_config_loaded", org_id=org_id, org_name=org_config.org_name,
                agent_count=len(org_config.agents))

    # Initialize core infrastructure
    bus = AsyncioMessageBus()
    context_store = ContextStore()
    await context_store.initialize()
    registry = AgentRegistry()
    tracker = TaskTracker()

    # Create AgentFactory
    factory = AgentFactory(
        bus=bus,
        registry=registry,
        tracker=tracker,
        default_model=org_config.agents[0].model if org_config.agents else "sonnet",
        default_workspace=org_config.workspace_path,
    )

    # Spawn default roster workers from org config
    org_dir = ORGS_DIR / org_id
    for agent_entry in org_config.agents:
        soul_prompt = org_config.load_soul_prompt(agent_entry, org_dir)
        await factory.spawn_worker(
            agent_type=agent_entry.type,
            soul_prompt=soul_prompt,
            capabilities=agent_entry.capabilities,
            model=agent_entry.model,
            workspace_path=org_config.workspace_path,
            agent_id=agent_entry.id,
            max_budget_usd=agent_entry.max_budget_usd,
        )
        logger.info("agent_spawned", agent_id=agent_entry.id,
                     agent_type=agent_entry.type,
                     soul_file=agent_entry.soul_file or "org-level soul.md")

    # Create PM with ToolUsingExecutor
    pm_tool_handlers = PMToolHandlers()
    pm_dispatcher = create_pm_dispatcher(pm_tool_handlers)

    pm_inner_executor = AnthropicExecutor(
        model=org_config.pm.model,
        api_key=settings.anthropic_api_key or None,
    )
    pm_executor = ToolUsingExecutor(
        inner=pm_inner_executor,
        tools=PM_TOOL_SCHEMAS,
        tool_dispatcher=pm_dispatcher,
    )

    pm = PMAgent(
        bus=bus, executor=pm_executor, registry=registry, tracker=tracker,
        review_timeout_minutes=settings.review_timeout_minutes,
    )

    # Wire PM tool handlers with system references
    pm_tool_handlers.set_dependencies(
        factory=factory,
        registry=registry,
        tracker=tracker,
        bus=bus,
    )

    # Register PM in registry
    registry.register("pm", "pm", ["decompose", "assign", "track", "coordinate"])
    await pm.start()

    # Setup Telegram
    admin_ids = org_config.telegram.admin_user_ids or settings.admin_ids
    wizard = SetupWizard(admin_ids=admin_ids)

    handlers = CommandHandlers(
        pm=pm, tracker=tracker, registry=registry,
        admin_ids=admin_ids,
    )

    # Setup Natural Language handler
    from aimesh.nl.context import ConversationContext
    from aimesh.telegram.nl_handler import NaturalLanguageHandler

    nl_context = ConversationContext()
    nl_handler = NaturalLanguageHandler(
        bus=bus, tracker=tracker, context=nl_context,
        handlers=handlers,
    )

    # Subscribe NL handler to receive PM CHAT responses
    async def _nl_response_handler(msg: MeshMessage):
        if msg.msg_type == MessageType.CHAT:
            await nl_handler.handle_pm_response(msg)

    await bus.subscribe("human", _nl_response_handler)

    bot = TelegramBot(
        token=settings.telegram_bot_token,
        handlers=handlers,
        setup_wizard=wizard,
        nl_handler=nl_handler,
    )
    await bot.setup()

    display = TelegramDisplay(
        bus=bus,
        chat_id=org_config.telegram.group_chat_id or settings.telegram_group_chat_id,
        send_fn=bot.get_send_fn(),
    )
    await display.start()

    # Start bot polling
    await bot.start()

    # Log agent roster
    agent_ids = [w.agent_id for w in factory.list_workers()]
    logger.info("aimesh_ready", org=org_id, agents=["pm"] + agent_ids,
                pm_tools_enabled=org_config.pm.tools_enabled)

    # Setup shutdown handler
    shutdown_event = asyncio.Event()

    def on_signal(sig):
        logger.info("shutdown_signal_received", signal=sig)
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, on_signal, sig)

    # Wait for shutdown signal
    await shutdown_event.wait()

    # Graceful shutdown sequence
    logger.info("shutdown_starting")

    # 1. Stop Telegram bot (no new commands)
    await bot.stop()

    # 2. Stop display (no new messages to Telegram)
    await display.stop()

    # 3. Stop PM agent
    await pm.stop()

    # 4. Teardown all factory-spawned workers
    await factory.teardown_all()

    # 5. Shutdown bus (cancel consumer tasks)
    await bus.shutdown()

    # 6. Close context store
    await context_store.close()

    # 7. Close executor clients
    await pm_inner_executor.close()

    logger.info("aimesh_shutdown_complete")


def main() -> None:
    """CLI entry point."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
