"""Tests for shared context store."""

from aimesh.core.context import ContextStore


async def test_write_and_read(tmp_path):
    store = ContextStore(db_path=str(tmp_path / "test.db"))
    await store.initialize()

    await store.write("project.framework", "FastAPI", agent_id="coder-1", task_id="t-1")
    value = await store.read("project.framework")
    assert value == "FastAPI"

    await store.close()


async def test_read_nonexistent(tmp_path):
    store = ContextStore(db_path=str(tmp_path / "test.db"))
    await store.initialize()

    value = await store.read("nonexistent")
    assert value is None

    await store.close()


async def test_upsert(tmp_path):
    store = ContextStore(db_path=str(tmp_path / "test.db"))
    await store.initialize()

    await store.write("key", "value1", agent_id="a1")
    await store.write("key", "value2", agent_id="a2")
    value = await store.read("key")
    assert value == "value2"

    await store.close()


async def test_query_by_prefix(tmp_path):
    store = ContextStore(db_path=str(tmp_path / "test.db"))
    await store.initialize()

    await store.write("project.framework", "FastAPI", agent_id="a1")
    await store.write("project.db", "PostgreSQL", agent_id="a1")
    await store.write("other.key", "value", agent_id="a1")

    results = await store.query(prefix="project.")
    assert len(results) == 2

    await store.close()


async def test_query_by_agent_id(tmp_path):
    store = ContextStore(db_path=str(tmp_path / "test.db"))
    await store.initialize()

    await store.write("k1", "v1", agent_id="coder-1")
    await store.write("k2", "v2", agent_id="researcher-1")

    results = await store.query(agent_id="coder-1")
    assert len(results) == 1
    assert results[0].key == "k1"

    await store.close()


async def test_query_by_task_id(tmp_path):
    store = ContextStore(db_path=str(tmp_path / "test.db"))
    await store.initialize()

    await store.write("k1", "v1", agent_id="a1", task_id="task-1")
    await store.write("k2", "v2", agent_id="a1", task_id="task-2")

    results = await store.query(task_id="task-1")
    assert len(results) == 1

    await store.close()


async def test_persistence_across_reopen(tmp_path):
    db_path = str(tmp_path / "test.db")

    store = ContextStore(db_path=db_path)
    await store.initialize()
    await store.write("persistent", "data", agent_id="a1")
    await store.close()

    store2 = ContextStore(db_path=db_path)
    await store2.initialize()
    value = await store2.read("persistent")
    assert value == "data"
    await store2.close()
