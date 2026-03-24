"""Tests for SQLite storage backend — US-002."""

from __future__ import annotations

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


# --- Schema / Initialize ---


async def test_initialize_creates_tables(sqlite_storage: StorageBackend) -> None:
    """SqliteBackend(':memory:') creates all tables on initialize()."""
    from roots.storage.sqlite import SqliteBackend

    backend = sqlite_storage
    assert isinstance(backend, SqliteBackend)
    db = backend.db

    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    rows = await cursor.fetchall()
    table_names = sorted(r[0] for r in rows if r[0] != "sqlite_sequence")

    expected = sorted([
        "agents",
        "checkpoints",
        "decision_history",
        "escalations",
        "processes",
        "retry_state",
        "run_history",
        "runs",
        "webhooks",
    ])
    assert table_names == expected


# --- Process CRUD ---


async def test_save_and_get_process(
    sqlite_storage: StorageBackend, sample_process: ProcessDefinition
) -> None:
    await sqlite_storage.save_process(sample_process)
    loaded = await sqlite_storage.get_process(sample_process.id)
    assert loaded is not None
    assert loaded.id == sample_process.id
    assert loaded.name == sample_process.name
    assert loaded.version == sample_process.version
    assert loaded.description == sample_process.description


async def test_get_process_not_found(sqlite_storage: StorageBackend) -> None:
    result = await sqlite_storage.get_process("nonexistent")
    assert result is None


async def test_list_processes(
    sqlite_storage: StorageBackend, sample_process: ProcessDefinition
) -> None:
    await sqlite_storage.save_process(sample_process)
    processes = await sqlite_storage.list_processes()
    assert len(processes) == 1
    assert processes[0].id == sample_process.id


async def test_list_processes_empty(sqlite_storage: StorageBackend) -> None:
    processes = await sqlite_storage.list_processes()
    assert processes == []


async def test_delete_process(
    sqlite_storage: StorageBackend, sample_process: ProcessDefinition
) -> None:
    await sqlite_storage.save_process(sample_process)
    deleted = await sqlite_storage.delete_process(sample_process.id)
    assert deleted is True
    assert await sqlite_storage.get_process(sample_process.id) is None


async def test_delete_process_not_found(sqlite_storage: StorageBackend) -> None:
    deleted = await sqlite_storage.delete_process("nonexistent")
    assert deleted is False


async def test_process_round_trip_preserves_all_fields(
    sqlite_storage: StorageBackend,
) -> None:
    """Serialize -> store -> load -> compare preserves all fields."""
    process = ProcessDefinition(
        id="round-trip-test",
        name="Round Trip",
        version="2.0.0",
        description="Tests full round-trip fidelity",
        work_item_schema='{"type": "object"}',
        nodes=[
            NodeDefinition(
                id="start",
                type=NodeType.AGENT,
                label="Start",
                config=AgentNodeConfig(agent="test-agent", output_key="out"),
                metadata={"custom": "value"},
            ),
            NodeDefinition(
                id="end",
                type=NodeType.END,
                label="End",
                config=EndNodeConfig(status=EndStatus.COMPLETED),
            ),
        ],
        edges=[
            EdgeDefinition(from_node="start", to_node="end", label="next"),
        ],
        entry_point="start",
    )

    await sqlite_storage.save_process(process)
    loaded = await sqlite_storage.get_process(process.id)

    assert loaded is not None
    assert loaded.id == process.id
    assert loaded.name == process.name
    assert loaded.version == process.version
    assert loaded.description == process.description
    assert loaded.work_item_schema == process.work_item_schema
    assert loaded.entry_point == process.entry_point
    assert len(loaded.nodes) == len(process.nodes)
    assert len(loaded.edges) == len(process.edges)

    # Check node details
    start_node = loaded.get_node("start")
    assert start_node is not None
    assert start_node.type == NodeType.AGENT
    assert start_node.label == "Start"
    assert isinstance(start_node.config, AgentNodeConfig)
    assert start_node.config.agent == "test-agent"
    assert start_node.config.output_key == "out"
    assert start_node.metadata == {"custom": "value"}

    # Check edge details
    assert loaded.edges[0].from_node == "start"
    assert loaded.edges[0].to_node == "end"
    assert loaded.edges[0].label == "next"


async def test_save_process_overwrites(
    sqlite_storage: StorageBackend, sample_process: ProcessDefinition
) -> None:
    await sqlite_storage.save_process(sample_process)

    updated = sample_process.model_copy(update={"version": "2.0.0"})
    await sqlite_storage.save_process(updated)

    loaded = await sqlite_storage.get_process(sample_process.id)
    assert loaded is not None
    assert loaded.version == "2.0.0"


# --- Agent CRUD ---


async def test_save_and_get_agent(sqlite_storage: StorageBackend) -> None:
    agent = {"name": "echo", "type": "simple", "model": "gpt-4"}
    await sqlite_storage.save_agent(agent)
    loaded = await sqlite_storage.get_agent("echo")
    assert loaded is not None
    assert loaded["name"] == "echo"
    assert loaded["type"] == "simple"
    assert loaded["model"] == "gpt-4"


async def test_get_agent_not_found(sqlite_storage: StorageBackend) -> None:
    result = await sqlite_storage.get_agent("nonexistent")
    assert result is None


async def test_list_agents(sqlite_storage: StorageBackend) -> None:
    await sqlite_storage.save_agent({"name": "a1", "type": "simple"})
    await sqlite_storage.save_agent({"name": "a2", "type": "pool"})
    agents = await sqlite_storage.list_agents()
    assert len(agents) == 2
    names = {a["name"] for a in agents}
    assert names == {"a1", "a2"}


async def test_list_agents_empty(sqlite_storage: StorageBackend) -> None:
    agents = await sqlite_storage.list_agents()
    assert agents == []


async def test_delete_agent(sqlite_storage: StorageBackend) -> None:
    await sqlite_storage.save_agent({"name": "to-delete", "type": "simple"})
    deleted = await sqlite_storage.delete_agent("to-delete")
    assert deleted is True
    assert await sqlite_storage.get_agent("to-delete") is None


async def test_delete_agent_not_found(sqlite_storage: StorageBackend) -> None:
    deleted = await sqlite_storage.delete_agent("nonexistent")
    assert deleted is False


async def test_save_agent_overwrites(sqlite_storage: StorageBackend) -> None:
    await sqlite_storage.save_agent({"name": "echo", "type": "simple"})
    await sqlite_storage.save_agent({"name": "echo", "type": "advanced", "extra": 42})
    loaded = await sqlite_storage.get_agent("echo")
    assert loaded is not None
    assert loaded["type"] == "advanced"
    assert loaded["extra"] == 42
