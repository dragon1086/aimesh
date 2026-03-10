"""Multi-team runner for AI Mesh — starts N bot instances from N org configs."""

import asyncio
from dataclasses import dataclass, field

import structlog

from aimesh.config import MeshSettings, load_org_config, ORGS_DIR, MultiTeamConfig

logger = structlog.get_logger("multi")


@dataclass
class TeamInstance:
    """A running team instance with all its components."""

    org_id: str
    bot: object  # TelegramBot
    pm: object  # PMAgent
    factory: object  # AgentFactory
    bot_username: str = ""


class MultiTeamRunner:
    """Starts and manages N team instances from org configs.

    Each team has its own bus, registry, tracker, PM, factory, and bot.
    State is fully isolated between teams — no shared in-memory state.
    """

    def __init__(self, config: MultiTeamConfig) -> None:
        self.config = config
        self.teams: list[TeamInstance] = []

    async def start_all(self) -> None:
        """Start all team instances."""
        for org_id in self.config.teams:
            try:
                instance = await self._start_team(org_id)
                self.teams.append(instance)
                logger.info("team_started", org_id=org_id, bot=instance.bot_username)
            except Exception as e:
                logger.error("team_start_failed", org_id=org_id, error=str(e))

    async def _start_team(self, org_id: str) -> TeamInstance:
        """Start a single team instance (mirrors main.py run() logic)."""
        from aimesh.core.bus import AsyncioMessageBus
        from aimesh.core.message import MeshMessage, MessageType
        from aimesh.core.registry import AgentRegistry
        from aimesh.agents.executor import AnthropicExecutor, ToolUsingExecutor
        from aimesh.agents.factory import AgentFactory
        from aimesh.agents.pm import PMAgent
        from aimesh.agents.pm_tools import PM_TOOL_SCHEMAS, PMToolHandlers, create_pm_dispatcher
        from aimesh.tasks.tracker import TaskTracker
        from aimesh.telegram.bot import TelegramBot
        from aimesh.telegram.display import TelegramDisplay
        from aimesh.telegram.handlers import CommandHandlers
        from aimesh.nl.context import ConversationContext
        from aimesh.telegram.nl_handler import NaturalLanguageHandler

        org_config = load_org_config(org_id)
        settings = MeshSettings()

        bus = AsyncioMessageBus()
        registry = AgentRegistry()
        tracker = TaskTracker()

        factory = AgentFactory(
            bus=bus, registry=registry, tracker=tracker,
            default_model=org_config.agents[0].model if org_config.agents else "sonnet",
            default_workspace=org_config.workspace_path,
        )

        # Spawn workers
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

        # PM setup
        pm_tool_handlers = PMToolHandlers()
        pm_dispatcher = create_pm_dispatcher(pm_tool_handlers)
        pm_inner_executor = AnthropicExecutor(
            model=org_config.pm.model,
            api_key=settings.anthropic_api_key or None,
        )
        pm_executor = ToolUsingExecutor(
            inner=pm_inner_executor, tools=PM_TOOL_SCHEMAS,
            tool_dispatcher=pm_dispatcher,
        )
        pm = PMAgent(bus=bus, executor=pm_executor, registry=registry, tracker=tracker)
        pm_tool_handlers.set_dependencies(
            factory=factory, registry=registry, tracker=tracker, bus=bus,
        )
        registry.register("pm", "pm", ["decompose", "assign", "track", "coordinate"])
        await pm.start()

        # Telegram setup
        admin_ids = org_config.telegram.admin_user_ids or settings.admin_ids
        handlers = CommandHandlers(
            pm=pm, tracker=tracker, registry=registry, admin_ids=admin_ids,
        )

        nl_context = ConversationContext()
        nl_handler = NaturalLanguageHandler(
            bus=bus, tracker=tracker, context=nl_context,
            handlers=handlers,
        )

        # Subscribe NL handler for PM responses
        async def _nl_response(msg: MeshMessage):
            if msg.msg_type == MessageType.CHAT:
                await nl_handler.handle_pm_response(msg)

        await bus.subscribe("human", _nl_response)

        bot = TelegramBot(
            token=settings.telegram_bot_token,
            handlers=handlers,
            nl_handler=nl_handler,
        )
        await bot.setup()

        display = TelegramDisplay(
            bus=bus,
            chat_id=org_config.telegram.group_chat_id or settings.telegram_group_chat_id,
            send_fn=bot.get_send_fn(),
        )
        await display.start()
        await bot.start()

        # Get bot username for @mention filtering
        bot_username = ""
        try:
            if bot._app and bot._app.bot:
                me = await bot._app.bot.get_me()
                bot_username = me.username or ""
                nl_handler.bot_username = bot_username
        except Exception:
            pass

        return TeamInstance(
            org_id=org_id, bot=bot, pm=pm, factory=factory,
            bot_username=bot_username,
        )

    async def stop_all(self) -> None:
        """Stop all teams in reverse order."""
        for team in reversed(self.teams):
            try:
                if hasattr(team.bot, "stop"):
                    await team.bot.stop()
                if hasattr(team.pm, "stop"):
                    await team.pm.stop()
                if hasattr(team.factory, "teardown_all"):
                    await team.factory.teardown_all()
                logger.info("team_stopped", org_id=team.org_id)
            except Exception as e:
                logger.error("team_stop_failed", org_id=team.org_id, error=str(e))

        self.teams.clear()
