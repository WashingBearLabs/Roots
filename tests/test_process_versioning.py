"""Tests for process version history storage and orchestrator pinning."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

import pytest

from roots.agents.invoker import AgentInvoker
from roots.agents.registry import AgentRegistry
from roots.core.decision import DecisionEngine
from roots.core.orchestrator import OrchestrationError, ProcessRunner
from roots.core.schema import (
    AgentNodeConfig,
    EdgeDefinition,
    EndNodeConfig,
    EndStatus,
    NodeDefinition,
    NodeType,
    ProcessDefinition,
)
from roots.core.state_machine import RunStatus
from roots.events.emitter import EventEmitter
from roots.events.sinks import EventSink
from roots.events.types import EventEnvelope

if TYPE_CHECKING:
    from roots.storage.base import StorageBackend


# --- Fixtures ---


@pytest.fixture
def sample_process() -> ProcessDefinition:
    return ProcessDefinition(
        id="proc-version-test",
        name="Version Test Process",
        version="1.0.0",
        description="Used to test versioning",
        nodes=[
            NodeDefinition(
                id="start",
                type=NodeType.AGENT,
                label="Start",
                config=AgentNodeConfig(agent="echo", output_key="result"),
            ),
            NodeDefinition(
                id="end",
                type=NodeType.END,
                label="End",
                config=EndNodeConfig(status=EndStatus.COMPLETED),
            ),
        ],
        edges=[
            EdgeDefinition(from_node="start", to_node="end"),
        ],
        entry_point="start",
    )


# --- Table creation ---


async def test_process_versions_table_created(sqlite_storage: StorageBackend) -> None:
    from roots.storage.sqlite import SqliteBackend

    backend: SqliteBackend = sqlite_storage  # type: ignore[assignment]
    cursor = await backend.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='process_versions'"
    )
    row = await cursor.fetchone()
    assert row is not None, "process_versions table was not created"


# --- save_process creates version history ---


async def test_save_process_creates_version_entry(
    sqlite_storage: StorageBackend, sample_process: ProcessDefinition
) -> None:
    await sqlite_storage.save_process(sample_process)
    versions = await sqlite_storage.list_process_versions(sample_process.id)
    assert len(versions) == 1
    assert versions[0].version == "1.0.0"
    assert versions[0].id == sample_process.id


async def test_save_process_different_versions_creates_multiple_entries(
    sqlite_storage: StorageBackend, sample_process: ProcessDefinition
) -> None:
    await sqlite_storage.save_process(sample_process)
    v2 = sample_process.model_copy(update={"version": "2.0.0"})
    await sqlite_storage.save_process(v2)
    versions = await sqlite_storage.list_process_versions(sample_process.id)
    assert len(versions) == 2
    version_strings = {v.version for v in versions}
    assert version_strings == {"1.0.0", "2.0.0"}


async def test_save_process_duplicate_version_last_write_wins(
    sqlite_storage: StorageBackend, sample_process: ProcessDefinition
) -> None:
    await sqlite_storage.save_process(sample_process)
    updated = sample_process.model_copy(update={"description": "Updated description"})
    await sqlite_storage.save_process(updated)
    versions = await sqlite_storage.list_process_versions(sample_process.id)
    assert len(versions) == 1, "Duplicate (id, version) should replace, not append"

    retrieved = await sqlite_storage.get_process_version(sample_process.id, "1.0.0")
    assert retrieved is not None
    assert retrieved.description == "Updated description"


# --- get_process_version ---


async def test_get_process_version_returns_correct_definition(
    sqlite_storage: StorageBackend, sample_process: ProcessDefinition
) -> None:
    await sqlite_storage.save_process(sample_process)
    v2 = sample_process.model_copy(update={"version": "2.0.0", "description": "V2"})
    await sqlite_storage.save_process(v2)

    result = await sqlite_storage.get_process_version(sample_process.id, "1.0.0")
    assert result is not None
    assert result.version == "1.0.0"
    assert result.description == sample_process.description

    result2 = await sqlite_storage.get_process_version(sample_process.id, "2.0.0")
    assert result2 is not None
    assert result2.version == "2.0.0"
    assert result2.description == "V2"


async def test_get_process_version_not_found_returns_none(
    sqlite_storage: StorageBackend, sample_process: ProcessDefinition
) -> None:
    await sqlite_storage.save_process(sample_process)
    result = await sqlite_storage.get_process_version(sample_process.id, "99.0.0")
    assert result is None


async def test_get_process_version_unknown_id_returns_none(
    sqlite_storage: StorageBackend,
) -> None:
    result = await sqlite_storage.get_process_version("nonexistent", "1.0.0")
    assert result is None


# --- list_process_versions ---


async def test_list_process_versions_ordered_by_created_at_desc(
    sqlite_storage: StorageBackend, sample_process: ProcessDefinition
) -> None:
    await sqlite_storage.save_process(sample_process)
    v2 = sample_process.model_copy(update={"version": "2.0.0"})
    await sqlite_storage.save_process(v2)
    v3 = sample_process.model_copy(update={"version": "3.0.0"})
    await sqlite_storage.save_process(v3)

    versions = await sqlite_storage.list_process_versions(sample_process.id)
    assert len(versions) == 3
    assert versions[0].version == "3.0.0"
    assert versions[2].version == "1.0.0"


async def test_list_process_versions_empty_for_unknown_id(
    sqlite_storage: StorageBackend,
) -> None:
    versions = await sqlite_storage.list_process_versions("nonexistent")
    assert versions == []


# --- delete_process cascades to version history ---


async def test_delete_process_removes_version_history(
    sqlite_storage: StorageBackend, sample_process: ProcessDefinition
) -> None:
    await sqlite_storage.save_process(sample_process)
    v2 = sample_process.model_copy(update={"version": "2.0.0"})
    await sqlite_storage.save_process(v2)

    versions = await sqlite_storage.list_process_versions(sample_process.id)
    assert len(versions) == 2

    await sqlite_storage.delete_process(sample_process.id)

    versions_after = await sqlite_storage.list_process_versions(sample_process.id)
    assert versions_after == []


async def test_delete_process_only_removes_its_own_versions(
    sqlite_storage: StorageBackend, sample_process: ProcessDefinition
) -> None:
    other = sample_process.model_copy(update={"id": "other-proc"})
    await sqlite_storage.save_process(sample_process)
    await sqlite_storage.save_process(other)

    await sqlite_storage.delete_process(sample_process.id)

    remaining = await sqlite_storage.list_process_versions("other-proc")
    assert len(remaining) == 1


# --- Backward compatibility ---


async def test_get_process_still_returns_latest(
    sqlite_storage: StorageBackend, sample_process: ProcessDefinition
) -> None:
    await sqlite_storage.save_process(sample_process)
    v2 = sample_process.model_copy(update={"version": "2.0.0"})
    await sqlite_storage.save_process(v2)

    latest = await sqlite_storage.get_process(sample_process.id)
    assert latest is not None
    assert latest.version == "2.0.0"


async def test_list_processes_unaffected_by_versioning(
    sqlite_storage: StorageBackend, sample_process: ProcessDefinition
) -> None:
    await sqlite_storage.save_process(sample_process)
    v2 = sample_process.model_copy(update={"version": "2.0.0"})
    await sqlite_storage.save_process(v2)

    processes = await sqlite_storage.list_processes()
    assert len(processes) == 1
    assert processes[0].id == sample_process.id


# --- US-002: Run version pinning ---


async def test_create_run_stores_process_version(
    sqlite_storage: StorageBackend, sample_process: ProcessDefinition
) -> None:
    """create_run with a version stores it on the RunRecord."""
    await sqlite_storage.save_process(sample_process)
    run = await sqlite_storage.create_run(
        sample_process.id, {}, process_version="1.0.0"
    )
    assert run.process_version == "1.0.0"


async def test_create_run_without_version_is_none(
    sqlite_storage: StorageBackend,
) -> None:
    """create_run without a version stores None (backward compat)."""
    run = await sqlite_storage.create_run("proc-1", {})
    assert run.process_version is None


async def test_get_run_returns_process_version(
    sqlite_storage: StorageBackend, sample_process: ProcessDefinition
) -> None:
    """get_run round-trips process_version from storage."""
    await sqlite_storage.save_process(sample_process)
    run = await sqlite_storage.create_run(
        sample_process.id, {}, process_version="1.0.0"
    )
    loaded = await sqlite_storage.get_run(run.id)
    assert loaded is not None
    assert loaded.process_version == "1.0.0"


async def test_list_runs_returns_process_version(
    sqlite_storage: StorageBackend, sample_process: ProcessDefinition
) -> None:
    """list_runs includes process_version on each RunRecord."""
    await sqlite_storage.save_process(sample_process)
    await sqlite_storage.create_run(sample_process.id, {}, process_version="1.0.0")
    await sqlite_storage.create_run(sample_process.id, {})

    runs = await sqlite_storage.list_runs(process_id=sample_process.id)
    versions = {r.process_version for r in runs}
    assert versions == {"1.0.0", None}


async def test_run_pinned_to_original_version(
    sqlite_storage: StorageBackend, sample_process: ProcessDefinition
) -> None:
    """A run created at v1 retrieves v1 definition even after process updated to v2."""
    await sqlite_storage.save_process(sample_process)
    run = await sqlite_storage.create_run(
        sample_process.id, {}, process_version="1.0.0"
    )

    # Update process to v2
    v2 = sample_process.model_copy(update={"version": "2.0.0", "description": "V2"})
    await sqlite_storage.save_process(v2)

    # Run still has its pinned version
    loaded = await sqlite_storage.get_run(run.id)
    assert loaded is not None
    assert loaded.process_version == "1.0.0"

    # And the pinned version retrieves the original definition
    pinned = await sqlite_storage.get_process_version(
        loaded.process_id, loaded.process_version  # type: ignore[arg-type]
    )
    assert pinned is not None
    assert pinned.version == "1.0.0"
    assert pinned.description == sample_process.description

    # While the latest is v2
    latest = await sqlite_storage.get_process(sample_process.id)
    assert latest is not None
    assert latest.version == "2.0.0"


# --- Orchestrator-level version pinning tests ---


class CollectorSink(EventSink):
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def emit(self, event: EventEnvelope) -> None:
        self.events.append(event)


async def echo_agent(input: dict[str, Any]) -> dict[str, Any]:
    return {"output": {"echo": input["work_item_state"]}, "escalate": False}


@pytest.fixture
def registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg.register_local("echo", echo_agent)
    return reg


@pytest.fixture
def invoker(registry: AgentRegistry) -> AgentInvoker:
    return AgentInvoker(registry)


@pytest.fixture
def decision_engine() -> DecisionEngine:
    return DecisionEngine(default_model="gpt-4o-mini")


@pytest.fixture
def sink() -> CollectorSink:
    return CollectorSink()


@pytest.fixture
def emitter(sink: CollectorSink) -> EventEmitter:
    return EventEmitter(sinks=[sink])


@pytest.mark.asyncio
async def test_orchestrator_tick_uses_pinned_version(
    sqlite_storage: StorageBackend,
    sample_process: ProcessDefinition,
    invoker: AgentInvoker,
    decision_engine: DecisionEngine,
    emitter: EventEmitter,
) -> None:
    """Create run at v1, update process to v2, tick — run uses v1 definition."""
    await sqlite_storage.save_process(sample_process)
    run = await sqlite_storage.create_run(
        sample_process.id, {"input": "test"}, process_version="1.0.0"
    )

    v2 = sample_process.model_copy(
        update={"version": "2.0.0", "description": "V2 changed"}
    )
    await sqlite_storage.save_process(v2)

    runner = ProcessRunner(
        run_id=run.id,
        storage=sqlite_storage,
        agent_invoker=invoker,
        decision_engine=decision_engine,
        event_emitter=emitter,
        owner_id="test-owner",
    )

    await runner.tick()  # pending -> running
    await runner.tick()  # execute agent node using v1 definition
    await runner.tick()  # end node

    final = await sqlite_storage.get_run(run.id)
    assert final is not None
    assert final.status == RunStatus.COMPLETED
    assert final.process_version == "1.0.0"


@pytest.mark.asyncio
async def test_orchestrator_tick_fails_on_deleted_pinned_version(
    sqlite_storage: StorageBackend,
    sample_process: ProcessDefinition,
    invoker: AgentInvoker,
    decision_engine: DecisionEngine,
    emitter: EventEmitter,
) -> None:
    """If pinned version is deleted, orchestrator raises OrchestrationError."""
    await sqlite_storage.save_process(sample_process)
    run = await sqlite_storage.create_run(
        sample_process.id, {"input": "test"}, process_version="1.0.0"
    )

    await sqlite_storage.delete_process(sample_process.id)

    v2 = sample_process.model_copy(update={"version": "2.0.0"})
    await sqlite_storage.save_process(v2)

    runner = ProcessRunner(
        run_id=run.id,
        storage=sqlite_storage,
        agent_invoker=invoker,
        decision_engine=decision_engine,
        event_emitter=emitter,
        owner_id="test-owner",
    )

    with pytest.raises(OrchestrationError, match="Pinned version.*not found"):
        await runner.tick()
