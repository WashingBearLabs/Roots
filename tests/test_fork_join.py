"""Tests for fork/join (US-001 branch creation, US-002 parallel execution)."""

from __future__ import annotations

import asyncio
import copy
import time
from typing import Any

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
    ForkNodeConfig,
    JoinNodeConfig,
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


async def slow_agent(input: dict[str, Any]) -> dict[str, Any]:
    """Agent that sleeps 0.1s to verify concurrent execution."""
    await asyncio.sleep(0.1)
    return {"output": {"slow": True}, "escalate": False}


async def failing_agent(input: dict[str, Any]) -> dict[str, Any]:
    """Agent that always raises an exception."""
    raise RuntimeError("branch failure")


def make_fork_2_branch_process() -> ProcessDefinition:
    """Fork → 2 agents → Join → End process."""
    proc = ProcessDefinition(
        id="fork-2",
        name="Fork 2 Branch",
        version="1.0.0",
        nodes=[
            NodeDefinition(
                id="fork1",
                type=NodeType.FORK,
                label="Fork",
                config=ForkNodeConfig(),
            ),
            NodeDefinition(
                id="branch_a",
                type=NodeType.AGENT,
                label="Branch A",
                config=AgentNodeConfig(agent="echo", output_key="a_out"),
            ),
            NodeDefinition(
                id="branch_b",
                type=NodeType.AGENT,
                label="Branch B",
                config=AgentNodeConfig(agent="echo", output_key="b_out"),
            ),
            NodeDefinition(
                id="join1",
                type=NodeType.JOIN,
                label="Join",
                config=JoinNodeConfig(),
            ),
            NodeDefinition(
                id="done",
                type=NodeType.END,
                label="Done",
                config=EndNodeConfig(status=EndStatus.COMPLETED),
            ),
        ],
        edges=[
            EdgeDefinition(from_node="fork1", to_node="branch_a"),
            EdgeDefinition(from_node="fork1", to_node="branch_b"),
            EdgeDefinition(from_node="branch_a", to_node="join1"),
            EdgeDefinition(from_node="branch_b", to_node="join1"),
            EdgeDefinition(from_node="join1", to_node="done"),
        ],
        entry_point="fork1",
        fork_join_map={"fork1": "join1"},
    )
    return proc


def make_fork_3_branch_process() -> ProcessDefinition:
    """Fork → 3 agents → Join → End process."""
    proc = ProcessDefinition(
        id="fork-3",
        name="Fork 3 Branch",
        version="1.0.0",
        nodes=[
            NodeDefinition(
                id="fork1",
                type=NodeType.FORK,
                label="Fork",
                config=ForkNodeConfig(),
            ),
            NodeDefinition(
                id="branch_a",
                type=NodeType.AGENT,
                label="Branch A",
                config=AgentNodeConfig(agent="echo", output_key="a_out"),
            ),
            NodeDefinition(
                id="branch_b",
                type=NodeType.AGENT,
                label="Branch B",
                config=AgentNodeConfig(agent="echo", output_key="b_out"),
            ),
            NodeDefinition(
                id="branch_c",
                type=NodeType.AGENT,
                label="Branch C",
                config=AgentNodeConfig(agent="echo", output_key="c_out"),
            ),
            NodeDefinition(
                id="join1",
                type=NodeType.JOIN,
                label="Join",
                config=JoinNodeConfig(),
            ),
            NodeDefinition(
                id="done",
                type=NodeType.END,
                label="Done",
                config=EndNodeConfig(status=EndStatus.COMPLETED),
            ),
        ],
        edges=[
            EdgeDefinition(from_node="fork1", to_node="branch_a"),
            EdgeDefinition(from_node="fork1", to_node="branch_b"),
            EdgeDefinition(from_node="fork1", to_node="branch_c"),
            EdgeDefinition(from_node="branch_a", to_node="join1"),
            EdgeDefinition(from_node="branch_b", to_node="join1"),
            EdgeDefinition(from_node="branch_c", to_node="join1"),
            EdgeDefinition(from_node="join1", to_node="done"),
        ],
        entry_point="fork1",
        fork_join_map={"fork1": "join1"},
    )
    return proc


