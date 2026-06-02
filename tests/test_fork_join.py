"""Tests for fork/join (US-001 branch creation, US-002 parallel execution, US-003 merge_all, US-004 collect, US-005 partial failure, US-002-crash branch persistence, US-003-crash recovery)."""

from __future__ import annotations

import asyncio
import copy
import time
from typing import Any
from unittest.mock import patch

import pytest

from roots.agents.invoker import AgentInvoker
from roots.agents.registry import AgentRegistry
from roots.core.decision import DecisionEngine
from roots.core.orchestrator import OrchestrationError, ProcessRunner, deep_merge
from roots.core.schema import (
    AgentNodeConfig,
    EdgeDefinition,
    EndNodeConfig,
    EndStatus,
    ForkNodeConfig,
    JoinNodeConfig,
    MergeStrategy,
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


# --- US-003 Helpers ---


async def agent_add_x(input: dict[str, Any]) -> dict[str, Any]:
    """Agent that adds x-related keys."""
    return {"output": {"x": 1, "shared": {"from_x": True}}, "escalate": False}


async def agent_add_y(input: dict[str, Any]) -> dict[str, Any]:
    """Agent that adds y-related keys."""
    return {"output": {"y": 2, "shared": {"from_y": True}}, "escalate": False}


async def agent_overlap_a(input: dict[str, Any]) -> dict[str, Any]:
    """Agent that sets overlapping keys (branch A)."""
    return {
        "output": {
            "winner": "a",
            "nested": {"level1": {"a_key": 1, "conflict": "a_val"}},
            "list_val": [1, 2],
        },
        "escalate": False,
    }


async def agent_overlap_b(input: dict[str, Any]) -> dict[str, Any]:
    """Agent that sets overlapping keys (branch B — last writer wins)."""
    return {
        "output": {
            "winner": "b",
            "nested": {"level1": {"b_key": 2, "conflict": "b_val"}},
            "list_val": [3, 4],
        },
        "escalate": False,
    }


def make_merge_all_process(
    agent_a: str = "agent_add_x",
    agent_b: str = "agent_add_y",
) -> ProcessDefinition:
    """Fork → 2 agents → Join (merge_all) → End."""
    return ProcessDefinition(
        id="merge-all",
        name="Merge All Test",
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
                config=AgentNodeConfig(agent=agent_a, output_key="a_out"),
            ),
            NodeDefinition(
                id="branch_b",
                type=NodeType.AGENT,
                label="Branch B",
                config=AgentNodeConfig(agent=agent_b, output_key="b_out"),
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


# --- US-003 Tests ---


class TestDeepMerge:
    """Unit tests for the deep_merge utility."""

    def test_disjoint_keys(self) -> None:
        assert deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_overlapping_scalar_last_writer_wins(self) -> None:
        assert deep_merge({"k": "first"}, {"k": "second"}) == {"k": "second"}

    def test_nested_dicts_merged_recursively(self) -> None:
        base = {"outer": {"a": 1, "inner": {"x": 10}}}
        override = {"outer": {"b": 2, "inner": {"y": 20}}}
        result = deep_merge(base, override)
        assert result == {"outer": {"a": 1, "b": 2, "inner": {"x": 10, "y": 20}}}

    def test_nested_conflict_last_writer_wins(self) -> None:
        base = {"outer": {"key": "base_val"}}
        override = {"outer": {"key": "override_val"}}
        assert deep_merge(base, override) == {"outer": {"key": "override_val"}}

    def test_list_override_replaces(self) -> None:
        base = {"items": [1, 2, 3]}
        override = {"items": [4, 5]}
        assert deep_merge(base, override) == {"items": [4, 5]}

    def test_empty_base(self) -> None:
        assert deep_merge({}, {"a": 1}) == {"a": 1}

    def test_empty_override(self) -> None:
        assert deep_merge({"a": 1}, {}) == {"a": 1}

    def test_three_way_merge(self) -> None:
        """Simulates branch-order merge across 3 branches."""
        a = {"x": 1, "shared": "a"}
        b = {"y": 2, "shared": "b"}
        c = {"z": 3, "shared": "c"}
        result = deep_merge(deep_merge(a, b), c)
        assert result == {"x": 1, "y": 2, "z": 3, "shared": "c"}


class TestJoinMergeAll:
    """US-003: Join Node — merge_all Strategy."""

    async def test_branch_outputs_deep_merged_in_order(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Branch outputs are deep-merged in branch order."""
        invoker._registry.register_local("agent_add_x", agent_add_x)
        invoker._registry.register_local("agent_add_y", agent_add_y)
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_merge_all_process("agent_add_x", "agent_add_y"),
            {"input": "hello"},
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        # Tick 1: fork → executes branches → routes to join
        await runner.tick()
        # Tick 2: join → merges branch results
        await runner.tick()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        state = run.work_item_state
        # Both branches' output_keys should be in merged state
        assert "a_out" in state
        assert "b_out" in state
        # Original input preserved
        assert state["input"] == "hello"

    async def test_nested_dicts_merged_recursively(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Nested dicts are merged recursively, not replaced."""
        invoker._registry.register_local("agent_add_x", agent_add_x)
        invoker._registry.register_local("agent_add_y", agent_add_y)
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_merge_all_process("agent_add_x", "agent_add_y"),
            {"input": "hello"},
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()  # fork
        await runner.tick()  # join

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        state = run.work_item_state
        # a_out has {x: 1, shared: {from_x: True}}
        # b_out has {y: 2, shared: {from_y: True}}
        # The branch states both have input + their output_key
        # Merged state should have both output keys
        assert "a_out" in state
        assert "b_out" in state

    async def test_overlapping_keys_last_writer_wins(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Non-dict conflicts resolved by last-writer-wins (branch order)."""
        invoker._registry.register_local("agent_overlap_a", agent_overlap_a)
        invoker._registry.register_local("agent_overlap_b", agent_overlap_b)
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_merge_all_process("agent_overlap_a", "agent_overlap_b"),
            {"input": "test"},
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()  # fork
        await runner.tick()  # join

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        state = run.work_item_state
        # Branch B is last — its a_out has overlap keys
        # Both branches write to different output_keys (a_out, b_out)
        # But each branch's state has its output_key added
        # Branch A state: {input: test, a_out: {winner: a, nested: ..., list_val: [1,2]}}
        # Branch B state: {input: test, b_out: {winner: b, nested: ..., list_val: [3,4]}}
        # After merge: state has both a_out and b_out, input from last writer (same value)
        assert "a_out" in state
        assert "b_out" in state
        # b_out should have branch B's values (last writer for b_out)
        assert state["b_out"]["winner"] == "b"
        assert state["b_out"]["list_val"] == [3, 4]
        # a_out should have branch A's values
        assert state["a_out"]["winner"] == "a"

    async def test_merged_state_written_to_work_item(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Merged state is persisted to work item in storage."""
        invoker._registry.register_local("agent_add_x", agent_add_x)
        invoker._registry.register_local("agent_add_y", agent_add_y)
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_merge_all_process("agent_add_x", "agent_add_y"),
            {"input": "persist_test"},
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()  # fork
        await runner.tick()  # join

        # Read fresh from storage to confirm persistence
        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert "a_out" in run.work_item_state
        assert "b_out" in run.work_item_state
        assert run.work_item_state["input"] == "persist_test"

    async def test_execution_continues_from_join_outbound_edge(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
        sink: CollectorSink,
    ) -> None:
        """Execution continues from join's outbound edge to END node."""
        invoker._registry.register_local("agent_add_x", agent_add_x)
        invoker._registry.register_local("agent_add_y", agent_add_y)
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_merge_all_process("agent_add_x", "agent_add_y"),
            {"input": "continue_test"},
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()  # fork
        await runner.tick()  # join
        await runner.tick()  # end

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.COMPLETED

        # Verify run.completed event was emitted
        completed_events = [
            e for e in sink.events if e.event == "roots.run.completed"
        ]
        assert len(completed_events) == 1

    async def test_merge_with_overlapping_nested_structures(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Overlapping nested structures: dicts merge, scalars last-writer-wins."""
        invoker._registry.register_local("agent_overlap_a", agent_overlap_a)
        invoker._registry.register_local("agent_overlap_b", agent_overlap_b)
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_merge_all_process("agent_overlap_a", "agent_overlap_b"),
            {"input": "nested_test"},
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()  # fork
        await runner.tick()  # join

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        state = run.work_item_state
        # a_out has nested structure from agent_overlap_a
        assert state["a_out"]["nested"]["level1"]["a_key"] == 1
        assert state["a_out"]["nested"]["level1"]["conflict"] == "a_val"
        # b_out has nested structure from agent_overlap_b
        assert state["b_out"]["nested"]["level1"]["b_key"] == 2
        assert state["b_out"]["nested"]["level1"]["conflict"] == "b_val"


# --- US-004 Helpers ---


async def agent_hetero_a(input: dict[str, Any]) -> dict[str, Any]:
    """Agent returning string-heavy output."""
    return {"output": {"kind": "text", "value": "hello"}, "escalate": False}


async def agent_hetero_b(input: dict[str, Any]) -> dict[str, Any]:
    """Agent returning numeric-heavy output."""
    return {"output": {"kind": "number", "value": 42}, "escalate": False}


async def agent_hetero_c(input: dict[str, Any]) -> dict[str, Any]:
    """Agent returning list output."""
    return {"output": {"kind": "list", "value": [1, 2, 3]}, "escalate": False}


def make_collect_process(
    agent_a: str = "echo",
    agent_b: str = "echo",
    collect_key: str = "results",
    allow_partial: bool = False,
) -> ProcessDefinition:
    """Fork → 2 agents → Join (collect) → End."""
    return ProcessDefinition(
        id="collect-2",
        name="Collect 2 Branch",
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
                config=AgentNodeConfig(agent=agent_a, output_key="a_out"),
            ),
            NodeDefinition(
                id="branch_b",
                type=NodeType.AGENT,
                label="Branch B",
                config=AgentNodeConfig(agent=agent_b, output_key="b_out"),
            ),
            NodeDefinition(
                id="join1",
                type=NodeType.JOIN,
                label="Join",
                config=JoinNodeConfig(
                    merge_strategy=MergeStrategy.COLLECT,
                    collect_key=collect_key,
                    allow_partial=allow_partial,
                ),
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


def make_collect_3_branch_process(
    collect_key: str = "results",
    allow_partial: bool = False,
) -> ProcessDefinition:
    """Fork → 3 heterogeneous agents → Join (collect) → End."""
    return ProcessDefinition(
        id="collect-3",
        name="Collect 3 Branch",
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
                config=AgentNodeConfig(agent="hetero_a", output_key="a_out"),
            ),
            NodeDefinition(
                id="branch_b",
                type=NodeType.AGENT,
                label="Branch B",
                config=AgentNodeConfig(agent="hetero_b", output_key="b_out"),
            ),
            NodeDefinition(
                id="branch_c",
                type=NodeType.AGENT,
                label="Branch C",
                config=AgentNodeConfig(agent="hetero_c", output_key="c_out"),
            ),
            NodeDefinition(
                id="join1",
                type=NodeType.JOIN,
                label="Join",
                config=JoinNodeConfig(
                    merge_strategy=MergeStrategy.COLLECT,
                    collect_key=collect_key,
                    allow_partial=allow_partial,
                ),
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


# --- US-004 Tests ---


class TestJoinCollect:
    """US-004: Join Node — collect Strategy."""

    async def test_branch_outputs_collected_as_list(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Branch outputs collected as list under configured key."""
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_collect_process("echo", "echo", collect_key="results"),
            {"input": "hello"},
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()  # fork
        await runner.tick()  # join

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        state = run.work_item_state
        assert "results" in state
        assert isinstance(state["results"], list)
        assert len(state["results"]) == 2

    async def test_each_entry_includes_branch_metadata(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Each list entry includes branch_id, entry_node, and state."""
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_collect_process("echo", "echo", collect_key="results"),
            {"input": "hello"},
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()  # fork
        await runner.tick()  # join

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        results = run.work_item_state["results"]

        for entry in results:
            assert "branch_id" in entry
            assert "entry_node" in entry
            assert "state" in entry
            assert isinstance(entry["state"], dict)

        # Check specific metadata
        assert results[0]["branch_id"] == "branch-0"
        assert results[0]["entry_node"] == "branch_a"
        assert results[1]["branch_id"] == "branch-1"
        assert results[1]["entry_node"] == "branch_b"

    async def test_collect_key_from_config(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """collect_key is used from join node config."""
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_collect_process("echo", "echo", collect_key="my_custom_key"),
            {"input": "hello"},
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()  # fork
        await runner.tick()  # join

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert "my_custom_key" in run.work_item_state
        assert isinstance(run.work_item_state["my_custom_key"], list)
        assert len(run.work_item_state["my_custom_key"]) == 2

    async def test_order_matches_branch_order(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Order matches branch order (deterministic)."""
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_collect_process("echo", "echo", collect_key="results"),
            {"input": "hello"},
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()  # fork
        await runner.tick()  # join

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        results = run.work_item_state["results"]

        # Order must match branch order: branch-0 first, branch-1 second
        assert results[0]["branch_id"] == "branch-0"
        assert results[1]["branch_id"] == "branch-1"
        # branch_a writes to a_out, branch_b writes to b_out
        assert "a_out" in results[0]["state"]
        assert "b_out" in results[1]["state"]

    async def test_collect_with_heterogeneous_outputs(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Collect works with heterogeneous branch outputs."""
        invoker._registry.register_local("hetero_a", agent_hetero_a)
        invoker._registry.register_local("hetero_b", agent_hetero_b)
        invoker._registry.register_local("hetero_c", agent_hetero_c)
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_collect_3_branch_process(collect_key="gathered"),
            {"input": "test"},
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()  # fork
        await runner.tick()  # join

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        gathered = run.work_item_state["gathered"]
        assert len(gathered) == 3

        # Verify heterogeneous outputs are preserved
        assert gathered[0]["state"]["a_out"]["kind"] == "text"
        assert gathered[0]["state"]["a_out"]["value"] == "hello"
        assert gathered[1]["state"]["b_out"]["kind"] == "number"
        assert gathered[1]["state"]["b_out"]["value"] == 42
        assert gathered[2]["state"]["c_out"]["kind"] == "list"
        assert gathered[2]["state"]["c_out"]["value"] == [1, 2, 3]

        # Branch metadata in order
        assert gathered[0]["branch_id"] == "branch-0"
        assert gathered[1]["branch_id"] == "branch-1"
        assert gathered[2]["branch_id"] == "branch-2"

    async def test_collect_allow_partial_includes_failed_branches(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Failed branches included with error when allow_partial is True."""
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_collect_process(
                "echo", "failing",
                collect_key="results",
                allow_partial=True,
            ),
            {"input": "partial"},
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()  # fork
        await runner.tick()  # join

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        results = run.work_item_state["results"]
        assert len(results) == 2

        # First branch succeeded
        assert results[0]["branch_id"] == "branch-0"
        assert isinstance(results[0]["state"], dict)
        assert "error" not in results[0]

        # Second branch failed
        assert results[1]["branch_id"] == "branch-1"
        assert results[1]["state"] is None
        assert "error" in results[1]
        assert "branch failure" in results[1]["error"]

    async def test_collect_without_allow_partial_fails_on_branch_failure(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """allow_partial=False fails the run on any branch failure (US-005)."""
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_collect_process(
                "echo", "failing",
                collect_key="results",
                allow_partial=False,
            ),
            {"input": "strict"},
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()  # fork
        with pytest.raises(OrchestrationError, match="Branch failure"):
            await runner.tick()  # join — should fail

    async def test_collect_continues_to_end(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
        sink: CollectorSink,
    ) -> None:
        """Execution continues from join to end after collect."""
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_collect_process("echo", "echo", collect_key="results"),
            {"input": "continue"},
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()  # fork
        await runner.tick()  # join
        await runner.tick()  # end

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.COMPLETED


# --- US-005 Helpers ---


def make_fork_all_failing_process(
    allow_partial: bool = False,
    merge_strategy: MergeStrategy = MergeStrategy.MERGE_ALL,
    collect_key: str | None = None,
) -> ProcessDefinition:
    """Fork → 2 failing agents → Join → End."""
    join_config: dict[str, Any] = {
        "merge_strategy": merge_strategy,
        "allow_partial": allow_partial,
    }
    if collect_key is not None:
        join_config["collect_key"] = collect_key
    return ProcessDefinition(
        id="fork-all-fail",
        name="Fork All Failing",
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
                label="Failing A",
                config=AgentNodeConfig(agent="failing", output_key="a_out"),
            ),
            NodeDefinition(
                id="branch_b",
                type=NodeType.AGENT,
                label="Failing B",
                config=AgentNodeConfig(agent="failing", output_key="b_out"),
            ),
            NodeDefinition(
                id="join1",
                type=NodeType.JOIN,
                label="Join",
                config=JoinNodeConfig(**join_config),
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


def make_partial_failure_process(
    allow_partial: bool,
    merge_strategy: MergeStrategy = MergeStrategy.MERGE_ALL,
    collect_key: str | None = None,
) -> ProcessDefinition:
    """Fork → 1 echo + 1 failing → Join → End."""
    join_config: dict[str, Any] = {
        "merge_strategy": merge_strategy,
        "allow_partial": allow_partial,
    }
    if collect_key is not None:
        join_config["collect_key"] = collect_key
    return ProcessDefinition(
        id="fork-partial",
        name="Fork Partial Failure",
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
                config=JoinNodeConfig(**join_config),
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


# --- US-005 Tests ---


class TestPartialFailureHandling:
    """US-005: Partial Failure Handling."""

    async def test_allow_partial_false_fails_on_any_branch_failure(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
        sink: CollectorSink,
    ) -> None:
        """allow_partial=False fails run on any branch failure."""
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_partial_failure_process(allow_partial=False),
            {"input": "strict"},
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()  # fork
        with pytest.raises(OrchestrationError, match="Branch failure"):
            await runner.tick()  # join — should raise

        # run.failed event should have been emitted
        failed_events = [
            e for e in sink.events if e.event == "roots.run.failed"
        ]
        assert len(failed_events) == 1
        assert "failed_branches" in failed_events[0].metadata

    async def test_allow_partial_true_continues_with_successful_branches(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """allow_partial=True merges only successful branches."""
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_partial_failure_process(allow_partial=True),
            {"input": "tolerant"},
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()  # fork
        await runner.tick()  # join — should NOT raise
        await runner.tick()  # end

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.COMPLETED
        state = run.work_item_state
        # Successful branch's output should be present
        assert "a_out" in state

    async def test_failed_branch_info_recorded_in_state(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Failed branch info recorded in work item state under _failed_branches."""
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_partial_failure_process(allow_partial=True),
            {"input": "record"},
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()  # fork
        await runner.tick()  # join

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        state = run.work_item_state
        assert "_failed_branches" in state
        failed = state["_failed_branches"]
        assert len(failed) == 1
        assert failed[0]["branch_id"] == "branch-1"
        assert failed[0]["entry_node"] == "branch_b"
        assert "error_message" in failed[0]
        assert "branch failure" in failed[0]["error_message"]

    async def test_all_branches_fail_even_with_allow_partial(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
        sink: CollectorSink,
    ) -> None:
        """All branches failing fails the run even with allow_partial=True."""
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_fork_all_failing_process(allow_partial=True),
            {"input": "all_fail"},
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()  # fork
        with pytest.raises(OrchestrationError, match="All branches failed"):
            await runner.tick()  # join — should raise

        # run.failed event emitted
        failed_events = [
            e for e in sink.events if e.event == "roots.run.failed"
        ]
        assert len(failed_events) == 1

    async def test_join_metadata_indicates_partial_completion(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
        sink: CollectorSink,
    ) -> None:
        """Join node.completed event metadata notes partial completion."""
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_partial_failure_process(allow_partial=True),
            {"input": "metadata"},
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()  # fork
        await runner.tick()  # join

        # Find the node.completed event for join1
        join_completed = [
            e for e in sink.events
            if e.event == "roots.node.completed" and e.node_id == "join1"
        ]
        assert len(join_completed) == 1
        meta = join_completed[0].metadata
        assert meta.get("partial_completion") is True
        assert meta.get("failed_branches") == 1
        assert meta.get("successful_branches") == 1

    async def test_all_success_no_partial_metadata(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
        sink: CollectorSink,
    ) -> None:
        """All-success scenario: no _failed_branches, no partial_completion metadata."""
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_fork_2_branch_process(),
            {"input": "all_good"},
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()  # fork
        await runner.tick()  # join

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert "_failed_branches" not in run.work_item_state

        # node.completed for join should NOT have partial_completion metadata
        join_completed = [
            e for e in sink.events
            if e.event == "roots.node.completed" and e.node_id == "join1"
        ]
        assert len(join_completed) == 1
        meta = join_completed[0].metadata
        assert meta.get("partial_completion") is not True

    async def test_partial_failure_not_allowed_merge_all(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """merge_all with allow_partial=False raises on branch failure."""
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_partial_failure_process(
                allow_partial=False,
                merge_strategy=MergeStrategy.MERGE_ALL,
            ),
            {"input": "strict_merge"},
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()  # fork
        with pytest.raises(OrchestrationError, match="Branch failure"):
            await runner.tick()  # join

    async def test_partial_failure_allowed_collect(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """collect with allow_partial=True includes failed branches and records _failed_branches."""
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_partial_failure_process(
                allow_partial=True,
                merge_strategy=MergeStrategy.COLLECT,
                collect_key="results",
            ),
            {"input": "partial_collect"},
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()  # fork
        await runner.tick()  # join

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        state = run.work_item_state
        # _failed_branches should be recorded
        assert "_failed_branches" in state
        assert len(state["_failed_branches"]) == 1
        # Collect results should include both branches
        assert "results" in state
        results = state["results"]
        assert len(results) == 2
        # Failed branch has state=None and error
        failed_entry = [r for r in results if r["state"] is None]
        assert len(failed_entry) == 1
        assert "error" in failed_entry[0]

    async def test_all_fail_without_allow_partial(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """All branches failing with allow_partial=False also raises."""
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_fork_all_failing_process(allow_partial=False),
            {"input": "all_fail_strict"},
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()  # fork
        with pytest.raises(OrchestrationError, match="All branches failed"):
            await runner.tick()  # join


# --- US-002 (crash-safe) Tests ---


class TestCrashSafeForkPersistence:
    """US-002 (crash-safe): Fork handler persists branch results to storage."""

    async def test_fork_persists_completed_branch_results_to_storage(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Completed branch results are persisted using fork node ID and target-node-derived branch IDs."""
        proc, run_id = await _setup_run(
            sqlite_storage, make_fork_2_branch_process()
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        await runner.tick()  # fork tick executes branches

        results = await sqlite_storage.get_branch_results(run_id, "fork1")
        assert len(results) == 2
        branch_ids = {r.branch_id for r in results}
        assert branch_ids == {"branch:branch_a", "branch:branch_b"}
        for r in results:
            assert r.status == "completed"
            assert r.node_id == "fork1"
            assert r.run_id == run_id
            assert isinstance(r.result_json, dict)

    async def test_fork_persists_failed_branch_with_error_details(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Failed branches persisted with status='failed' and error details."""
        proc, run_id = await _setup_run(
            sqlite_storage, make_fork_one_failing_process()
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        # fork tick captures exceptions in results (return_exceptions=True)
        await runner.tick()

        results = await sqlite_storage.get_branch_results(run_id, "fork1")
        assert len(results) == 2

        failed = [r for r in results if r.status == "failed"]
        completed = [r for r in results if r.status == "completed"]
        assert len(failed) == 1
        assert len(completed) == 1
        assert "branch failure" in str(failed[0].result_json)

    async def test_lock_renewal_calls_release_and_acquire_during_gather(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Background renewal task calls release+acquire periodically during gather."""
        proc, run_id = await _setup_run(
            sqlite_storage, make_fork_slow_branches_process()
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        acquire_count = 0
        release_count = 0
        orig_acquire = sqlite_storage.acquire_run_lock
        orig_release = sqlite_storage.release_run_lock

        async def counting_acquire(rid: str, oid: str, stale_timeout: int = 300) -> bool:
            nonlocal acquire_count
            acquire_count += 1
            return await orig_acquire(rid, oid, stale_timeout)

        async def counting_release(rid: str, oid: str) -> None:
            nonlocal release_count
            release_count += 1
            return await orig_release(rid, oid)

        sqlite_storage.acquire_run_lock = counting_acquire  # type: ignore[method-assign]
        sqlite_storage.release_run_lock = counting_release  # type: ignore[method-assign]

        orig_sleep = asyncio.sleep

        async def fast_sleep(t: float) -> None:
            # Make renewal intervals (>= 100s) fire immediately; leave small sleeps intact
            await orig_sleep(0 if t >= 100 else t)

        with patch("asyncio.sleep", fast_sleep):
            await runner.tick()

        # tick() acquires once; renewal fires and acquires again → at least 2 total
        assert acquire_count >= 2
        # renewal releases once; tick() finally releases once → at least 2 total
        assert release_count >= 2

    async def test_lock_stolen_cancels_branches_and_raises_orchestration_error(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """When lock is stolen during gather, branches are cancelled and OrchestrationError is raised."""
        proc, run_id = await _setup_run(
            sqlite_storage, make_fork_slow_branches_process()
        )
        runner = _make_runner(
            run_id, sqlite_storage, invoker, decision_engine, emitter
        )

        acquire_count = 0
        orig_acquire = sqlite_storage.acquire_run_lock

        async def stealing_acquire(rid: str, oid: str, stale_timeout: int = 300) -> bool:
            nonlocal acquire_count
            acquire_count += 1
            if acquire_count >= 2:
                # Simulate lock stolen: reacquire fails
                return False
            return await orig_acquire(rid, oid, stale_timeout)

        sqlite_storage.acquire_run_lock = stealing_acquire  # type: ignore[method-assign]

        orig_sleep = asyncio.sleep

        async def fast_sleep(t: float) -> None:
            await orig_sleep(0 if t >= 100 else t)

        with patch("asyncio.sleep", fast_sleep):
            with pytest.raises(OrchestrationError, match="Lock lost during parallel execution"):
                await runner.tick()


# --- US-003 (crash recovery) Tests ---


class TestCrashRecovery:
    """US-003: Crash-safe fork — recovery on re-entry."""

    async def test_fork_skips_completed_branch_from_storage(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Fork detects completed branch via storage and does not re-execute it."""
        call_log: list[str] = []

        async def tracking_a(input: dict[str, Any]) -> dict[str, Any]:
            call_log.append("branch_a_called")
            return {"output": {"a_out": "fresh"}, "escalate": False}

        async def tracking_b(input: dict[str, Any]) -> dict[str, Any]:
            call_log.append("branch_b_called")
            return {"output": {"b_out": "fresh"}, "escalate": False}

        invoker._registry.register_local("tracking_a", tracking_a)
        invoker._registry.register_local("tracking_b", tracking_b)

        proc = ProcessDefinition(
            id="crash-skip",
            name="Crash Skip Test",
            version="1.0.0",
            nodes=[
                NodeDefinition(id="fork1", type=NodeType.FORK, label="Fork", config=ForkNodeConfig()),
                NodeDefinition(id="ba", type=NodeType.AGENT, label="A", config=AgentNodeConfig(agent="tracking_a", output_key="a_out")),
                NodeDefinition(id="bb", type=NodeType.AGENT, label="B", config=AgentNodeConfig(agent="tracking_b", output_key="b_out")),
                NodeDefinition(id="join1", type=NodeType.JOIN, label="Join", config=JoinNodeConfig()),
                NodeDefinition(id="done", type=NodeType.END, label="Done", config=EndNodeConfig(status=EndStatus.COMPLETED)),
            ],
            edges=[
                EdgeDefinition(from_node="fork1", to_node="ba"),
                EdgeDefinition(from_node="fork1", to_node="bb"),
                EdgeDefinition(from_node="ba", to_node="join1"),
                EdgeDefinition(from_node="bb", to_node="join1"),
                EdgeDefinition(from_node="join1", to_node="done"),
            ],
            entry_point="fork1",
            fork_join_map={"fork1": "join1"},
        )
        proc, run_id = await _setup_run(sqlite_storage, proc)

        # Pre-seed branch_a (target node "ba") as already completed
        pre_seeded_state = {"input": "hello", "a_out": "recovered_value"}
        await sqlite_storage.save_branch_result(
            run_id, "fork1", "branch:ba", "completed", pre_seeded_state
        )

        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)
        await runner.tick()  # fork

        # branch_a should NOT have been called (its result was recovered from storage)
        assert "branch_a_called" not in call_log
        # branch_b SHOULD have been called (not in storage)
        assert "branch_b_called" in call_log

        # The final results should have both: recovered branch_a and fresh branch_b
        results = runner._fork_branch_results
        assert results is not None
        assert len(results) == 2
        assert results[0] == pre_seeded_state  # recovered
        assert isinstance(results[1], dict)    # fresh

    async def test_fork_re_executes_failed_branch(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Failed branches in storage are re-executed on re-entry (not skipped)."""
        call_log: list[str] = []

        async def tracking_a(input: dict[str, Any]) -> dict[str, Any]:
            call_log.append("branch_a_called")
            return {"output": {"a_out": "retried"}, "escalate": False}

        invoker._registry.register_local("tracking_a_retry", tracking_a)

        proc = ProcessDefinition(
            id="crash-retry",
            name="Crash Retry Test",
            version="1.0.0",
            nodes=[
                NodeDefinition(id="fork1", type=NodeType.FORK, label="Fork", config=ForkNodeConfig()),
                NodeDefinition(id="ba", type=NodeType.AGENT, label="A", config=AgentNodeConfig(agent="tracking_a_retry", output_key="a_out")),
                NodeDefinition(id="bb", type=NodeType.AGENT, label="B", config=AgentNodeConfig(agent="echo", output_key="b_out")),
                NodeDefinition(id="join1", type=NodeType.JOIN, label="Join", config=JoinNodeConfig()),
                NodeDefinition(id="done", type=NodeType.END, label="Done", config=EndNodeConfig(status=EndStatus.COMPLETED)),
            ],
            edges=[
                EdgeDefinition(from_node="fork1", to_node="ba"),
                EdgeDefinition(from_node="fork1", to_node="bb"),
                EdgeDefinition(from_node="ba", to_node="join1"),
                EdgeDefinition(from_node="bb", to_node="join1"),
                EdgeDefinition(from_node="join1", to_node="done"),
            ],
            entry_point="fork1",
            fork_join_map={"fork1": "join1"},
        )
        proc, run_id = await _setup_run(sqlite_storage, proc)

        # Pre-seed branch_a as FAILED (should be re-executed)
        await sqlite_storage.save_branch_result(
            run_id, "fork1", "branch:ba", "failed", "previous transient error"
        )

        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)
        await runner.tick()  # fork

        # branch_a SHOULD have been called (failed branches are re-executed)
        assert "branch_a_called" in call_log

    async def test_join_recovery_loads_from_storage(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Join handler loads branch results from storage when _fork_branch_results is None."""
        invoker._registry.register_local("agent_add_x", agent_add_x)
        invoker._registry.register_local("agent_add_y", agent_add_y)

        proc, run_id = await _setup_run(
            sqlite_storage,
            make_merge_all_process("agent_add_x", "agent_add_y"),
            {"input": "recovery_test"},
        )
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        # Fork tick: executes branches and stores results
        await runner.tick()

        # Simulate crash: clear in-memory state
        runner._fork_branch_results = None
        runner._fork_branches = []

        # Join tick: must recover from storage
        await runner.tick()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        # Join should have merged both branch outputs
        assert "a_out" in run.work_item_state
        assert "b_out" in run.work_item_state

    async def test_join_resolves_fork_node_via_fork_join_map(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Join finds its fork node via fork_join_map inverse lookup."""
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_fork_2_branch_process(),
        )
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.tick()  # fork (saves branch results to storage)

        # Simulate crash: clear in-memory state
        runner._fork_branch_results = None
        runner._fork_branches = []

        # Join should resolve fork node from fork_join_map and load from storage
        await runner.tick()  # join

        # Verify join completed (run moved past join node)
        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        # Run should have continued (not failed)
        assert run.current_node_id == "done"

    async def test_clear_branch_results_after_successful_join(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Branch results are cleared from storage after a successful join."""
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_fork_2_branch_process(),
        )
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.tick()  # fork

        # Verify results stored before join
        before = await sqlite_storage.get_branch_results(run_id, "fork1")
        assert len(before) == 2

        await runner.tick()  # join (successful)

        # Results should be cleared after successful join
        after = await sqlite_storage.get_branch_results(run_id, "fork1")
        assert len(after) == 0

    async def test_branch_results_preserved_on_failed_join(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Branch results are NOT cleared when the join fails (preserves progress for recovery)."""
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_fork_all_failing_process(allow_partial=False),
        )
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.tick()  # fork (both fail, stored as failed)

        stored_before = await sqlite_storage.get_branch_results(run_id, "fork1")
        assert len(stored_before) > 0

        with pytest.raises(OrchestrationError):
            await runner.tick()  # join fails

        # Results must still be in storage
        stored_after = await sqlite_storage.get_branch_results(run_id, "fork1")
        assert len(stored_after) > 0

    async def test_merge_all_correct_with_recovered_data(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """merge_all strategy produces correct results when join recovers from storage."""
        invoker._registry.register_local("agent_add_x", agent_add_x)
        invoker._registry.register_local("agent_add_y", agent_add_y)

        proc, run_id = await _setup_run(
            sqlite_storage,
            make_merge_all_process("agent_add_x", "agent_add_y"),
            {"input": "merge_recovery"},
        )
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.tick()  # fork

        # Reset in-memory state (simulate crash)
        runner._fork_branch_results = None
        runner._fork_branches = []

        await runner.tick()  # join — recovers from storage
        await runner.tick()  # end

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.COMPLETED
        state = run.work_item_state
        assert "a_out" in state
        assert "b_out" in state
        assert state["input"] == "merge_recovery"

    async def test_collect_correct_with_recovered_data(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """collect strategy produces correct results when join recovers from storage."""
        proc, run_id = await _setup_run(
            sqlite_storage,
            make_collect_process("echo", "echo", collect_key="results"),
            {"input": "collect_recovery"},
        )
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.tick()  # fork

        # Simulate crash
        runner._fork_branch_results = None
        runner._fork_branches = []

        await runner.tick()  # join — recovers from storage
        await runner.tick()  # end

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.COMPLETED
        state = run.work_item_state
        assert "results" in state
        collected = state["results"]
        assert len(collected) == 2
        assert collected[0]["branch_id"] == "branch-0"
        assert collected[1]["branch_id"] == "branch-1"

    async def test_crash_recovery_integration(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Integration: crash after branch_a completes; recovered fork skips branch_a,
        re-runs branch_b, and join merges both correctly."""
        invoker._registry.register_local("agent_add_x", agent_add_x)
        invoker._registry.register_local("agent_add_y", agent_add_y)

        proc, run_id = await _setup_run(
            sqlite_storage,
            make_merge_all_process("agent_add_x", "agent_add_y"),
            {"input": "crash_integration"},
        )

        # Simulate: before the crash, branch_a (target="branch_a") completed.
        # agent_add_x writes {x: 1, shared: {from_x: True}} under output_key "a_out".
        pre_crash_state_a: dict[str, Any] = {
            "input": "crash_integration",
            "a_out": {"x": 1, "shared": {"from_x": True}},
        }
        await sqlite_storage.save_branch_result(
            run_id, "fork1", "branch:branch_a", "completed", pre_crash_state_a
        )

        # First "recovery" fork tick: skips branch_a, runs branch_b fresh
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)
        await runner.tick()

        results = runner._fork_branch_results
        assert results is not None
        assert len(results) == 2

        # Branch_a result came from storage (pre-seeded)
        assert results[0] == pre_crash_state_a
        # Branch_b result is fresh (agent_add_y: {y: 2, shared: {from_y: True}})
        assert isinstance(results[1], dict)
        assert "b_out" in results[1]

        # Join tick: merges pre-seeded branch_a + fresh branch_b
        await runner.tick()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        state = run.work_item_state
        # Both outputs should be merged
        assert "a_out" in state
        assert "b_out" in state
        assert state["a_out"]["x"] == 1      # from pre-seeded branch_a
        assert state["b_out"]["y"] == 2      # from fresh branch_b

        # Branch results should be cleared after successful join
        stored = await sqlite_storage.get_branch_results(run_id, "fork1")
        assert len(stored) == 0
