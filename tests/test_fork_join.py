"""Tests for fork/join branch creation (US-001)."""

from __future__ import annotations

import copy
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
