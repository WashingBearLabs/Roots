"""Tests for edge evaluation and state accumulation (US-005)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from roots.agents.registry import AgentRegistry
from roots.agents.invoker import AgentInvoker
from roots.core.decision import DecisionEngine, DecisionResult
from roots.core.orchestrator import OrchestrationError, ProcessRunner
from roots.core.schema import (
    AgentNodeConfig,
    CheckpointNodeConfig,
    DecisionEdge,
    DecisionMode,
    DecisionNodeConfig,
    EdgeDefinition,
    EmitNodeConfig,
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
def sink() -> CollectorSink:
    return CollectorSink()


@pytest.fixture
def emitter(sink: CollectorSink) -> EventEmitter:
    return EventEmitter(sinks=[sink])


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


# --- Edge Evaluation Tests ---


class TestDecisionNodeRouting:
    """AC: Decision node next node comes from handler return value."""

    async def test_decision_next_node_from_handler(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        """Decision handler sets _decision_next_node, tick uses it."""
        proc = ProcessDefinition(
            id="decision-route",
            name="Decision Route",
            version="1.0.0",
            nodes=[
                NodeDefinition(
                    id="gate",
                    type=NodeType.DECISION,
                    label="Gate",
                    config=DecisionNodeConfig(
                        mode=DecisionMode.DETERMINISTIC,
                        edges=[
                            DecisionEdge(target="good", condition="True"),
                            DecisionEdge(target="bad", condition="False"),
                        ],
                    ),
                ),
                NodeDefinition(
                    id="good",
                    type=NodeType.END,
                    label="Good",
                    config=EndNodeConfig(status=EndStatus.COMPLETED),
                ),
                NodeDefinition(
                    id="bad",
                    type=NodeType.END,
                    label="Bad",
                    config=EndNodeConfig(status=EndStatus.FAILED),
                ),
            ],
            edges=[],
            entry_point="gate",
        )
        await sqlite_storage.save_process(proc)
        run = await sqlite_storage.create_run(proc.id, {"val": 1})

        decision_engine = DecisionEngine(default_model="gpt-4o-mini")
        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)
        await runner.run_to_completion()

        final = await sqlite_storage.get_run(run.id)
        assert final is not None
        # Routed to "good" end node → completed
        assert final.status == "completed"


class TestEdgeAdvancement:
    """AC: Non-decision nodes advance via first outbound edge."""

    async def test_agent_advances_via_outbound_edge(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        emitter: EventEmitter,
    ) -> None:
        proc = ProcessDefinition(
            id="edge-advance",
            name="Edge Advance",
            version="1.0.0",
            nodes=[
                NodeDefinition(
                    id="a",
                    type=NodeType.AGENT,
                    label="A",
                    config=AgentNodeConfig(agent="echo", output_key="a_out"),
                ),
                NodeDefinition(
                    id="b",
                    type=NodeType.AGENT,
                    label="B",
                    config=AgentNodeConfig(agent="echo", output_key="b_out"),
                ),
                NodeDefinition(
                    id="done",
                    type=NodeType.END,
                    label="Done",
                    config=EndNodeConfig(status=EndStatus.COMPLETED),
                ),
            ],
            edges=[
                EdgeDefinition(from_node="a", to_node="b"),
                EdgeDefinition(from_node="b", to_node="done"),
            ],
            entry_point="a",
        )
        await sqlite_storage.save_process(proc)
        run = await sqlite_storage.create_run(proc.id, {"x": 1})

        decision_engine = DecisionEngine(default_model="gpt-4o-mini")
        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)

        # First tick: node "a" → advances to "b" via edge
        await runner.tick()
        run_after = await sqlite_storage.get_run(run.id)
        assert run_after is not None
        assert run_after.current_node_id == "b"

        # Second tick: node "b" → advances to "done"
        await runner.tick()
        run_after = await sqlite_storage.get_run(run.id)
        assert run_after is not None
        assert run_after.current_node_id == "done"


class TestMissingEdge:
    """AC: Missing outbound edge raises OrchestrationError."""

    async def test_no_outbound_edge_raises(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        emitter: EventEmitter,
    ) -> None:
        proc = ProcessDefinition(
            id="no-edge",
            name="No Edge",
            version="1.0.0",
            nodes=[
                NodeDefinition(
                    id="orphan",
                    type=NodeType.AGENT,
                    label="Orphan",
                    config=AgentNodeConfig(agent="echo", output_key="out"),
                ),
            ],
            edges=[],
            entry_point="orphan",
        )
        await sqlite_storage.save_process(proc)
        run = await sqlite_storage.create_run(proc.id, {"x": 1})

        decision_engine = DecisionEngine(default_model="gpt-4o-mini")
        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)

        with pytest.raises(OrchestrationError, match="has no outbound edges"):
            await runner.tick()


# --- State Accumulation Tests ---


class TestStateAccumulation:
    """AC: Output written to state[output_key] as full dict.
    AC: State accumulates across multiple nodes.
    AC: 3-node process where node2 reads key1 from state.
    """

    async def test_output_stored_as_full_dict(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        emitter: EventEmitter,
    ) -> None:
        """Output dict is stored under output_key, not flattened."""
        proc = ProcessDefinition(
            id="full-dict",
            name="Full Dict",
            version="1.0.0",
            nodes=[
                NodeDefinition(
                    id="step1",
                    type=NodeType.AGENT,
                    label="Step 1",
                    config=AgentNodeConfig(agent="echo", output_key="validation"),
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
        await sqlite_storage.save_process(proc)
        run = await sqlite_storage.create_run(proc.id, {"input": "test"})

        decision_engine = DecisionEngine(default_model="gpt-4o-mini")
        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)
        await runner.tick()

        run_after = await sqlite_storage.get_run(run.id)
        assert run_after is not None
        # The echo agent returns {"echo": state} — full dict stored under key
        assert "validation" in run_after.work_item_state
        assert isinstance(run_after.work_item_state["validation"], dict)
        assert "echo" in run_after.work_item_state["validation"]

    async def test_three_node_state_accumulation_node2_reads_node1(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        emitter: EventEmitter,
    ) -> None:
        """3-node process: node1 writes key1, node2 reads key1 from state."""
        proc = ProcessDefinition(
            id="accumulate",
            name="Accumulate",
            version="1.0.0",
            nodes=[
                NodeDefinition(
                    id="node1",
                    type=NodeType.AGENT,
                    label="Node 1",
                    config=AgentNodeConfig(agent="echo", output_key="key1"),
                ),
                NodeDefinition(
                    id="node2",
                    type=NodeType.AGENT,
                    label="Node 2",
                    config=AgentNodeConfig(agent="echo", output_key="key2"),
                ),
                NodeDefinition(
                    id="done",
                    type=NodeType.END,
                    label="Done",
                    config=EndNodeConfig(status=EndStatus.COMPLETED),
                ),
            ],
            edges=[
                EdgeDefinition(from_node="node1", to_node="node2"),
                EdgeDefinition(from_node="node2", to_node="done"),
            ],
            entry_point="node1",
        )
        await sqlite_storage.save_process(proc)
        run = await sqlite_storage.create_run(proc.id, {"seed": 42})

        decision_engine = DecisionEngine(default_model="gpt-4o-mini")
        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)
        await runner.run_to_completion()

        final = await sqlite_storage.get_run(run.id)
        assert final is not None
        assert final.status == "completed"

        state = final.work_item_state
        # Both nodes wrote their output under separate keys
        assert "key1" in state
        assert "key2" in state

        # node1 saw initial state (seed only)
        assert state["key1"]["echo"]["seed"] == 42
        assert "key1" not in state["key1"]["echo"]

        # node2 saw state INCLUDING key1 — proves it read node1's output
        assert "key1" in state["key2"]["echo"]
        assert state["key2"]["echo"]["key1"] == state["key1"]

    async def test_each_node_gets_own_key(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        emitter: EventEmitter,
    ) -> None:
        """Each node's output stored under its own output_key, not merged."""
        proc = ProcessDefinition(
            id="own-keys",
            name="Own Keys",
            version="1.0.0",
            nodes=[
                NodeDefinition(
                    id="a",
                    type=NodeType.AGENT,
                    label="A",
                    config=AgentNodeConfig(agent="echo", output_key="alpha"),
                ),
                NodeDefinition(
                    id="b",
                    type=NodeType.AGENT,
                    label="B",
                    config=AgentNodeConfig(agent="echo", output_key="beta"),
                ),
                NodeDefinition(
                    id="done",
                    type=NodeType.END,
                    label="Done",
                    config=EndNodeConfig(status=EndStatus.COMPLETED),
                ),
            ],
            edges=[
                EdgeDefinition(from_node="a", to_node="b"),
                EdgeDefinition(from_node="b", to_node="done"),
            ],
            entry_point="a",
        )
        await sqlite_storage.save_process(proc)
        run = await sqlite_storage.create_run(proc.id, {"init": True})

        decision_engine = DecisionEngine(default_model="gpt-4o-mini")
        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)
        await runner.run_to_completion()

        final = await sqlite_storage.get_run(run.id)
        assert final is not None
        # Keys are separate — alpha and beta
        assert "alpha" in final.work_item_state
        assert "beta" in final.work_item_state
        assert "alpha" != "beta"


