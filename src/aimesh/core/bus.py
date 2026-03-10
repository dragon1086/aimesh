"""Message bus for AI Mesh inter-agent communication."""

import asyncio
import inspect
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from typing import Any

import structlog

from aimesh.core.message import MeshMessage, MessageStatus

logger = structlog.get_logger("bus")

MessageHandler = Callable[[MeshMessage], Coroutine[Any, Any, None]]


class AbstractMessageBus(ABC):
    """Abstract base class for message bus implementations.

    Swap AsyncioMessageBus for RedisBus or NATSBus to scale beyond a single process.
    """

    @abstractmethod
    async def publish(self, message: MeshMessage) -> None:
        """Publish a message to the bus."""
        ...

    @abstractmethod
    async def subscribe(self, agent_id: str, handler: MessageHandler) -> None:
        """Subscribe an agent to receive messages addressed to it or broadcast."""
        ...

    @abstractmethod
    async def unsubscribe(self, agent_id: str) -> None:
        """Remove an agent's subscription."""
        ...


class AsyncioMessageBus(AbstractMessageBus):
    """v1 message bus using asyncio.Queue per agent."""

    def __init__(self) -> None:
        self._handlers: dict[str, MessageHandler] = {}
        self._queues: dict[str, asyncio.Queue[MeshMessage]] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    async def publish(self, message: MeshMessage) -> None:
        """Publish a message. Routes to recipient or broadcasts to all."""
        logger.info(
            "message_published",
            message_id=message.id,
            sender=message.sender,
            recipient=message.recipient,
            task_id=message.task_id,
            msg_type=message.msg_type.value,
            timestamp=message.timestamp.isoformat(),
            content_length=len(message.content),
        )

        if message.recipient == "broadcast":
            for agent_id, queue in self._queues.items():
                if agent_id != message.sender:
                    await queue.put(message)
        elif message.recipient in self._queues:
            await self._queues[message.recipient].put(message)
            message.status = MessageStatus.DELIVERED
        else:
            logger.warning(
                "message_undeliverable",
                message_id=message.id,
                recipient=message.recipient,
            )
            message.status = MessageStatus.FAILED

    async def subscribe(self, agent_id: str, handler: MessageHandler) -> None:
        """Subscribe an agent with a message handler."""
        self._handlers[agent_id] = handler
        self._queues[agent_id] = asyncio.Queue()
        self._tasks[agent_id] = asyncio.create_task(
            self._consume(agent_id), name=f"bus-consumer-{agent_id}"
        )
        logger.info("agent_subscribed", agent_id=agent_id)

    async def unsubscribe(self, agent_id: str) -> None:
        """Remove an agent's subscription and stop its consumer task."""
        if agent_id in self._tasks:
            self._tasks[agent_id].cancel()
            del self._tasks[agent_id]
        self._handlers.pop(agent_id, None)
        self._queues.pop(agent_id, None)
        logger.info("agent_unsubscribed", agent_id=agent_id)

    async def _consume(self, agent_id: str) -> None:
        """Consumer loop: reads messages from the agent's queue and calls handler."""
        queue = self._queues[agent_id]
        handler = self._handlers[agent_id]
        try:
            while True:
                message = await queue.get()
                try:
                    result = handler(message)
                    if inspect.isawaitable(result):
                        await result
                    message.status = MessageStatus.READ
                except Exception:
                    logger.exception(
                        "handler_error",
                        agent_id=agent_id,
                        message_id=message.id,
                    )
        except asyncio.CancelledError:
            pass

    async def shutdown(self) -> None:
        """Cancel all consumer tasks and drain queues."""
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        self._handlers.clear()
        self._queues.clear()
        logger.info("bus_shutdown")
