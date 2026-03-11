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

    # Multi-org support: AIMESH_TEAM_IDS=dev,marketing or single AIMESH_ORG_ID
    team_ids_str = os.environ.get("AIMESH_TEAM_IDS", "")
    if team_ids_str:
        org_ids = [t.strip() for t in team_ids_str.split(",") if t.strip()]
    else:
        org_ids = [os.environ.get("AIMESH_ORG_ID", "_default")]

    org_configs = {}
    for oid in org_ids:
        org_configs[oid] = load_org_config(oid)
        logger.info("org_config_loaded", org_id=oid,
                     org_name=org_configs[oid].org_name,
                     agent_count=len(org_configs[oid].agents))

    # Use first org as primary (for backward-compatible settings)
    primary_org_id = org_ids[0]
    primary_config = org_configs[primary_org_id]

    # Initialize core infrastructure (shared across all orgs)
    bus = AsyncioMessageBus()
    context_store = ContextStore()
    await context_store.initialize()
    registry = AgentRegistry()
    tracker = TaskTracker()

    # Collaboration manager (handles multi-PM routing and inter-PM collab)
    from aimesh.collaboration.manager import CollaborationManager, PMInstance
    collab_manager = CollaborationManager(bus=bus) if len(org_ids) > 1 else None

    # Create PM for each org
    pm_instances = {}  # org_id -> PM instance
    factories = {}  # org_id -> AgentFactory

    for org_id, org_config in org_configs.items():
        # Determine agent_id: "pm" for single-org, "pm_{org_id}" for multi-org
        pm_agent_id = f"pm_{org_id}" if len(org_ids) > 1 else "pm"

        # Create AgentFactory for this org
        factory = AgentFactory(
            bus=bus,
            registry=registry,
            tracker=tracker,
            default_model=org_config.agents[0].model if org_config.agents else "sonnet",
            default_workspace=org_config.workspace_path,
        )
        factories[org_id] = factory

        # Spawn default roster workers from org config
        org_dir = ORGS_DIR / org_id
        for agent_entry in org_config.agents:
            # Prefix agent IDs in multi-org mode to avoid collisions
            worker_id = (
                f"{org_id}_{agent_entry.id}" if len(org_ids) > 1
                else agent_entry.id
            )
            soul_prompt = org_config.load_soul_prompt(agent_entry, org_dir)
            await factory.spawn_worker(
                agent_type=agent_entry.type,
                soul_prompt=soul_prompt,
                capabilities=agent_entry.capabilities,
                model=agent_entry.model,
                workspace_path=org_config.workspace_path,
                agent_id=worker_id,
                max_budget_usd=agent_entry.max_budget_usd,
            )
            logger.info("agent_spawned", agent_id=worker_id,
                         agent_type=agent_entry.type, org=org_id)

        # Create PM based on engine setting
        pm_tool_handlers = PMToolHandlers()
        pm_dispatcher = create_pm_dispatcher(pm_tool_handlers)

        if org_config.pm.engine == "anthropic":
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
            # For multi-org API mode, override agent_id
            if len(org_ids) > 1:
                pm.agent_id = pm_agent_id
        else:
            from aimesh.tmux.orchestrator import TmuxPMOrchestrator
            pm = TmuxPMOrchestrator(
                bus=bus,
                registry=registry,
                tracker=tracker,
                pm_id=org_id,
                workspace=org_config.workspace_path,
                engine_command=org_config.engine_config.get_command(org_config.pm.engine),
                agent_id=pm_agent_id,
                collab_manager=collab_manager,
            )

        pm_tool_handlers.set_dependencies(
            factory=factory,
            registry=registry,
            tracker=tracker,
            bus=bus,
        )

        registry.register(pm_agent_id, "pm", ["decompose", "assign", "track", "coordinate"])
        await pm.start()
        pm_instances[org_id] = pm

        # Register with CollaborationManager
        if collab_manager is not None:
            # Load identity text from soul.md
            soul_path = org_dir / "soul.md"
            identity_text = soul_path.read_text(encoding="utf-8") if soul_path.exists() else ""

            collab_manager.register_pm(PMInstance(
                org_id=org_id,
                org_config=org_config,
                pm=pm,
                agent_id=pm_agent_id,
                domain=org_config.domain,
                domain_keywords=org_config.domain_keywords,
                identity_text=identity_text,
            ))

        logger.info("pm_started", pm_id=pm_agent_id, org=org_id,
                     engine=org_config.pm.engine)

    # Use primary PM for backward-compatible handlers
    primary_pm = pm_instances[primary_org_id]

    # Subscribe CollaborationManager to bus if multi-PM
    if collab_manager is not None:
        await bus.subscribe("collab_manager", collab_manager.on_bus_message)

    # Setup Telegram
    admin_ids = primary_config.telegram.admin_user_ids or settings.admin_ids
    wizard = SetupWizard(admin_ids=admin_ids)

    handlers = CommandHandlers(
        pm=primary_pm, tracker=tracker, registry=registry,
        admin_ids=admin_ids,
    )

    # Setup Natural Language handler
    from aimesh.nl.context import ConversationContext
    from aimesh.telegram.nl_handler import NaturalLanguageHandler

    nl_context = ConversationContext()
    nl_handler = NaturalLanguageHandler(
        bus=bus, tracker=tracker, context=nl_context,
        handlers=handlers,
        collab_manager=collab_manager,
    )

    # Subscribe NL handler to receive PM CHAT responses
    async def _nl_response_handler(msg: MeshMessage):
        if msg.msg_type == MessageType.CHAT:
            if collab_manager is not None:
                await collab_manager.handle_pm_response(msg)
            else:
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
        chat_id=primary_config.telegram.group_chat_id or settings.telegram_group_chat_id,
        send_fn=bot.get_send_fn(),
    )
    await display.start()

    # Start bot polling
    await bot.start()

    # Log agent roster
    all_agent_ids = []
    for f in factories.values():
        all_agent_ids.extend([w.agent_id for w in f.list_workers()])
    pm_ids = list(pm_instances.keys())
    logger.info("aimesh_ready", orgs=pm_ids, agents=list(pm_instances.keys()) + all_agent_ids,
                multi_pm=collab_manager is not None)

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

    # 3. Stop all PM agents
    for oid, pm_inst in pm_instances.items():
        await pm_inst.stop()
        # Close executor clients (only exists in legacy anthropic mode)
        if hasattr(pm_inst, 'executor') and hasattr(pm_inst.executor, 'close'):
            await pm_inst.executor.close()

    # 4. Teardown all factory-spawned workers
    for f in factories.values():
        await f.teardown_all()

    # 5. Shutdown bus (cancel consumer tasks)
    await bus.shutdown()

    # 6. Close context store
    await context_store.close()

    logger.info("aimesh_shutdown_complete")


def main() -> None:
    """CLI entry point."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
