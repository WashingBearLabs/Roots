"""Tests for retry exhaustion fail mode integration (US-002).

Verifies that exhausted retries with on_exhaustion=fail:
- Transition the run to 'failed'
- Emit roots.node.failed and roots.run.failed events
- Include last error in failure metadata
- Record all attempts and the final failure in run history
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


fail_count = 0


async def always_fail_agent(input: dict[str, Any]) -> dict[str, Any]:
    """Agent that always raises a retryable error."""
    raise RuntimeError("agent crashed")


async def echo_agent(input: dict[str, Any]) -> dict[str, Any]:
    return {"output": {"echo": input["work_item_state"]}, "escalate": False}


def make_retry_process(
    max_attempts: int = 3,
    on_exhaustion: OnExhaustion = OnExhaustion.FAIL,
) -> ProcessDefinition:
    """Agent with retry → End."""
    return ProcessDefinition(
        id="retry-proc",
        name="Retry Process",
        version="1.0.0",
        nodes=[
            NodeDefinition(
                id="retry-agent",
                type=NodeType.AGENT,
                label="Retry Agent",
                config=AgentNodeConfig(agent="always_fail", output_key="result"),
                retry=RetryConfig(
                    max_attempts=max_attempts,
                    backoff=BackoffStrategy.FIXED,
                    backoff_seconds=0.0,
                    on_exhaustion=on_exhaustion,
                ),
            ),
            NodeDefinition(
                id="done",
                type=NodeType.END,
                label="Done",
                config=EndNodeConfig(status=EndStatus.COMPLETED),
            ),
        ],
        edges=[EdgeDefinition(from_node="retry-agent", to_node="done")],
        entry_point="retry-agent",
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


class TestRetryExhaustionFailIntegration:
    @pytest.mark.asyncio
    async def test_run_transitions_to_failed(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Run status should be 'failed' after retry exhaustion with on_exhaustion=fail."""
        process = make_retry_process(max_attempts=2)
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.FAILED

    @pytest.mark.asyncio
    async def test_node_failed_and_run_failed_events_emitted(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        """Both roots.node.failed and roots.run.failed events should be emitted."""
        process = make_retry_process(max_attempts=2)
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()
        await emitter.close()

        event_types = [e.event for e in sink.events]
        assert "roots.node.failed" in event_types
        assert "roots.run.failed" in event_types

    @pytest.mark.asyncio
    async def test_last_error_in_failure_metadata(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        """Failure events should include the last error in metadata."""
        process = make_retry_process(max_attempts=2)
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()
        await emitter.close()

        node_failed = [
            e for e in sink.events if e.event == "roots.node.failed"
        ]
        assert len(node_failed) == 1
        assert "error" in node_failed[0].metadata
        assert "agent crashed" in node_failed[0].metadata["error"]

        run_failed = [
            e for e in sink.events if e.event == "roots.run.failed"
        ]
        assert len(run_failed) == 1
        assert "error" in run_failed[0].metadata
        assert "agent crashed" in run_failed[0].metadata["error"]

    @pytest.mark.asyncio
    async def test_run_history_records_attempts_and_failure(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Run history should contain the entered event and the failed event."""
        process = make_retry_process(max_attempts=3)
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()

        history = await sqlite_storage.list_history_events(run_id)
        event_types = [h.event_type for h in history]
        # Should have entered + failed for the retry-agent node
        assert "entered" in event_types
        assert "failed" in event_types

        # The failed event should contain error and attempts
        failed_events = [h for h in history if h.event_type == "failed"]
        assert len(failed_events) == 1
        assert "agent crashed" in failed_events[0].data["error"]
        assert failed_events[0].data["attempts"] == 3

    @pytest.mark.asyncio
    async def test_tick_returns_false_on_exhaustion(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """tick() should return False when retry exhaustion fails the run."""
        process = make_retry_process(max_attempts=2)
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        # First tick: pending -> running, then exhaustion -> failed
        result = await runner.tick()
        # Should still be running after pending->running transition
        # Second tick processes the node
        if result:
            result = await runner.tick()
        assert result is False

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.FAILED
