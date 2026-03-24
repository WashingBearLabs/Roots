"""Tests for ProcessRunner tick-based execution loop (US-002)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from roots.agents.registry import AgentRegistry
from roots.agents.invoker import AgentInvoker
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
from roots.storage.base import StorageBackend


# --- Helpers ---


class CollectorSink(EventSink):
    """Collects emitted events for assertion."""

    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def emit(self, event: EventEnvelope) -> None:
        self.events.append(event)


async def echo_agent(input: dict[str, Any]) -> dict[str, Any]:
    return {"output": {"echo": input["work_item_state"]}, "escalate": False}


def make_two_node_process() -> ProcessDefinition:
    """Agent → End linear process."""
    return ProcessDefinition(
        id="two-node",
        name="Two Node Process",
        version="1.0.0",
        nodes=[
            NodeDefinition(
                id="step1",
                type=NodeType.AGENT,
                label="Step 1",
                config=AgentNodeConfig(agent="echo", output_key="step1_out"),
            ),
            NodeDefinition(
                id="done",
                type=NodeType.END,
                label="Done",
                config=EndNodeConfig(status=EndStatus.COMPLETED),
            ),
        ],
        edges=[EdgeDefinition(from_node="step1", to_node="done")],
        entry_point="step1",
    )


def make_three_node_process() -> ProcessDefinition:
    """Agent → Agent → End for state accumulation tests."""
    return ProcessDefinition(
        id="three-node",
        name="Three Node Process",
        version="1.0.0",
        nodes=[
            NodeDefinition(
                id="step1",
                type=NodeType.AGENT,
                label="Step 1",
                config=AgentNodeConfig(agent="echo", output_key="step1_out"),
            ),
            NodeDefinition(
                id="step2",
                type=NodeType.AGENT,
                label="Step 2",
                config=AgentNodeConfig(agent="echo", output_key="step2_out"),
            ),
            NodeDefinition(
                id="done",
                type=NodeType.END,
                label="Done",
                config=EndNodeConfig(status=EndStatus.COMPLETED),
            ),
        ],
        edges=[
            EdgeDefinition(from_node="step1", to_node="step2"),
            EdgeDefinition(from_node="step2", to_node="done"),
        ],
        entry_point="step1",
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
    process: ProcessDefinition | None = None,
) -> tuple[ProcessDefinition, str]:
    """Save process and create a pending run, return (process, run_id)."""
    proc = process or make_two_node_process()
    await storage.save_process(proc)
    run = await storage.create_run(proc.id, {"input": "hello"})
    return proc, run.id


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


class TestTickLockBehavior:
    async def test_tick_acquires_and_releases_lock(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        _, run_id = await _setup_run(sqlite_storage)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.tick()

        # Lock should be released after tick
        locked_by, _ = await sqlite_storage.check_run_lock(run_id)
        assert locked_by is None

    async def test_lock_released_on_exception(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        proc = make_two_node_process()
        await sqlite_storage.save_process(proc)
        run = await sqlite_storage.create_run(proc.id, {"input": "hello"})
        # Set run to running with a bad node ID to trigger error
        await sqlite_storage.update_run_status(run.id, "running", "nonexistent")

        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)

        with pytest.raises(OrchestrationError):
            await runner.tick()

        # Lock must still be released
        locked_by, _ = await sqlite_storage.check_run_lock(run.id)
        assert locked_by is None

    async def test_tick_returns_false_when_lock_unavailable(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        _, run_id = await _setup_run(sqlite_storage)

        # Pre-acquire lock with different owner
        await sqlite_storage.acquire_run_lock(run_id, "other-owner")

        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)
        result = await runner.tick()
        assert result is False


class TestPendingToRunningTransition:
    async def test_first_tick_transitions_pending_to_running(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        _, run_id = await _setup_run(sqlite_storage)

        # Verify initial state is pending
        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == "pending"

        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)
        result = await runner.tick()
        assert result is True

        # After tick, run should be running (moved to step2)
        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == "running"

    async def test_first_tick_sets_current_node_to_entry_point(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        proc, run_id = await _setup_run(sqlite_storage)

        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)
        await runner.tick()

        # After first tick (agent node), current_node should advance to 'done'
        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.current_node_id == "done"


class TestFreshStateLoading:
    async def test_state_loaded_fresh_each_tick(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        proc = make_three_node_process()
        _, run_id = await _setup_run(sqlite_storage, proc)

        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        # First tick: processes step1
        result1 = await runner.tick()
        assert result1 is True

        # Verify state was persisted with step1_out
        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert "step1_out" in run.work_item_state

        # Second tick: processes step2 — loads state fresh including step1_out
        result2 = await runner.tick()
        assert result2 is True

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert "step1_out" in run.work_item_state
        assert "step2_out" in run.work_item_state


class TestEventEmission:
    async def test_events_emitted_with_correct_fields(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        _, run_id = await _setup_run(sqlite_storage)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.tick()
        # Allow event tasks to complete
        await emitter.close()

        entered = [e for e in sink.events if e.event == "roots.node.entered"]
        completed = [e for e in sink.events if e.event == "roots.node.completed"]

        assert len(entered) == 1
        assert len(completed) == 1

        # Verify entered event fields
        assert entered[0].run_id == run_id
        assert entered[0].process_id == "two-node"
        assert entered[0].node_id == "step1"
        assert entered[0].node_type == "agent"

        # Verify completed event fields
        assert completed[0].run_id == run_id
        assert completed[0].process_id == "two-node"
        assert completed[0].node_id == "step1"
        assert completed[0].node_type == "agent"
        assert completed[0].duration_ms is not None
        assert completed[0].duration_ms >= 0


class TestHistoryEvents:
    async def test_history_uses_lifecycle_strings(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        _, run_id = await _setup_run(sqlite_storage)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.tick()

        history = await sqlite_storage.list_history_events(run_id)
        types = [h.event_type for h in history]
        # Should use "entered" and "completed", NOT node type like "agent"
        assert "entered" in types
        assert "completed" in types
        assert "agent" not in types

    async def test_history_events_reference_correct_node(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        _, run_id = await _setup_run(sqlite_storage)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.tick()

        history = await sqlite_storage.list_history_events(run_id)
        for h in history:
            assert h.node_id == "step1"


class TestRunToCompletion:
    async def test_run_to_completion_loops(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        _, run_id = await _setup_run(sqlite_storage)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == "completed"

    async def test_run_to_completion_three_nodes(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        proc = make_three_node_process()
        _, run_id = await _setup_run(sqlite_storage, proc)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()
        await emitter.close()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == "completed"

        # Verify both agent outputs in state
        assert "step1_out" in run.work_item_state
        assert "step2_out" in run.work_item_state

        # Verify history has entered+completed for each node executed
        history = await sqlite_storage.list_history_events(run_id)
        entered_nodes = [h.node_id for h in history if h.event_type == "entered"]
        completed_nodes = [h.node_id for h in history if h.event_type == "completed"]
        assert "step1" in entered_nodes
        assert "step2" in entered_nodes
        assert "done" in entered_nodes
        assert "step1" in completed_nodes
        assert "step2" in completed_nodes
        assert "done" in completed_nodes


class TestTickReturnsCorrectly:
    async def test_tick_returns_false_for_completed_run(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        proc, run_id = await _setup_run(sqlite_storage)
        await sqlite_storage.update_run_status(run_id, "running")
        await sqlite_storage.update_run_status(run_id, "completed")

        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)
        result = await runner.tick()
        assert result is False

    async def test_end_node_tick_returns_false(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """When tick processes the end node, it should return False."""
        _, run_id = await _setup_run(sqlite_storage)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        # First tick: agent node → returns True (still running)
        assert await runner.tick() is True
        # Second tick: end node → returns False (completed)
        assert await runner.tick() is False
