"""AgentFactory for runtime agent spawning and lifecycle management."""

import uuid

import structlog

from aimesh.agents.dynamic_worker import DynamicWorker
from aimesh.agents.executor import ClaudeAgentSDKExecutor
from aimesh.core.bus import AbstractMessageBus
from aimesh.core.registry import AgentRegistry
from aimesh.tasks.tracker import TaskTracker

logger = structlog.get_logger("factory")


class AgentFactory:
    """Factory for spawning and tearing down DynamicWorker agents at runtime.

    Config.yaml agents are the "default roster" spawned at boot.
    PM can spawn additional workers beyond this roster via its tools.
    """

    def __init__(
        self,
        bus: AbstractMessageBus,
        registry: AgentRegistry,
        tracker: TaskTracker,
        default_model: str = "sonnet",
        default_workspace: str | None = None,
    ) -> None:
        self.bus = bus
        self.registry = registry
        self.tracker = tracker
        self.default_model = default_model
        self.default_workspace = default_workspace
        self._workers: dict[str, DynamicWorker] = {}

    async def spawn_worker(
        self,
        agent_type: str,
        soul_prompt: str,
        capabilities: list[str],
        model: str | None = None,
        workspace_path: str | None = None,
        agent_id: str | None = None,
        max_budget_usd: float = 5.0,
    ) -> DynamicWorker:
        """Spawn a new DynamicWorker and register it.

        Args:
            agent_type: Type of agent (e.g., 'coder', 'designer')
            soul_prompt: System prompt defining agent behavior
            capabilities: List of capabilities for registry
            model: Model to use (default: factory default)
            workspace_path: Working directory (default: factory default)
            agent_id: Specific ID (default: auto-generated)
            max_budget_usd: Budget limit for the executor

        Returns:
            The spawned DynamicWorker instance.
        """
        if agent_id is None:
            short_uuid = uuid.uuid4().hex[:8]
            agent_id = f"{agent_type}-{short_uuid}"

        # Create executor for this worker
        executor = ClaudeAgentSDKExecutor(
            model=model or self.default_model,
            system_prompt=soul_prompt,
            cwd=workspace_path or self.default_workspace,
        )

        # Create the worker
        worker = DynamicWorker(
            agent_id=agent_id,
            agent_type=agent_type,
            bus=self.bus,
            executor=executor,
            tracker=self.tracker,
            soul_prompt=soul_prompt,
            capabilities=capabilities,
            workspace_path=workspace_path or self.default_workspace,
        )

        # Register in registry
        self.registry.register(agent_id, agent_type, capabilities)

        # Start the worker (subscribes to bus, starts consumer loop)
        await worker.start()

        # Track it
        self._workers[agent_id] = worker

        logger.info(
            "worker_spawned",
            agent_id=agent_id,
            agent_type=agent_type,
            capabilities=capabilities,
        )
        return worker

    async def teardown_worker(self, agent_id: str) -> bool:
        """Stop and remove a spawned worker.

        Returns True on success, False if agent_id not found.
        """
        worker = self._workers.get(agent_id)
        if worker is None:
            logger.warning("teardown_unknown_agent", agent_id=agent_id)
            return False

        # Stop the worker (unsubscribes from bus)
        await worker.stop()

        # Unregister
        self.registry.unregister(agent_id)

        # Remove from tracking
        del self._workers[agent_id]

        logger.info("worker_torn_down", agent_id=agent_id)
        return True

    async def teardown_all(self) -> None:
        """Teardown all spawned workers. Used during shutdown."""
        agent_ids = list(self._workers.keys())
        for agent_id in agent_ids:
            await self.teardown_worker(agent_id)
        logger.info("all_workers_torn_down", count=len(agent_ids))

    def get_worker(self, agent_id: str) -> DynamicWorker | None:
        """Get a spawned worker by ID."""
        return self._workers.get(agent_id)

    def list_workers(self) -> list[DynamicWorker]:
        """List all spawned workers."""
        return list(self._workers.values())

    def list_available_types(self) -> list[str]:
        """List all unique agent types currently registered."""
        return list({w.agent_type for w in self._workers.values()})
