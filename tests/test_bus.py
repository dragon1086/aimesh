"""Tests for AsyncioMessageBus."""

import asyncio

from aimesh.core.bus import AsyncioMessageBus
from aimesh.core.message import MeshMessage, MessageStatus, MessageType


async def test_subscribe_and_publish_targeted():
    bus = AsyncioMessageBus()
    received = []

    async def handler(msg: MeshMessage):
        received.append(msg)

    await bus.subscribe("agent-1", handler)

    msg = MeshMessage(
        sender="pm", recipient="agent-1", msg_type=MessageType.TASK_ASSIGN, content="Do work"
    )
    await bus.publish(msg)
    await asyncio.sleep(0.05)  # let consumer process

    assert len(received) == 1
    assert received[0].content == "Do work"
    assert msg.status == MessageStatus.READ  # DELIVERED -> READ after handler processes
    await bus.shutdown()


async def test_publish_broadcast():
    bus = AsyncioMessageBus()
    received_1 = []
    received_2 = []

    async def handler_1(msg: MeshMessage):
        received_1.append(msg)

    async def handler_2(msg: MeshMessage):
        received_2.append(msg)

    await bus.subscribe("agent-1", handler_1)
    await bus.subscribe("agent-2", handler_2)

    msg = MeshMessage(
        sender="pm", recipient="broadcast", msg_type=MessageType.SYSTEM, content="Announcement"
    )
    await bus.publish(msg)
    await asyncio.sleep(0.05)

    assert len(received_1) == 1
    assert len(received_2) == 1
    await bus.shutdown()


async def test_broadcast_excludes_sender():
    bus = AsyncioMessageBus()
    received = []

    async def handler(msg: MeshMessage):
        received.append(msg)

    await bus.subscribe("pm", handler)

    msg = MeshMessage(
        sender="pm", recipient="broadcast", msg_type=MessageType.SYSTEM, content="Test"
    )
    await bus.publish(msg)
    await asyncio.sleep(0.05)

    assert len(received) == 0  # sender should not receive own broadcast
    await bus.shutdown()


async def test_targeted_message_not_delivered_to_others():
    bus = AsyncioMessageBus()
    received_1 = []
    received_2 = []

    async def handler_1(msg: MeshMessage):
        received_1.append(msg)

    async def handler_2(msg: MeshMessage):
        received_2.append(msg)

    await bus.subscribe("agent-1", handler_1)
    await bus.subscribe("agent-2", handler_2)

    msg = MeshMessage(
        sender="pm", recipient="agent-1", msg_type=MessageType.TASK_ASSIGN, content="Only for 1"
    )
    await bus.publish(msg)
    await asyncio.sleep(0.05)

    assert len(received_1) == 1
    assert len(received_2) == 0
    await bus.shutdown()


async def test_unsubscribe():
    bus = AsyncioMessageBus()
    received = []

    async def handler(msg: MeshMessage):
        received.append(msg)

    await bus.subscribe("agent-1", handler)
    await bus.unsubscribe("agent-1")

    msg = MeshMessage(
        sender="pm", recipient="agent-1", msg_type=MessageType.TASK_ASSIGN, content="Test"
    )
    await bus.publish(msg)
    await asyncio.sleep(0.05)

    assert len(received) == 0
    assert msg.status == MessageStatus.FAILED  # undeliverable
    await bus.shutdown()


async def test_publish_to_unknown_recipient():
    bus = AsyncioMessageBus()

    msg = MeshMessage(
        sender="pm", recipient="nonexistent", msg_type=MessageType.TASK_ASSIGN, content="Test"
    )
    await bus.publish(msg)

    assert msg.status == MessageStatus.FAILED
    await bus.shutdown()