class TestNoOutputKeySkipsState:
    """AC: Nodes without output_key don't modify state."""

    async def test_emit_node_does_not_add_to_state(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        emitter: EventEmitter,
    ) -> None:
        proc = ProcessDefinition(
            id="no-output",
            name="No Output Key",
            version="1.0.0",
            nodes=[
                NodeDefinition(
                    id="step1",
                    type=NodeType.AGENT,
                    label="Step 1",
                    config=AgentNodeConfig(agent="echo", output_key="step1_out"),
                ),
                NodeDefinition(
                    id="notify",
                    type=NodeType.EMIT,
                    label="Notify",
                    config=EmitNodeConfig(
                        event_type="custom.test",
                        payload_keys=["step1_out"],
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
                EdgeDefinition(from_node="step1", to_node="notify"),
                EdgeDefinition(from_node="notify", to_node="done"),
            ],
            entry_point="step1",
        )
        await sqlite_storage.save_process(proc)
        run = await sqlite_storage.create_run(proc.id, {"input": "test"})

        decision_engine = DecisionEngine(default_model="gpt-4o-mini")
        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)
        await runner.run_to_completion()

        final = await sqlite_storage.get_run(run.id)
        assert final is not None
        # Only step1_out and original input should be in state
        # Emit, End nodes have no output_key — they don't add keys
        state_keys = set(final.work_item_state.keys())
        assert state_keys == {"input", "step1_out"}

    async def test_checkpoint_does_not_add_to_state(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        emitter: EventEmitter,
    ) -> None:
        proc = ProcessDefinition(
            id="cp-no-state",
            name="Checkpoint No State",
            version="1.0.0",
            nodes=[
                NodeDefinition(
                    id="review",
                    type=NodeType.CHECKPOINT,
                    label="Review",
                    config=CheckpointNodeConfig(prompt="Review please"),
                ),
                NodeDefinition(
                    id="done",
                    type=NodeType.END,
                    label="Done",
                    config=EndNodeConfig(status=EndStatus.COMPLETED),
                ),
            ],
            edges=[EdgeDefinition(from_node="review", to_node="done")],
            entry_point="review",
        )
        await sqlite_storage.save_process(proc)
        run = await sqlite_storage.create_run(proc.id, {"only_this": True})

        decision_engine = DecisionEngine(default_model="gpt-4o-mini")
        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)
        await runner.tick()

        final = await sqlite_storage.get_run(run.id)
        assert final is not None
        # Checkpoint returns None and has no output_key — state unchanged
        assert final.work_item_state == {"only_this": True}
