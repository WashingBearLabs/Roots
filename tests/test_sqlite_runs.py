"""Tests for SQLite storage backend — US-003: Run Lifecycle and Work Item State."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from roots.storage.base import StorageBackend


# --- Create Run ---


async def test_create_run_returns_run_record(sqlite_storage: StorageBackend) -> None:
    """Creating a run returns RunRecord with pending status and generated ID."""
    run = await sqlite_storage.create_run("proc-1", {"key": "value"})
    assert run.id.startswith("run-")
    assert run.process_id == "proc-1"
    assert run.status == "pending"
    assert run.current_node_id is None
    assert run.work_item_state == {"key": "value"}
    assert run.created_at is not None
    assert run.updated_at is not None


async def test_create_run_unique_ids(sqlite_storage: StorageBackend) -> None:
    """Each created run gets a unique ID."""
    r1 = await sqlite_storage.create_run("proc-1", {})
    r2 = await sqlite_storage.create_run("proc-1", {})
    assert r1.id != r2.id


# --- Get Run ---


async def test_get_run_returns_record(sqlite_storage: StorageBackend) -> None:
    run = await sqlite_storage.create_run("proc-1", {"x": 1})
    loaded = await sqlite_storage.get_run(run.id)
    assert loaded is not None
    assert loaded.id == run.id
    assert loaded.process_id == "proc-1"
    assert loaded.status == "pending"
    assert loaded.work_item_state == {"x": 1}


async def test_get_run_nonexistent_returns_none(sqlite_storage: StorageBackend) -> None:
    """Non-existent run_id returns None (not exception)."""
    result = await sqlite_storage.get_run("nonexistent-id")
    assert result is None


# --- Update Run Status ---


async def test_update_run_status_persisted(sqlite_storage: StorageBackend) -> None:
    """Run status updates are persisted and retrievable."""
    run = await sqlite_storage.create_run("proc-1", {})
    await sqlite_storage.update_run_status(run.id, "running", "node-1")

    loaded = await sqlite_storage.get_run(run.id)
    assert loaded is not None
    assert loaded.status == "running"
    assert loaded.current_node_id == "node-1"
    assert loaded.updated_at > run.updated_at


async def test_update_run_status_without_node_id(
    sqlite_storage: StorageBackend,
) -> None:
    """Status can be updated without changing current_node_id."""
    run = await sqlite_storage.create_run("proc-1", {})
    await sqlite_storage.update_run_status(run.id, "running")
    await sqlite_storage.update_run_status(run.id, "completed")

    loaded = await sqlite_storage.get_run(run.id)
    assert loaded is not None
    assert loaded.status == "completed"
    assert loaded.current_node_id is None


# --- Work Item State ---


async def test_get_work_item_state(sqlite_storage: StorageBackend) -> None:
    """Work item state can be read independently."""
    run = await sqlite_storage.create_run("proc-1", {"initial": True})
    state = await sqlite_storage.get_work_item_state(run.id)
    assert state == {"initial": True}


async def test_update_work_item_state(sqlite_storage: StorageBackend) -> None:
    """Work item state can be updated independently."""
    run = await sqlite_storage.create_run("proc-1", {"step": 0})
    await sqlite_storage.update_work_item_state(run.id, {"step": 1, "result": "ok"})

    state = await sqlite_storage.get_work_item_state(run.id)
    assert state == {"step": 1, "result": "ok"}


async def test_get_work_item_state_nonexistent(
    sqlite_storage: StorageBackend,
) -> None:
    """Work item state for nonexistent run returns empty dict."""
    state = await sqlite_storage.get_work_item_state("nonexistent-id")
    assert state == {}


# --- List Runs ---


async def test_list_runs_no_filter(sqlite_storage: StorageBackend) -> None:
    """List runs with no filters returns all runs."""
    await sqlite_storage.create_run("proc-1", {})
    await sqlite_storage.create_run("proc-2", {})

    runs = await sqlite_storage.list_runs()
    assert len(runs) == 2


async def test_list_runs_filter_by_process_id(
    sqlite_storage: StorageBackend,
) -> None:
    """List runs filters correctly by process_id."""
    await sqlite_storage.create_run("proc-1", {})
    await sqlite_storage.create_run("proc-2", {})
    await sqlite_storage.create_run("proc-1", {})

    runs = await sqlite_storage.list_runs(process_id="proc-1")
    assert len(runs) == 2
    assert all(r.process_id == "proc-1" for r in runs)


async def test_list_runs_filter_by_status(sqlite_storage: StorageBackend) -> None:
    """List runs filters correctly by status."""
    r1 = await sqlite_storage.create_run("proc-1", {})
    await sqlite_storage.create_run("proc-1", {})

    await sqlite_storage.update_run_status(r1.id, "running")

    runs = await sqlite_storage.list_runs(status="running")
    assert len(runs) == 1
    assert runs[0].id == r1.id


async def test_list_runs_filter_by_both(sqlite_storage: StorageBackend) -> None:
    """List runs filters correctly by process_id and status combined."""
    r1 = await sqlite_storage.create_run("proc-1", {})
    await sqlite_storage.create_run("proc-2", {})
    await sqlite_storage.create_run("proc-1", {})

    await sqlite_storage.update_run_status(r1.id, "running")

    runs = await sqlite_storage.list_runs(process_id="proc-1", status="running")
    assert len(runs) == 1
    assert runs[0].id == r1.id


async def test_list_runs_empty(sqlite_storage: StorageBackend) -> None:
    """List runs returns empty list when no runs exist."""
    runs = await sqlite_storage.list_runs()
    assert runs == []


# --- Atomic Update ---


async def test_update_run_atomically(sqlite_storage: StorageBackend) -> None:
    """update_run_atomically updates state+status+node in a single operation."""
    run = await sqlite_storage.create_run("proc-1", {"step": 0})

    await sqlite_storage.update_run_atomically(
        run_id=run.id,
        work_item_state={"step": 1, "data": "processed"},
        status="running",
        current_node_id="node-2",
    )

    loaded = await sqlite_storage.get_run(run.id)
    assert loaded is not None
    assert loaded.status == "running"
    assert loaded.current_node_id == "node-2"
    assert loaded.work_item_state == {"step": 1, "data": "processed"}
    assert loaded.updated_at > run.updated_at


async def test_update_run_atomically_with_none_node(
    sqlite_storage: StorageBackend,
) -> None:
    """Atomic update works with None current_node_id."""
    run = await sqlite_storage.create_run("proc-1", {})
    await sqlite_storage.update_run_status(run.id, "running")
    await sqlite_storage.update_run_atomically(
        run_id=run.id,
        work_item_state={"done": True},
        status="completed",
        current_node_id=None,
    )

    loaded = await sqlite_storage.get_run(run.id)
    assert loaded is not None
    assert loaded.status == "completed"
    assert loaded.current_node_id is None
    assert loaded.work_item_state == {"done": True}


# --- Full Lifecycle ---


async def test_full_run_lifecycle(sqlite_storage: StorageBackend) -> None:
    """Tests cover full lifecycle: create -> update to running -> atomic update -> complete."""
    # Create
    run = await sqlite_storage.create_run("proc-1", {"input": "data"})
    assert run.status == "pending"

    # Update to running
    await sqlite_storage.update_run_status(run.id, "running", "start-node")
    loaded = await sqlite_storage.get_run(run.id)
    assert loaded is not None
    assert loaded.status == "running"
    assert loaded.current_node_id == "start-node"

    # Atomic update (simulating orchestrator tick)
    await sqlite_storage.update_run_atomically(
        run_id=run.id,
        work_item_state={"input": "data", "processed": True},
        status="running",
        current_node_id="process-node",
    )
    loaded = await sqlite_storage.get_run(run.id)
    assert loaded is not None
    assert loaded.status == "running"
    assert loaded.current_node_id == "process-node"
    assert loaded.work_item_state == {"input": "data", "processed": True}

    # Complete
    await sqlite_storage.update_run_atomically(
        run_id=run.id,
        work_item_state={"input": "data", "processed": True, "result": "success"},
        status="completed",
        current_node_id=None,
    )
    loaded = await sqlite_storage.get_run(run.id)
    assert loaded is not None
    assert loaded.status == "completed"
    assert loaded.current_node_id is None
    assert loaded.work_item_state["result"] == "success"

    # Verify timestamps progressed
    assert loaded.updated_at > run.created_at
