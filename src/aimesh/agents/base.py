"""Base agent ABC for AI Mesh."""

from abc import ABC, abstractmethod

import structlog

from aimesh.core.bus import AbstractMessageBus
from aimesh.core.message import MeshMessage, MessageType

logger = structlog.get_logger("agent")


class BaseAgent(ABC):
    """Abstract base class for all AI Mesh agents.

    Subclass this and implement handle_message() to create a new agent type.
    """

    def __init__(
        self,
        agent_id: str,
        agent_type: str,
        bus: AbstractMessageBus,
        capabilities: list[str] | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.agent_type = agent_type
        self.bus = bus
        self.capabilities = capabilities or []
        self.status: str = "idle"

    async def start(self) -> None:
        """Start the agent and subscribe to the message bus."""
        await self.bus.subscribe(self.agent_id, self._on_message)
        self.status = "idle"
        logger.info("agent_started", agent_id=self.agent_id, agent_type=self.agent_type)

    async def stop(self) -> None:
        """Stop the agent and unsubscribe from the bus."""
        await self.bus.unsubscribe(self.agent_id)
        self.status = "stopped"
        logger.info("agent_stopped", agent_id=self.agent_id)

    async def _on_message(self, message: MeshMessage) -> None:
        """Internal message handler that updates status and delegates."""
        self.status = "busy"
        try:
            await self.handle_message(message)
        finally:
            self.status = "idle"

    @abstractmethod
    async def handle_message(self, message: MeshMessage) -> None:
        """Process an incoming message. Must be implemented by subclasses."""
        ...

    async def heartbeat(self) -> dict:
        """Return agent health status. Override for custom health checks."""
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "status": self.status,
        }

    async def send(
        self,
        recipient: str,
        msg_type: MessageType,
        content: str,
        task_id: str | None = None,
        metadata: dict | None = None,
        parent_id: str | None = None,
    ) -> None:
        """Helper to send a message via the bus."""
        msg = MeshMessage(
            sender=self.agent_id,
            recipient=recipient,
            msg_type=msg_type,
            content=content,
            task_id=task_id,
            metadata=metadata or {},
            parent_id=parent_id,
        )
        await self.bus.publish(msg)
