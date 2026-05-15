"""Shared test fixtures for Roots."""

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
    from roots import Roots
    from roots.storage.base import StorageBackend


@pytest.fixture
async def sqlite_storage() -> AsyncIterator[StorageBackend]:
    from roots.storage.sqlite import SqliteBackend

    backend = SqliteBackend(":memory:")
    await backend.initialize()
    yield backend
    await backend.close()


@pytest.fixture(params=["sqlite", "postgres"])
async def storage(request: pytest.FixtureRequest) -> AsyncIterator[StorageBackend]:
    """Parameterized fixture that yields both SQLite and PostgreSQL backends."""
    if request.param == "sqlite":
        from roots.storage.sqlite import SqliteBackend

        backend = SqliteBackend(":memory:")
        await backend.initialize()
        yield backend
        await backend.close()
    else:
        dsn = os.environ.get("ROOTS_POSTGRES_DSN")
        if not dsn:
            pytest.skip("PostgreSQL not available (ROOTS_POSTGRES_DSN not set)")
        from roots.storage.postgres import PostgresBackend

        backend = PostgresBackend(dsn)
        await backend.initialize()
        async with backend.pool.acquire() as conn:
            await conn.execute(
                "TRUNCATE process_versions, processes, agents, runs, run_history, "
                "checkpoints, escalations, decision_history, retry_state, webhooks, "
                "run_locks CASCADE"
            )
        yield backend
        await backend.close()


@pytest.fixture
def sample_process() -> ProcessDefinition:
    """A simple 2-node linear process for testing."""
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


@pytest.fixture
async def roots_instance() -> AsyncIterator["Roots"]:
    from roots import Roots
    from roots.storage.sqlite import SqliteBackend

    backend = SqliteBackend(":memory:")
    await backend.initialize()
    roots = Roots(storage=backend)

    async def echo_agent(input: dict) -> dict:  # noqa: A002
        return {"output": {"echo": input["work_item_state"]}, "escalate": False}

    await roots.register_agent("echo", echo_agent)
    yield roots
    await roots.close()
