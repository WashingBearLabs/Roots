"""Tests for PostgreSQL storage backend — US-008."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import pytest

from roots.core.schema import (
    AgentNodeConfig,
    EdgeDefinition,
    EndNodeConfig,
    EndStatus,
    NodeDefinition,
    NodeType,
    ProcessDefinition,
)

if TYPE_CHECKING:
    from roots.storage.base import StorageBackend

pytestmark = pytest.mark.skipif(
    not os.environ.get("ROOTS_POSTGRES_DSN"),
    reason="PostgreSQL not available",
)


@pytest.fixture
async def pg_storage() -> AsyncIterator[StorageBackend]:
    from roots.storage.postgres import PostgresBackend

    dsn = os.environ["ROOTS_POSTGRES_DSN"]
    backend = PostgresBackend(dsn)
    await backend.initialize()
    # Clean tables before each test
    async with backend.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE processes, agents, runs, run_history, checkpoints, "
            "escalations, decision_history, retry_state, webhooks, "
            "run_locks CASCADE"
        )
    yield backend
    await backend.close()


@pytest.fixture
def sample_process() -> ProcessDefinition:
    return ProcessDefinition(
        id="test-process-1",
        name="Test Process",
        version="1.0.0",
        description="A simple test process",
        nodes=[
            NodeDefinition(
                id="start",
                type=NodeType.AGENT,
                label="Start Node",
                config=AgentNodeConfig(agent="echo", output_key="result"),
            ),
            NodeDefinition(
                id="end",
                type=NodeType.END,
                label="End Node",
                config=EndNodeConfig(status=EndStatus.COMPLETED),
            ),
        ],
        edges=[
            EdgeDefinition(from_node="start", to_node="end"),
        ],
        entry_point="start",
    )


# --- Schema / Initialize ---


async def test_initialize_creates_tables(pg_storage: StorageBackend) -> None:
    """PostgresBackend creates all tables on initialize()."""
    from roots.storage.postgres import PostgresBackend

    backend = pg_storage
    assert isinstance(backend, PostgresBackend)

    async with backend.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        )
    table_names = sorted(r["tablename"] for r in rows)

    expected = sorted([
        "agents",
        "checkpoints",
        "decision_history",
        "escalations",
        "processes",
        "retry_state",
        "run_history",
        "run_locks",
        "runs",
        "webhooks",
    ])
    assert table_names == expected


# --- Process CRUD ---


async def test_save_and_get_process(
    pg_storage: StorageBackend, sample_process: ProcessDefinition
) -> None:
    await pg_storage.save_process(sample_process)
    loaded = await pg_storage.get_process(sample_process.id)
    assert loaded is not None
    assert loaded.id == sample_process.id
    assert loaded.name == sample_process.name
    assert loaded.version == sample_process.version
    assert loaded.description == sample_process.description


async def test_get_process_not_found(pg_storage: StorageBackend) -> None:
    result = await pg_storage.get_process("nonexistent")
    assert result is None


async def test_list_processes(
    pg_storage: StorageBackend, sample_process: ProcessDefinition
) -> None:
    await pg_storage.save_process(sample_process)
    processes = await pg_storage.list_processes()
    assert len(processes) == 1
    assert processes[0].id == sample_process.id


async def test_list_processes_empty(pg_storage: StorageBackend) -> None:
    processes = await pg_storage.list_processes()
    assert processes == []


async def test_delete_process(
    pg_storage: StorageBackend, sample_process: ProcessDefinition
) -> None:
    await pg_storage.save_process(sample_process)
    deleted = await pg_storage.delete_process(sample_process.id)
    assert deleted is True
    assert await pg_storage.get_process(sample_process.id) is None


async def test_delete_process_not_found(pg_storage: StorageBackend) -> None:
    deleted = await pg_storage.delete_process("nonexistent")
    assert deleted is False


async def test_save_process_overwrites(
    pg_storage: StorageBackend, sample_process: ProcessDefinition
) -> None:
    await pg_storage.save_process(sample_process)
    updated = sample_process.model_copy(update={"version": "2.0.0"})
    await pg_storage.save_process(updated)
    loaded = await pg_storage.get_process(sample_process.id)
    assert loaded is not None
    assert loaded.version == "2.0.0"


# --- Agent CRUD ---


async def test_save_and_get_agent(pg_storage: StorageBackend) -> None:
    agent = {"name": "echo", "type": "simple", "model": "gpt-4"}
    await pg_storage.save_agent(agent)
    loaded = await pg_storage.get_agent("echo")
    assert loaded is not None
    assert loaded["name"] == "echo"
    assert loaded["type"] == "simple"
    assert loaded["model"] == "gpt-4"


async def test_get_agent_not_found(pg_storage: StorageBackend) -> None:
    result = await pg_storage.get_agent("nonexistent")
    assert result is None


async def test_list_agents(pg_storage: StorageBackend) -> None:
    await pg_storage.save_agent({"name": "a1", "type": "simple"})
    await pg_storage.save_agent({"name": "a2", "type": "pool"})
    agents = await pg_storage.list_agents()
    assert len(agents) == 2
    names = {a["name"] for a in agents}
    assert names == {"a1", "a2"}


async def test_list_agents_empty(pg_storage: StorageBackend) -> None:
    agents = await pg_storage.list_agents()
    assert agents == []


async def test_delete_agent(pg_storage: StorageBackend) -> None:
    await pg_storage.save_agent({"name": "to-delete", "type": "simple"})
    deleted = await pg_storage.delete_agent("to-delete")
    assert deleted is True
    assert await pg_storage.get_agent("to-delete") is None


async def test_delete_agent_not_found(pg_storage: StorageBackend) -> None:
    deleted = await pg_storage.delete_agent("nonexistent")
    assert deleted is False


async def test_save_agent_overwrites(pg_storage: StorageBackend) -> None:
    await pg_storage.save_agent({"name": "echo", "type": "simple"})
    await pg_storage.save_agent({"name": "echo", "type": "advanced", "extra": 42})
    loaded = await pg_storage.get_agent("echo")
    assert loaded is not None
    assert loaded["type"] == "advanced"
    assert loaded["extra"] == 42


# --- Run Lifecycle ---


async def test_create_run_returns_run_record(pg_storage: StorageBackend) -> None:
    run = await pg_storage.create_run("proc-1", {"key": "value"})
    assert run.id.startswith("run-")
    assert run.process_id == "proc-1"
    assert run.status == "pending"
    assert run.current_node_id is None
    assert run.work_item_state == {"key": "value"}
    assert run.created_at is not None
    assert run.updated_at is not None


async def test_create_run_unique_ids(pg_storage: StorageBackend) -> None:
    r1 = await pg_storage.create_run("proc-1", {})
    r2 = await pg_storage.create_run("proc-1", {})
    assert r1.id != r2.id


async def test_get_run_returns_record(pg_storage: StorageBackend) -> None:
    run = await pg_storage.create_run("proc-1", {"x": 1})
    loaded = await pg_storage.get_run(run.id)
    assert loaded is not None
    assert loaded.id == run.id
    assert loaded.process_id == "proc-1"
    assert loaded.status == "pending"
    assert loaded.work_item_state == {"x": 1}


async def test_get_run_nonexistent_returns_none(pg_storage: StorageBackend) -> None:
    result = await pg_storage.get_run("nonexistent-id")
    assert result is None


async def test_update_run_status_persisted(pg_storage: StorageBackend) -> None:
    run = await pg_storage.create_run("proc-1", {})
    await pg_storage.update_run_status(run.id, "running", "node-1")
    loaded = await pg_storage.get_run(run.id)
    assert loaded is not None
    assert loaded.status == "running"
    assert loaded.current_node_id == "node-1"
    assert loaded.updated_at > run.updated_at


async def test_update_run_status_without_node_id(pg_storage: StorageBackend) -> None:
    run = await pg_storage.create_run("proc-1", {})
    await pg_storage.update_run_status(run.id, "completed")
    loaded = await pg_storage.get_run(run.id)
    assert loaded is not None
    assert loaded.status == "completed"
    assert loaded.current_node_id is None


# --- Work Item State ---


async def test_get_work_item_state(pg_storage: StorageBackend) -> None:
    run = await pg_storage.create_run("proc-1", {"initial": True})
    state = await pg_storage.get_work_item_state(run.id)
    assert state == {"initial": True}


async def test_update_work_item_state(pg_storage: StorageBackend) -> None:
    run = await pg_storage.create_run("proc-1", {"step": 0})
    await pg_storage.update_work_item_state(run.id, {"step": 1, "result": "ok"})
    state = await pg_storage.get_work_item_state(run.id)
    assert state == {"step": 1, "result": "ok"}


async def test_get_work_item_state_nonexistent(pg_storage: StorageBackend) -> None:
    state = await pg_storage.get_work_item_state("nonexistent-id")
    assert state == {}


# --- List Runs ---


async def test_list_runs_no_filter(pg_storage: StorageBackend) -> None:
    await pg_storage.create_run("proc-1", {})
    await pg_storage.create_run("proc-2", {})
    runs = await pg_storage.list_runs()
    assert len(runs) == 2


async def test_list_runs_filter_by_process_id(pg_storage: StorageBackend) -> None:
    await pg_storage.create_run("proc-1", {})
    await pg_storage.create_run("proc-2", {})
    await pg_storage.create_run("proc-1", {})
    runs = await pg_storage.list_runs(process_id="proc-1")
    assert len(runs) == 2
    assert all(r.process_id == "proc-1" for r in runs)


async def test_list_runs_filter_by_status(pg_storage: StorageBackend) -> None:
    r1 = await pg_storage.create_run("proc-1", {})
    await pg_storage.create_run("proc-1", {})
    await pg_storage.update_run_status(r1.id, "running")
    runs = await pg_storage.list_runs(status="running")
    assert len(runs) == 1
    assert runs[0].id == r1.id


async def test_list_runs_filter_by_both(pg_storage: StorageBackend) -> None:
    r1 = await pg_storage.create_run("proc-1", {})
    await pg_storage.create_run("proc-2", {})
    await pg_storage.create_run("proc-1", {})
    await pg_storage.update_run_status(r1.id, "running")
    runs = await pg_storage.list_runs(process_id="proc-1", status="running")
    assert len(runs) == 1
    assert runs[0].id == r1.id


async def test_list_runs_empty(pg_storage: StorageBackend) -> None:
    runs = await pg_storage.list_runs()
    assert runs == []


# --- Atomic Update ---


async def test_update_run_atomically(pg_storage: StorageBackend) -> None:
    run = await pg_storage.create_run("proc-1", {"step": 0})
    await pg_storage.update_run_atomically(
        run_id=run.id,
        work_item_state={"step": 1, "data": "processed"},
        status="running",
        current_node_id="node-2",
    )
    loaded = await pg_storage.get_run(run.id)
    assert loaded is not None
    assert loaded.status == "running"
    assert loaded.current_node_id == "node-2"
    assert loaded.work_item_state == {"step": 1, "data": "processed"}
    assert loaded.updated_at > run.updated_at


async def test_update_run_atomically_with_none_node(pg_storage: StorageBackend) -> None:
    run = await pg_storage.create_run("proc-1", {})
    await pg_storage.update_run_atomically(
        run_id=run.id,
        work_item_state={"done": True},
        status="completed",
        current_node_id=None,
    )
    loaded = await pg_storage.get_run(run.id)
    assert loaded is not None
    assert loaded.status == "completed"
    assert loaded.current_node_id is None
    assert loaded.work_item_state == {"done": True}


# --- Full Lifecycle ---


async def test_full_run_lifecycle(pg_storage: StorageBackend) -> None:
    run = await pg_storage.create_run("proc-1", {"input": "data"})
    assert run.status == "pending"

    await pg_storage.update_run_status(run.id, "running", "start-node")
    loaded = await pg_storage.get_run(run.id)
    assert loaded is not None
    assert loaded.status == "running"
    assert loaded.current_node_id == "start-node"

    await pg_storage.update_run_atomically(
        run_id=run.id,
        work_item_state={"input": "data", "processed": True},
        status="running",
        current_node_id="process-node",
    )
    loaded = await pg_storage.get_run(run.id)
    assert loaded is not None
    assert loaded.current_node_id == "process-node"

    await pg_storage.update_run_atomically(
        run_id=run.id,
        work_item_state={"input": "data", "processed": True, "result": "success"},
        status="completed",
        current_node_id=None,
    )
    loaded = await pg_storage.get_run(run.id)
    assert loaded is not None
    assert loaded.status == "completed"
    assert loaded.work_item_state["result"] == "success"


# --- Advisory Lock Specific ---


async def test_advisory_lock_auto_release_on_disconnect(
    pg_storage: StorageBackend,
) -> None:
    """Advisory locks auto-release when the holding connection drops."""
    from roots.storage.postgres import PostgresBackend

    backend: PostgresBackend = pg_storage  # type: ignore[assignment]

    run = await backend.create_run("proc-1", {})
    acquired = await backend.acquire_run_lock(run.id, "owner-1")
    assert acquired is True

    # Forcibly close the pinned connection (simulates crash / disconnect)
    pinned_conn = backend._lock_connections.pop(run.id)
    await pinned_conn.close()

    # Clean up the tracking table entry (would be stale after crash)
    async with backend.pool.acquire() as conn:
        await conn.execute("DELETE FROM run_locks WHERE run_id = $1", run.id)

    # Advisory lock was auto-released — a new owner can acquire it
    acquired2 = await backend.acquire_run_lock(run.id, "owner-2")
    assert acquired2 is True

    locked_by, _ = await backend.check_run_lock(run.id)
    assert locked_by == "owner-2"