def make_fork_no_edges_process() -> ProcessDefinition:
    """Fork node with no outbound edges (invalid, for error testing)."""
    proc = ProcessDefinition(
        id="fork-no-edges",
        name="Fork No Edges",
        version="1.0.0",
        nodes=[
            NodeDefinition(
                id="fork1",
                type=NodeType.FORK,
                label="Fork",
                config=ForkNodeConfig(),
            ),
            NodeDefinition(
                id="done",
                type=NodeType.END,
                label="Done",
                config=EndNodeConfig(status=EndStatus.COMPLETED),
            ),
        ],
        edges=[],
        entry_point="fork1",
    )
    return proc


# --- Fixtures ---


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
    reg.register_local("slow", slow_agent)
    reg.register_local("failing", failing_agent)
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
    work_item: dict[str, Any] | None = None,
) -> tuple[ProcessDefinition, str]:
    """Save process and create a pending run, return (process, run_id)."""
    await storage.save_process(process)
    run = await storage.create_run(process.id, work_item or {"input": "hello"})
    return process, run.id


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


class TestForkBranchCreation:
    """US-001: Fork Node — Branch Creation."""

    async def test_fork_identifies_outbound_edges_as_branches(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Fork node identifies all outbound edges as branches."""
        proc, run_id = await _setup_run(
            sqlite_storage, make_fork_2_branch_process()
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()

        # Runner should have created 2 branches
        assert hasattr(runner, "_fork_branches")
        branches = runner._fork_branches
        assert len(branches) == 2
        entry_nodes = {b["entry_node_id"] for b in branches}
        assert entry_nodes == {"branch_a", "branch_b"}

    async def test_each_branch_gets_deep_copy_of_state(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Each branch gets a deep copy of work item state."""
        work_item = {"input": "hello", "nested": {"key": "value"}}
        proc, run_id = await _setup_run(
            sqlite_storage, make_fork_2_branch_process(), work_item
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()

        branches = runner._fork_branches
        # Both branches should have the same state content
        for branch in branches:
            assert branch["state"]["input"] == "hello"
            assert branch["state"]["nested"]["key"] == "value"

        # But they should be independent copies (not same object)
        assert branches[0]["state"] is not branches[1]["state"]
        assert branches[0]["state"]["nested"] is not branches[1]["state"]["nested"]

        # Mutating one branch state should not affect the other
        branches[0]["state"]["nested"]["key"] = "modified"
        assert branches[1]["state"]["nested"]["key"] == "value"

    async def test_branch_metadata_tracked(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Branch metadata is tracked (branch_id, entry node)."""
        proc, run_id = await _setup_run(
            sqlite_storage, make_fork_2_branch_process()
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()

        branches = runner._fork_branches
        # Check branch_id format
        assert branches[0]["branch_id"] == "branch-0"
        assert branches[1]["branch_id"] == "branch-1"
        # Check entry_node_id
        assert branches[0]["entry_node_id"] == "branch_a"
        assert branches[1]["entry_node_id"] == "branch_b"
        # Check join node was resolved
        assert runner._fork_join_node_id == "join1"

    async def test_fork_zero_outbound_edges_raises_error(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Fork node with 0 outbound edges raises OrchestrationError."""
        proc, run_id = await _setup_run(
            sqlite_storage, make_fork_no_edges_process()
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        with pytest.raises(OrchestrationError, match="no outbound edges"):
            await runner.tick()

    async def test_fork_3_branches(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Fork creates correct branches for 3+ branch case."""
        proc, run_id = await _setup_run(
            sqlite_storage, make_fork_3_branch_process()
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()

        branches = runner._fork_branches
        assert len(branches) == 3
        assert branches[0]["branch_id"] == "branch-0"
        assert branches[1]["branch_id"] == "branch-1"
        assert branches[2]["branch_id"] == "branch-2"
        entry_nodes = {b["entry_node_id"] for b in branches}
        assert entry_nodes == {"branch_a", "branch_b", "branch_c"}
        # All states are independent deep copies
        states = [b["state"] for b in branches]
        for i in range(len(states)):
            for j in range(i + 1, len(states)):
                assert states[i] is not states[j]

    async def test_fork_node_completed_event_emitted(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
        sink: CollectorSink,
    ) -> None:
        """Fork node emits roots.node.completed event after branches set up."""
        proc, run_id = await _setup_run(
            sqlite_storage, make_fork_2_branch_process()
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()

        # Find node.completed event for fork1
        completed_events = [
            e for e in sink.events
            if e.event == "roots.node.completed" and e.node_id == "fork1"
        ]
        assert len(completed_events) == 1


# --- US-002 Helpers ---


def make_fork_slow_branches_process() -> ProcessDefinition:
    """Fork → 2 slow agents → Join → End (for timing test)."""
    return ProcessDefinition(
        id="fork-slow",
        name="Fork Slow Branches",
        version="1.0.0",
        nodes=[
            NodeDefinition(
                id="fork1",
                type=NodeType.FORK,
                label="Fork",
                config=ForkNodeConfig(),
            ),
            NodeDefinition(
                id="branch_a",
                type=NodeType.AGENT,
                label="Slow A",
                config=AgentNodeConfig(agent="slow", output_key="a_out"),
            ),
            NodeDefinition(
                id="branch_b",
                type=NodeType.AGENT,
                label="Slow B",
                config=AgentNodeConfig(agent="slow", output_key="b_out"),
            ),
            NodeDefinition(
                id="join1",
                type=NodeType.JOIN,
                label="Join",
                config=JoinNodeConfig(),
            ),
            NodeDefinition(
                id="done",
                type=NodeType.END,
                label="Done",
                config=EndNodeConfig(status=EndStatus.COMPLETED),
            ),
        ],
        edges=[
            EdgeDefinition(from_node="fork1", to_node="branch_a"),
            EdgeDefinition(from_node="fork1", to_node="branch_b"),
            EdgeDefinition(from_node="branch_a", to_node="join1"),
            EdgeDefinition(from_node="branch_b", to_node="join1"),
            EdgeDefinition(from_node="join1", to_node="done"),
        ],
        entry_point="fork1",
        fork_join_map={"fork1": "join1"},
    )


def make_fork_one_failing_process() -> ProcessDefinition:
    """Fork → 1 echo + 1 failing agent → Join → End."""
    return ProcessDefinition(
        id="fork-fail",
        name="Fork One Failing",
        version="1.0.0",
        nodes=[
            NodeDefinition(
                id="fork1",
                type=NodeType.FORK,
                label="Fork",
                config=ForkNodeConfig(),
            ),
            NodeDefinition(
                id="branch_a",
                type=NodeType.AGENT,
                label="Good Branch",
                config=AgentNodeConfig(agent="echo", output_key="a_out"),
            ),
            NodeDefinition(
                id="branch_b",
                type=NodeType.AGENT,
                label="Failing Branch",
                config=AgentNodeConfig(agent="failing", output_key="b_out"),
            ),
            NodeDefinition(
                id="join1",
                type=NodeType.JOIN,
                label="Join",
                config=JoinNodeConfig(),
            ),
            NodeDefinition(
                id="done",
                type=NodeType.END,
                label="Done",
                config=EndNodeConfig(status=EndStatus.COMPLETED),
            ),
        ],
        edges=[
            EdgeDefinition(from_node="fork1", to_node="branch_a"),
            EdgeDefinition(from_node="fork1", to_node="branch_b"),
            EdgeDefinition(from_node="branch_a", to_node="join1"),
            EdgeDefinition(from_node="branch_b", to_node="join1"),
            EdgeDefinition(from_node="join1", to_node="done"),
        ],
        entry_point="fork1",
        fork_join_map={"fork1": "join1"},
    )


# --- US-002 Tests ---


class TestParallelBranchExecution:
    """US-002: Parallel Branch Execution."""

    async def test_branches_execute_concurrently_via_gather(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """All branches execute concurrently — total time < 2x single branch."""
        proc, run_id = await _setup_run(
            sqlite_storage, make_fork_slow_branches_process()
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        start = time.monotonic()
        await runner.tick()  # fork tick executes branches
        elapsed = time.monotonic() - start

        # Both branches sleep 0.1s. If sequential, total >= 0.2s.
        # If concurrent, total should be ~0.1s (plus overhead).
        # Use 0.19s as threshold to confirm concurrency.
        assert elapsed < 0.19, (
            f"Branches took {elapsed:.3f}s — expected concurrent (<0.19s)"
        )

    async def test_each_branch_maintains_independent_state(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Each branch operates on its own state copy."""
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_fork_2_branch_process(),
            {"input": "hello", "shared": {"counter": 0}},
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()

        results = runner._fork_branch_results
        assert len(results) == 2
        # Both branches should return dicts (not exceptions)
        for r in results:
            assert isinstance(r, dict)
        # States are independent — not the same object
        assert results[0] is not results[1]

    async def test_branch_execution_stops_at_join_node(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
        sink: CollectorSink,
    ) -> None:
        """Branch execution stops at the join node — join is NOT executed."""
        proc, run_id = await _setup_run(
            sqlite_storage, make_fork_2_branch_process()
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()

        # Join node should NOT have been entered during branch execution
        join_entered = [
            e for e in sink.events
            if e.event == "roots.node.entered" and e.node_id == "join1"
        ]
        assert len(join_entered) == 0

        # Branch agent nodes SHOULD have been entered
        branch_entered = [
            e for e in sink.events
            if e.event == "roots.node.entered"
            and e.node_id in ("branch_a", "branch_b")
        ]
        assert len(branch_entered) == 2

    async def test_branch_results_collected(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Branch results (state dicts) are collected after execution."""
        proc, run_id = await _setup_run(
            sqlite_storage, make_fork_2_branch_process()
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()

        results = runner._fork_branch_results
        assert len(results) == 2
        # Echo agent writes output under the output_key
        # branch_a uses output_key="a_out", branch_b uses "b_out"
        assert isinstance(results[0], dict)
        assert "a_out" in results[0]
        assert isinstance(results[1], dict)
        assert "b_out" in results[1]

    async def test_branch_exceptions_captured_not_raised(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Branch exceptions are captured by return_exceptions=True."""
        proc, run_id = await _setup_run(
            sqlite_storage, make_fork_one_failing_process()
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        # Should NOT raise — exceptions captured
        await runner.tick()

        results = runner._fork_branch_results
        assert len(results) == 2
        # First branch (echo) succeeds
        assert isinstance(results[0], dict)
        # Second branch (failing) is an exception
        assert isinstance(results[1], BaseException)

    async def test_events_include_branch_id_in_metadata(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
        sink: CollectorSink,
    ) -> None:
        """Events emitted during branch execution include branch_id."""
        proc, run_id = await _setup_run(
            sqlite_storage, make_fork_2_branch_process()
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()

        # Get all branch node events (entered + completed for each branch agent)
        branch_events = [
            e for e in sink.events
            if e.node_id in ("branch_a", "branch_b")
            and e.event in (
                "roots.node.entered",
                "roots.node.completed",
            )
        ]
        # 2 branches × 2 events (entered + completed) = 4
        assert len(branch_events) == 4

        # Every branch event must have branch_id in metadata
        for event in branch_events:
            assert "branch_id" in event.metadata, (
                f"Event {event.event} for {event.node_id} "
                f"missing branch_id in metadata"
            )

        # Verify correct branch_id mapping
        branch_a_events = [
            e for e in branch_events if e.node_id == "branch_a"
        ]
        branch_b_events = [
            e for e in branch_events if e.node_id == "branch_b"
        ]
        for e in branch_a_events:
            assert e.metadata["branch_id"] == "branch-0"
        for e in branch_b_events:
            assert e.metadata["branch_id"] == "branch-1"

    async def test_concurrent_execution_with_timing(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Timing data is recorded per branch."""
        proc, run_id = await _setup_run(
            sqlite_storage, make_fork_slow_branches_process()
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()

        # Branch contexts should have duration_ms populated
        for branch in runner._fork_branches:
            assert "duration_ms" in branch
            assert isinstance(branch["duration_ms"], int)
            assert branch["duration_ms"] >= 0
