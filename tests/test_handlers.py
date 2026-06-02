"""Tests for Decision, Checkpoint, Emit, and End handlers (US-004)."""

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


# --- Decision Handler Tests ---


class TestDecisionHandler:
    @staticmethod
    def _make_decision_process() -> ProcessDefinition:
        """Agent → Decision → End(pass) / End(fail)."""
        return ProcessDefinition(
            id="decision-proc",
            name="Decision Process",
            version="1.0.0",
            nodes=[
                NodeDefinition(
                    id="start",
                    type=NodeType.AGENT,
                    label="Start",
                    config=AgentNodeConfig(agent="echo", output_key="start_out"),
                ),
                NodeDefinition(
                    id="gate",
                    type=NodeType.DECISION,
                    label="Gate",
                    config=DecisionNodeConfig(
                        mode=DecisionMode.DETERMINISTIC,
                        edges=[
                            DecisionEdge(
                                target="pass",
                                condition="start_out.echo.input == 'hello'",
                            ),
                            DecisionEdge(
                                target="fail",
                                condition="start_out.echo.input == 'bad'",
                            ),
                        ],
                    ),
                ),
                NodeDefinition(
                    id="pass",
                    type=NodeType.END,
                    label="Pass",
                    config=EndNodeConfig(status=EndStatus.COMPLETED),
                ),
                NodeDefinition(
                    id="fail",
                    type=NodeType.END,
                    label="Fail",
                    config=EndNodeConfig(status=EndStatus.FAILED),
                ),
            ],
            edges=[
                EdgeDefinition(from_node="start", to_node="gate"),
            ],
            entry_point="start",
        )

    async def test_decision_routes_to_selected_edge(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        decision_engine = DecisionEngine(default_model="gpt-4o-mini")
        proc = self._make_decision_process()
        await sqlite_storage.save_process(proc)
        run = await sqlite_storage.create_run(proc.id, {"input": "hello"})

        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)
        await runner.run_to_completion()
        await emitter.close()

        final = await sqlite_storage.get_run(run.id)
        assert final is not None
        assert final.status == "completed"

        # Verify decision.taken event was emitted
        taken = [e for e in sink.events if e.event == "roots.decision.taken"]
        assert len(taken) == 1
        assert taken[0].metadata["selected_edge"] == "pass"

    async def test_decision_records_to_storage(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        emitter: EventEmitter,
    ) -> None:
        decision_engine = DecisionEngine(default_model="gpt-4o-mini")
        proc = self._make_decision_process()
        await sqlite_storage.save_process(proc)
        run = await sqlite_storage.create_run(proc.id, {"input": "hello"})

        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)
        await runner.run_to_completion()

        decisions = await sqlite_storage.list_decisions(proc.id, "gate")
        assert len(decisions) == 1
        assert decisions[0].mode == "deterministic"
        assert decisions[0].confidence == 1.0

    async def test_decision_escalation_pauses_run(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        # Use a mock decision engine that returns an escalated result
        mock_engine = DecisionEngine(default_model="gpt-4o-mini")
        mock_engine.evaluate = AsyncMock(
            return_value=DecisionResult(
                selected_edge="pass",
                mode="ai_bounded",
                confidence=0.3,
                reasoning="Low confidence",
                escalated=True,
                ai_recommendation=None,
            )
        )

        proc = self._make_decision_process()
        await sqlite_storage.save_process(proc)
        run = await sqlite_storage.create_run(proc.id, {"input": "hello"})

        runner = _make_runner(run.id, sqlite_storage, invoker, mock_engine, emitter)

        # First tick: agent node
        await runner.tick()
        # Second tick: decision node → escalation → paused
        await runner.tick()
        await emitter.close()

        final = await sqlite_storage.get_run(run.id)
        assert final is not None
        assert final.status == "paused"

        # Verify escalation events
        escalated = [e for e in sink.events if e.event == "roots.decision.escalated"]
        checkpoint = [e for e in sink.events if e.event == "roots.checkpoint.reached"]
        assert len(escalated) == 1
        assert len(checkpoint) == 1

        # Verify checkpoint record created
        cp = await sqlite_storage.get_pending_checkpoint(run.id)
        assert cp is not None
        assert cp.checkpoint_type == "escalation"


# --- Checkpoint Handler Tests ---


class TestCheckpointHandler:
    @staticmethod
    def _make_checkpoint_process() -> ProcessDefinition:
        """Checkpoint → Agent → End."""
        return ProcessDefinition(
            id="checkpoint-proc",
            name="Checkpoint Process",
            version="1.0.0",
            nodes=[
                NodeDefinition(
                    id="review",
                    type=NodeType.CHECKPOINT,
                    label="Review",
                    config=CheckpointNodeConfig(prompt="Please review this item."),
                ),
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
            edges=[
                EdgeDefinition(from_node="review", to_node="step1"),
                EdgeDefinition(from_node="step1", to_node="done"),
            ],
            entry_point="review",
        )

    async def test_checkpoint_creates_record_and_pauses(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        decision_engine = DecisionEngine(default_model="gpt-4o-mini")
        proc = self._make_checkpoint_process()
        await sqlite_storage.save_process(proc)
        run = await sqlite_storage.create_run(proc.id, {"input": "test"})

        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)
        result = await runner.tick()
        await emitter.close()

        # Should pause (tick returns False)
        assert result is False

        # Run should be paused
        final = await sqlite_storage.get_run(run.id)
        assert final is not None
        assert final.status == "paused"

        # Checkpoint record created
        cp = await sqlite_storage.get_pending_checkpoint(run.id)
        assert cp is not None
        assert cp.checkpoint_type == "planned"
        assert cp.prompt == "Please review this item."

        # Checkpoint event emitted
        reached = [e for e in sink.events if e.event == "roots.checkpoint.reached"]
        assert len(reached) == 1
        assert reached[0].node_id == "review"


# --- Emit Handler Tests ---


class TestEmitHandler:
    @staticmethod
    def _make_emit_process() -> ProcessDefinition:
        """Agent → Emit → End."""
        return ProcessDefinition(
            id="emit-proc",
            name="Emit Process",
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
                        event_type="custom.notification",
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

    async def test_emit_fires_custom_event_with_payload(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        decision_engine = DecisionEngine(default_model="gpt-4o-mini")
        proc = self._make_emit_process()
        await sqlite_storage.save_process(proc)
        run = await sqlite_storage.create_run(proc.id, {"input": "hello"})

        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)
        await runner.run_to_completion()
        await emitter.close()

        # Verify custom event was emitted
        custom = [e for e in sink.events if e.event == "custom.notification"]
        assert len(custom) == 1
        assert custom[0].node_id == "notify"
        # Payload should contain step1_out from state
        assert "step1_out" in custom[0].metadata

    async def test_emit_skips_missing_payload_keys(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        """Emit node with payload_keys referencing missing state keys should emit with partial metadata."""
        decision_engine = DecisionEngine(default_model="gpt-4o-mini")
        proc = ProcessDefinition(
            id="emit-missing",
            name="Emit Missing Keys",
            version="1.0.0",
            nodes=[
                NodeDefinition(
                    id="notify",
                    type=NodeType.EMIT,
                    label="Notify",
                    config=EmitNodeConfig(
                        event_type="custom.test",
                        payload_keys=["missing_key", "also_missing"],
                    ),
                ),
                NodeDefinition(
                    id="done",
                    type=NodeType.END,
                    label="Done",
                    config=EndNodeConfig(status=EndStatus.COMPLETED),
                ),
            ],
            edges=[EdgeDefinition(from_node="notify", to_node="done")],
            entry_point="notify",
        )
        await sqlite_storage.save_process(proc)
        run = await sqlite_storage.create_run(proc.id, {"input": "test"})

        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)
        await runner.run_to_completion()
        await emitter.close()

        custom = [e for e in sink.events if e.event == "custom.test"]
        assert len(custom) == 1
        assert custom[0].metadata == {}


# --- End Handler Tests ---


class TestEndHandler:
    async def test_end_completed_emits_run_completed(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        decision_engine = DecisionEngine(default_model="gpt-4o-mini")
        proc = ProcessDefinition(
            id="end-completed",
            name="End Completed",
            version="1.0.0",
            nodes=[
                NodeDefinition(
                    id="done",
                    type=NodeType.END,
                    label="Done",
                    config=EndNodeConfig(status=EndStatus.COMPLETED),
                ),
            ],
            edges=[],
            entry_point="done",
        )
        await sqlite_storage.save_process(proc)
        run = await sqlite_storage.create_run(proc.id, {"input": "test"})

        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)
        await runner.run_to_completion()
        await emitter.close()

        final = await sqlite_storage.get_run(run.id)
        assert final is not None
        assert final.status == "completed"

        completed = [e for e in sink.events if e.event == "roots.run.completed"]
        assert len(completed) == 1

    async def test_end_failed_emits_run_failed(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        decision_engine = DecisionEngine(default_model="gpt-4o-mini")
        proc = ProcessDefinition(
            id="end-failed",
            name="End Failed",
            version="1.0.0",
            nodes=[
                NodeDefinition(
                    id="done",
                    type=NodeType.END,
                    label="Done",
                    config=EndNodeConfig(status=EndStatus.FAILED),
                ),
            ],
            edges=[],
            entry_point="done",
        )
        await sqlite_storage.save_process(proc)
        run = await sqlite_storage.create_run(proc.id, {"input": "test"})

        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)
        await runner.run_to_completion()
        await emitter.close()

        final = await sqlite_storage.get_run(run.id)
        assert final is not None
        assert final.status == "failed"

        failed = [e for e in sink.events if e.event == "roots.run.failed"]
        assert len(failed) == 1


# --- Dispatch Dict Tests ---


class TestDispatchDict:
    async def test_all_node_types_have_handlers(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        emitter: EventEmitter,
    ) -> None:
        """Verify dispatch dict routes all node types."""
        decision_engine = DecisionEngine(default_model="gpt-4o-mini")
        proc = ProcessDefinition(
            id="dummy",
            name="Dummy",
            version="1.0.0",
            nodes=[
                NodeDefinition(
                    id="done",
                    type=NodeType.END,
                    label="Done",
                    config=EndNodeConfig(status=EndStatus.COMPLETED),
                ),
            ],
            edges=[],
            entry_point="done",
        )
        await sqlite_storage.save_process(proc)
        run = await sqlite_storage.create_run(proc.id, {})

        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)

        # Build the dispatch dict to verify all types covered
        handlers = {
            NodeType.AGENT: runner._handle_agent,
            NodeType.AGENT_POOL: runner._handle_agent_pool,
            NodeType.DECISION: runner._handle_decision,
            NodeType.CHECKPOINT: runner._handle_checkpoint,
            NodeType.EMIT: runner._handle_emit,
            NodeType.END: runner._handle_end,
            NodeType.FORK: runner._handle_fork,
            NodeType.JOIN: runner._handle_join,
            NodeType.ITERATOR: runner._handle_iterator,
        }
        for node_type in NodeType:
            assert node_type in handlers, f"Missing handler for {node_type}"


# --- Fork/Join Stub Tests ---


class TestForkJoinStubs:
    async def test_fork_no_outbound_edges_raises(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        emitter: EventEmitter,
    ) -> None:
        decision_engine = DecisionEngine(default_model="gpt-4o-mini")
        proc = ProcessDefinition(
            id="fork-test",
            name="Fork Test",
            version="1.0.0",
            nodes=[
                NodeDefinition(
                    id="split",
                    type=NodeType.FORK,
                    label="Split",
                    config={},
                ),
                NodeDefinition(
                    id="done",
                    type=NodeType.END,
                    label="Done",
                    config=EndNodeConfig(status=EndStatus.COMPLETED),
                ),
            ],
            edges=[],
            entry_point="split",
        )
        await sqlite_storage.save_process(proc)
        run = await sqlite_storage.create_run(proc.id, {})

        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)
        with pytest.raises(OrchestrationError, match="no outbound edges"):
            await runner.tick()

    async def test_join_raises_without_branch_results(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        emitter: EventEmitter,
    ) -> None:
        decision_engine = DecisionEngine(default_model="gpt-4o-mini")
        proc = ProcessDefinition(
            id="join-test",
            name="Join Test",
            version="1.0.0",
            nodes=[
                NodeDefinition(
                    id="merge",
                    type=NodeType.JOIN,
                    label="Merge",
                    config={"merge_strategy": "merge_all"},
                ),
                NodeDefinition(
                    id="done",
                    type=NodeType.END,
                    label="Done",
                    config=EndNodeConfig(status=EndStatus.COMPLETED),
                ),
            ],
            edges=[EdgeDefinition(from_node="merge", to_node="done")],
            entry_point="merge",
        )
        await sqlite_storage.save_process(proc)
        run = await sqlite_storage.create_run(proc.id, {})

        runner = _make_runner(run.id, sqlite_storage, invoker, decision_engine, emitter)
        with pytest.raises(OrchestrationError, match="without branch results"):
            await runner.tick()
