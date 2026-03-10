"""Shared test fixtures for AI Mesh."""

import pytest

from aimesh.core.bus import AsyncioMessageBus
from aimesh.core.context import ContextStore
from aimesh.core.registry import AgentRegistry


@pytest.fixture
def bus():
    return AsyncioMessageBus()


@pytest.fixture
def registry():
    return AgentRegistry()


@pytest.fixture
async def context_store(tmp_path):
    store = ContextStore(db_path=str(tmp_path / "test.db"))
    await store.initialize()
    yield store
    await store.close()
