"""Tests for AgentFactory."""
import pytest
from unittest.mock import AsyncMock, patch

from aimesh.agents.factory import AgentFactory
from aimesh.core.bus import AsyncioMessageBus
from aimesh.core.registry import AgentRegistry
from aimesh.tasks.tracker import TaskTracker


@pytest.fixture
def bus():
    return AsyncioMessageBus()


@pytest.fixture
def registry():
    return AgentRegistry()


@pytest.fixture
def tracker():
    return TaskTracker()


@pytest.fixture
def factory(bus, registry, tracker):
    return AgentFactory(
        bus=bus, registry=registry, tracker=tracker,
        default_model="sonnet", default_workspace="/tmp/test",
    )


@pytest.mark.asyncio
async def test_spawn_worker_registers(factory, registry):
    """spawn_worker creates a worker visible in the registry."""
    worker = await factory.spawn_worker(
        agent_type="designer",
        soul_prompt="You are a UI designer",
        capabilities=["design", "mockup"],
    )

    assert worker.agent_id.startswith("designer-")
    assert worker.agent_type == "designer"
    assert worker.soul_prompt == "You are a UI designer"

    # Verify in registry
    found = registry.discover_by_type("designer")
    assert len(found) == 1
    assert found[0].agent_id == worker.agent_id

    await factory.teardown_all()


@pytest.mark.asyncio
async def test_teardown_worker_removes(factory, registry):
    """teardown_worker removes from registry."""
    worker = await factory.spawn_worker(
        agent_type="coder",
        soul_prompt="You code",
        capabilities=["code"],
    )
    agent_id = worker.agent_id

    assert len(registry.discover_by_type("coder")) == 1

    result = await factory.teardown_worker(agent_id)
    assert result is True
    assert len(registry.discover_by_type("coder")) == 0


@pytest.mark.asyncio
async def test_teardown_unknown_returns_false(factory):
    """teardown_worker with unknown ID returns False."""
    result = await factory.teardown_worker("nonexistent-agent")
    assert result is False


@pytest.mark.asyncio
async def test_spawn_multiple_and_teardown_all(factory, registry):
    """Spawn 3 workers, teardown all, registry empty."""
    await factory.spawn_worker("coder", "You code", ["code"])
    await factory.spawn_worker("researcher", "You research", ["research"])
    await factory.spawn_worker("designer", "You design", ["design"])

    assert len(factory.list_workers()) == 3

    await factory.teardown_all()

    assert len(factory.list_workers()) == 0
    assert len(registry.discover_by_type("coder")) == 0


@pytest.mark.asyncio
async def test_spawn_with_explicit_agent_id(factory, registry):
    """spawn_worker with explicit agent_id uses it."""
    worker = await factory.spawn_worker(
        agent_type="coder",
        soul_prompt="You code",
        capabilities=["code"],
        agent_id="my-coder-1",
    )
    assert worker.agent_id == "my-coder-1"
    await factory.teardown_all()
