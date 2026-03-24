"""Shared test fixtures for Roots."""

from __future__ import annotations

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


@pytest.fixture
async def sqlite_storage() -> AsyncIterator[StorageBackend]:
    from roots.storage.sqlite import SqliteBackend

    backend = SqliteBackend(":memory:")
    await backend.initialize()
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


# TODO: roots_instance fixture — created in T1.3 US-007 after Roots class exists
