"""Tests for retry exhaustion route mode integration (US-003).

Verifies that exhausted retries with on_exhaustion=route:
- Route to the fallback edge target node instead of failing the run
- Keep the run in 'running' status (not 'failed')
- Continue execution from the fallback target node
- Emit roots.node.failed event with fallback metadata
"""

from __future__ import annotations

from typing import Any

import pytest

from roots.agents.invoker import AgentInvoker
from roots.agents.registry import AgentRegistry
from roots.core.decision import DecisionEngine
from roots.core.orchestrator import ProcessRunner
from roots.core.schema import (
    AgentNodeConfig,
    BackoffStrategy,
    EdgeDefinition,
    EndNodeConfig,
    EndStatus,
    NodeDefinition,
    NodeType,
    OnExhaustion,
    RetryConfig,
    ProcessDefinition,
)
from roots.core.state_machine import RunStatus
from roots.events.emitter import EventEmitter
from roots.events.sinks import EventSink
from roots.events.types import EventEnvelope
from roots.storage.base import StorageBackend


# --- Helpers ---


class CollectorSink(EventSink):
    """Collects emitted events for assertion."""

    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def emit(self, event: EventEnvelope) -> None:
        self.events.append(event)


async def always_fail_agent(input: dict[str, Any]) -> dict[str, Any]:
    """Agent that always raises a retryable error."""
    raise RuntimeError("primary agent crashed")


async def fallback_agent(input: dict[str, Any]) -> dict[str, Any]:
    """Fallback agent that always succeeds."""
    return {"output": {"fallback": True, "handled": True}, "escalate": False}


def make_route_process(max_attempts: int = 2) -> ProcessDefinition:
    """Primary agent (with route on exhaustion) → fallback agent → End."""
    return ProcessDefinition(
        id="route-proc",
        name="Route Process",
        version="1.0.0",
        nodes=[
            NodeDefinition(
                id="primary-agent",
                type=NodeType.AGENT,
                label="Primary Agent",
                config=AgentNodeConfig(agent="always_fail", output_key="result"),
                retry=RetryConfig(
                    max_attempts=max_attempts,
                    backoff=BackoffStrategy.FIXED,
                    backoff_seconds=0.0,
                    on_exhaustion=OnExhaustion.ROUTE,
                    fallback_edge="fallback-agent",
                ),
            ),
            NodeDefinition(
                id="fallback-agent",
                type=NodeType.AGENT,
                label="Fallback Agent",
                config=AgentNodeConfig(agent="fallback", output_key="fallback_result"),
            ),
            NodeDefinition(
                id="done",
                type=NodeType.END,
                label="Done",
                config=EndNodeConfig(status=EndStatus.COMPLETED),
            ),
        ],
        edges=[
            EdgeDefinition(from_node="primary-agent", to_node="done"),
            EdgeDefinition(from_node="fallback-agent", to_node="done"),
        ],
        entry_point="primary-agent",
    )


@pytest.fixture
async def sqlite_storage():
    from roots.storage.sqlite import SqliteBackend

    backend = SqliteBackend(":memory:")
    await backend.initialize()
    yield backend
    await backend.close()


@pytest.fixture
def registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg.register_local("always_fail", always_fail_agent)
    reg.register_local("fallback", fallback_agent)
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


async def _setup_run(
    storage: StorageBackend,
    process: ProcessDefinition,
) -> str:
    """Save process and create a pending run, return run_id."""
    await storage.save_process(process)
    run = await storage.create_run(process.id, {"input": "test"})
    return run.id


def _make_runner(
    run_id: str,
    storage: StorageBackend,
    invoker: AgentInvoker,
    decision_engine: DecisionEngine,
    emitter: EventEmitter,
) -> ProcessRunner:
    return ProcessRunner(
        run_id=run_id,
        storage=storage,
        agent_invoker=invoker,
        decision_engine=decision_engine,
        event_emitter=emitter,
        owner_id="test-owner",
    )


# --- Tests ---


class TestRetryExhaustionRouteIntegration:
    @pytest.mark.asyncio
    async def test_run_continues_via_fallback_edge(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Run continues via fallback edge when retries exhaust with on_exhaustion=route."""
        process = make_route_process(max_attempts=2)
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_run_does_not_transition_to_failed(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Run does NOT transition to 'failed' when on_exhaustion=route."""
        process = make_route_process(max_attempts=2)
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()
        await emitter.close()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status != RunStatus.FAILED

    @pytest.mark.asyncio
    async def test_execution_continues_from_fallback_node(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        """Execution continues from the fallback target node."""
        process = make_route_process(max_attempts=2)
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()
        await emitter.close()

        # Check that fallback-agent node was entered and completed
        entered_nodes = [
            e.node_id for e in sink.events if e.event == "roots.node.entered"
        ]
        completed_nodes = [
            e.node_id for e in sink.events if e.event == "roots.node.completed"
        ]
        assert "fallback-agent" in entered_nodes
        assert "fallback-agent" in completed_nodes

        # Verify the end node was also reached
        assert "done" in entered_nodes

    @pytest.mark.asyncio
    async def test_event_metadata_indicates_fallback_routing(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        """Event metadata indicates fallback routing occurred."""
        process = make_route_process(max_attempts=2)
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()
        await emitter.close()

        node_failed = [
            e for e in sink.events if e.event == "roots.node.failed"
        ]
        assert len(node_failed) == 1
        assert node_failed[0].node_id == "primary-agent"
        assert node_failed[0].metadata["fallback"] is True
        assert node_failed[0].metadata["fallback_edge"] == "fallback-agent"
        assert "error" in node_failed[0].metadata
        assert "primary agent crashed" in node_failed[0].metadata["error"]

    @pytest.mark.asyncio
    async def test_fallback_routing_with_subsequent_node_execution(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        """Tests verify fallback routing with subsequent node execution."""
        process = make_route_process(max_attempts=2)
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()
        await emitter.close()

        # Verify complete execution path: primary fails → fallback runs → end
        history = await sqlite_storage.list_history_events(run_id)
        event_types = [(h.node_id, h.event_type) for h in history]

        # Primary agent entered then failed
        assert ("primary-agent", "entered") in event_types
        assert ("primary-agent", "failed") in event_types

        # Fallback agent entered and completed
        assert ("fallback-agent", "entered") in event_types
        assert ("fallback-agent", "completed") in event_types

        # End node entered and completed
        assert ("done", "entered") in event_types
        assert ("done", "completed") in event_types

        # Run completed successfully
        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_no_run_failed_event_emitted(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        """roots.run.failed should NOT be emitted when routing to fallback."""
        process = make_route_process(max_attempts=2)
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()
        await emitter.close()

        event_types = [e.event for e in sink.events]
        assert "roots.run.failed" not in event_types
        assert "roots.run.completed" in event_types
