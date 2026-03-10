"""Agent registry for AI Mesh capability discovery."""

from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger("registry")


@dataclass
class AgentInfo:
    """Registration info for an agent."""

    agent_id: str
    agent_type: str
    capabilities: list[str] = field(default_factory=list)
    status: str = "idle"


class AgentRegistry:
    """Registry for discovering agents by ID or capability."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentInfo] = {}

    def register(self, agent_id: str, agent_type: str, capabilities: list[str] | None = None) -> None:
        """Register an agent with its capabilities."""
        info = AgentInfo(
            agent_id=agent_id,
            agent_type=agent_type,
            capabilities=capabilities or [],
        )
        self._agents[agent_id] = info
        logger.info("agent_registered", agent_id=agent_id, agent_type=agent_type,
                     capabilities=info.capabilities)

    def unregister(self, agent_id: str) -> None:
        """Remove an agent from the registry."""
        if agent_id in self._agents:
            del self._agents[agent_id]
            logger.info("agent_unregistered", agent_id=agent_id)

    def get(self, agent_id: str) -> AgentInfo | None:
        """Get agent info by ID."""
        return self._agents.get(agent_id)

    def discover_by_capability(self, capability: str) -> list[AgentInfo]:
        """Find all agents with a specific capability."""
        return [
            info for info in self._agents.values()
            if capability in info.capabilities
        ]

    def discover_by_type(self, agent_type: str) -> list[AgentInfo]:
        """Find all agents of a specific type."""
        return [
            info for info in self._agents.values()
            if info.agent_type == agent_type
        ]

    def all_agents(self) -> list[AgentInfo]:
        """List all registered agents."""
        return list(self._agents.values())

    def set_status(self, agent_id: str, status: str) -> None:
        """Update an agent's status (idle/busy)."""
        if agent_id in self._agents:
            self._agents[agent_id].status = status
