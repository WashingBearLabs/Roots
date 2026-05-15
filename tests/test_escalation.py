"""Tests for escalation triggers (US-004).

Verifies that escalation is triggered automatically on:
- Schema validation failure on agent output
- AI confidence below threshold (decision escalation)
- Agent returning escalate: true

Each trigger should:
- Transition the run to 'paused'
- Emit roots.run.escalated event with trigger type
- Create an escalation record with work item state snapshot
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from roots.agents.invoker import AgentInvoker
from roots.agents.registry import AgentRegistry
from roots.core.decision import DecisionEngine, DecisionResult
from roots.core.escalation import EscalationTrigger, create_escalation_from_error
from roots.core.orchestrator import ProcessRunner
from roots.core.schema import (
    AgentNodeConfig,
    AgentPoolNodeConfig,
    DecisionEdge,
    DecisionMode,
    DecisionNodeConfig,
    EdgeDefinition,
    EndNodeConfig,
    EndStatus,
    ExecutionMode,
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


async def escalating_agent(input: dict[str, Any]) -> dict[str, Any]:
    return {
        "output": {"result": "done"},
        "escalate": True,
        "escalation_reason": "Agent wants human review",
    }


async def schema_fail_agent(input: dict[str, Any]) -> dict[str, Any]:
    """Returns output that will fail schema validation (missing 'status' field)."""
    return {"output": {"data": "no status field"}, "escalate": False}


def make_agent_process(agent_name: str = "echo") -> ProcessDefinition:
    return ProcessDefinition(
        id="esc-proc",
        name="Escalation Process",
        version="1.0.0",
        nodes=[
            NodeDefinition(
                id="agent-node",
                type=NodeType.AGENT,
                label="Agent",
                config=AgentNodeConfig(agent=agent_name, output_key="result"),
            ),
            NodeDefinition(
                id="done",
                type=NodeType.END,
                label="Done",
                config=EndNodeConfig(status=EndStatus.COMPLETED),
            ),
        ],
        edges=[EdgeDefinition(from_node="agent-node", to_node="done")],
        entry_point="agent-node",
    )


def make_pool_process(
    agent_name: str = "echo",
    mode: ExecutionMode = ExecutionMode.SEQUENTIAL,
) -> ProcessDefinition:
    return ProcessDefinition(
        id="pool-proc",
        name="Pool Process",
        version="1.0.0",
        nodes=[
            NodeDefinition(
                id="pool-node",
                type=NodeType.AGENT_POOL,
                label="Pool",
                config=AgentPoolNodeConfig(
                    agents=[agent_name],
                    execution_mode=mode,
                    output_key="result",
                ),
            ),
            NodeDefinition(
                id="done",
                type=NodeType.END,
                label="Done",
                config=EndNodeConfig(status=EndStatus.COMPLETED),
            ),
        ],
        edges=[EdgeDefinition(from_node="pool-node", to_node="done")],
        entry_point="pool-node",
    )


def make_decision_process() -> ProcessDefinition:
    return ProcessDefinition(
        id="decision-proc",
        name="Decision Process",
        version="1.0.0",
        nodes=[
            NodeDefinition(
                id="start",
                type=NodeType.AGENT,
                label="Start",
                config=AgentNodeConfig(agent="echo", output_key="data"),
            ),
            NodeDefinition(
                id="decide",
                type=NodeType.DECISION,
                label="Decision",
                config=DecisionNodeConfig(
                    mode=DecisionMode.AI_CHECKPOINT,
                    context_prompt="Review the data",
                    edges=[
                        DecisionEdge(
                            target="approve-end",
                            label="approve",
                            description="Approve",
                        ),
                        DecisionEdge(
                            target="reject-end",
                            label="reject",
                            description="Reject",
                        ),
                    ],
                    confidence_threshold=0.8,
                    checkpoint_prompt="AI confidence too low",
                ),
            ),
            NodeDefinition(
                id="approve-end",
                type=NodeType.END,
                label="Approved",
                config=EndNodeConfig(status=EndStatus.COMPLETED),
            ),
            NodeDefinition(
                id="reject-end",
                type=NodeType.END,
                label="Rejected",
                config=EndNodeConfig(status=EndStatus.FAILED),
            ),
        ],
        edges=[
            EdgeDefinition(from_node="start", to_node="decide"),
        ],
        entry_point="start",
    )


@pytest.fixture
async def sqlite_storage():
    from roots.storage.sqlite import SqliteBackend

    backend = SqliteBackend(":memory:")
    await backend.initialize()
    yield backend
    await backend.close()


STRICT_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["status"],
    "properties": {
        "status": {"type": "string"},
    },
}


@pytest.fixture
def registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg.register_local("echo", echo_agent)
    reg.register_local("escalating", escalating_agent)
    reg.register_local(
        "schema_fail", schema_fail_agent, output_schema=STRICT_OUTPUT_SCHEMA
    )
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


# --- Unit Tests for EscalationTrigger enum ---


class TestEscalationTrigger:
    def test_subprocess_paused_trigger_defined(self) -> None:
        assert EscalationTrigger.SUBPROCESS_PAUSED == "subprocess_paused"

    def test_all_four_triggers_defined(self) -> None:
        assert len(EscalationTrigger) == 4


# --- Unit Tests for create_escalation_from_error ---


class TestCreateEscalationFromError:
    @pytest.mark.asyncio
    async def test_creates_escalation_record(
        self, sqlite_storage: StorageBackend, sink: CollectorSink, emitter: EventEmitter
    ) -> None:
        process = make_agent_process()
        run_id = await _setup_run(sqlite_storage, process)

        esc_id = await create_escalation_from_error(
            storage=sqlite_storage,
            run_id=run_id,
            node_id="agent-node",
            trigger=EscalationTrigger.SCHEMA_VALIDATION_FAILURE,
            reason="bad schema",
            work_item_state={"input": "test"},
            emitter=emitter,
            process_id="esc-proc",
        )

        assert esc_id is not None
        esc = await sqlite_storage.get_pending_escalation(run_id)
        assert esc is not None
        assert esc.trigger_type == "schema_validation_failure"
        assert esc.reason == "bad schema"
        assert esc.work_item_snapshot == {"input": "test"}

    @pytest.mark.asyncio
    async def test_does_not_set_run_to_paused(
        self, sqlite_storage: StorageBackend, sink: CollectorSink, emitter: EventEmitter
    ) -> None:
        """create_escalation_from_error no longer sets status to PAUSED.

        The tick loop is responsible for the status transition.
        """
        process = make_agent_process()
        run_id = await _setup_run(sqlite_storage, process)

        await create_escalation_from_error(
            storage=sqlite_storage,
            run_id=run_id,
            node_id="agent-node",
            trigger=EscalationTrigger.AGENT_EXPLICIT_SIGNAL,
            reason="agent asked",
            work_item_state={"input": "test"},
            emitter=emitter,
        )

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        # Status should remain unchanged; the tick loop handles PAUSED transition
        assert run.status != RunStatus.PAUSED

    @pytest.mark.asyncio
    async def test_emits_run_escalated_event(
        self, sqlite_storage: StorageBackend, sink: CollectorSink, emitter: EventEmitter
    ) -> None:
        process = make_agent_process()
        run_id = await _setup_run(sqlite_storage, process)

        await create_escalation_from_error(
            storage=sqlite_storage,
            run_id=run_id,
            node_id="agent-node",
            trigger=EscalationTrigger.CONFIDENCE_BELOW_THRESHOLD,
            reason="low confidence",
            work_item_state={"input": "test"},
            emitter=emitter,
            process_id="esc-proc",
        )
        await emitter.close()

        escalated_events = [
            e for e in sink.events if e.event == "roots.run.escalated"
        ]
        assert len(escalated_events) == 1
        assert escalated_events[0].metadata["trigger_type"] == "confidence_below_threshold"
        assert escalated_events[0].metadata["reason"] == "low confidence"


# --- Integration Tests: Schema Validation Failure ---


class TestSchemaValidationEscalation:
    @pytest.mark.asyncio
    async def test_schema_failure_triggers_escalation(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Schema validation failure on agent output triggers escalation."""
        process = make_agent_process("schema_fail")
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.PAUSED

    @pytest.mark.asyncio
    async def test_schema_failure_creates_escalation_record(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        process = make_agent_process("schema_fail")
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()

        esc = await sqlite_storage.get_pending_escalation(run_id)
        assert esc is not None
        assert esc.trigger_type == "schema_validation_failure"
        assert "required property" in esc.reason

    @pytest.mark.asyncio
    async def test_schema_failure_emits_escalated_event(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        process = make_agent_process("schema_fail")
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()
        await emitter.close()

        escalated = [e for e in sink.events if e.event == "roots.run.escalated"]
        assert len(escalated) == 1
        assert escalated[0].metadata["trigger_type"] == "schema_validation_failure"

    @pytest.mark.asyncio
    async def test_schema_failure_includes_work_item_state(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        process = make_agent_process("schema_fail")
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()

        esc = await sqlite_storage.get_pending_escalation(run_id)
        assert esc is not None
        assert esc.work_item_snapshot == {"input": "test"}

    @pytest.mark.asyncio
    async def test_schema_failure_in_agent_pool(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Schema validation failure in agent pool triggers escalation."""
        process = make_pool_process("schema_fail")
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.PAUSED

        esc = await sqlite_storage.get_pending_escalation(run_id)
        assert esc is not None
        assert esc.trigger_type == "schema_validation_failure"


# --- Integration Tests: Agent Explicit Signal ---


class TestAgentExplicitSignalEscalation:
    @pytest.mark.asyncio
    async def test_escalate_true_triggers_escalation(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        """Agent returning escalate: true triggers escalation."""
        process = make_agent_process("escalating")
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.PAUSED

    @pytest.mark.asyncio
    async def test_escalate_creates_record_with_explicit_signal(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        process = make_agent_process("escalating")
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()

        esc = await sqlite_storage.get_pending_escalation(run_id)
        assert esc is not None
        assert esc.trigger_type == "agent_explicit_signal"
        assert esc.reason == "Agent wants human review"

    @pytest.mark.asyncio
    async def test_escalate_emits_event(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        process = make_agent_process("escalating")
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()
        await emitter.close()

        escalated = [e for e in sink.events if e.event == "roots.run.escalated"]
        assert len(escalated) == 1
        assert escalated[0].metadata["trigger_type"] == "agent_explicit_signal"
        assert "Agent wants human review" in escalated[0].metadata["reason"]

    @pytest.mark.asyncio
    async def test_escalate_includes_work_item_snapshot(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        process = make_agent_process("escalating")
        run_id = await _setup_run(sqlite_storage, process)
        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)

        await runner.run_to_completion()

        esc = await sqlite_storage.get_pending_escalation(run_id)
        assert esc is not None
        assert "input" in esc.work_item_snapshot


# --- Integration Tests: Confidence Below Threshold ---


class TestConfidenceBelowThresholdEscalation:
    @pytest.mark.asyncio
    async def test_low_confidence_triggers_escalation(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        """AI confidence below threshold triggers escalation with correct trigger type."""
        process = make_decision_process()
        run_id = await _setup_run(sqlite_storage, process)

        # Mock the decision engine to return an escalated result
        decision_engine.evaluate = AsyncMock(
            return_value=DecisionResult(
                selected_edge="approve-end",
                mode="ai",
                confidence=0.5,
                escalated=True,
                reasoning="Not sure",
            )
        )

        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)
        await runner.run_to_completion()

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.PAUSED

    @pytest.mark.asyncio
    async def test_low_confidence_creates_escalation_record(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        process = make_decision_process()
        run_id = await _setup_run(sqlite_storage, process)

        decision_engine.evaluate = AsyncMock(
            return_value=DecisionResult(
                selected_edge="approve-end",
                mode="ai",
                confidence=0.5,
                escalated=True,
                reasoning="Not sure",
            )
        )

        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)
        await runner.run_to_completion()

        esc = await sqlite_storage.get_pending_escalation(run_id)
        assert esc is not None
        assert esc.trigger_type == "confidence_below_threshold"

    @pytest.mark.asyncio
    async def test_low_confidence_emits_escalated_event(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        process = make_decision_process()
        run_id = await _setup_run(sqlite_storage, process)

        decision_engine.evaluate = AsyncMock(
            return_value=DecisionResult(
                selected_edge="approve-end",
                mode="ai",
                confidence=0.5,
                escalated=True,
                reasoning="Not sure",
            )
        )

        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)
        await runner.run_to_completion()
        await emitter.close()

        escalated = [e for e in sink.events if e.event == "roots.run.escalated"]
        assert len(escalated) == 1
        assert escalated[0].metadata["trigger_type"] == "confidence_below_threshold"

    @pytest.mark.asyncio
    async def test_low_confidence_includes_work_item_state(
        self,
        sqlite_storage: StorageBackend,
        invoker: AgentInvoker,
        decision_engine: DecisionEngine,
        emitter: EventEmitter,
    ) -> None:
        process = make_decision_process()
        run_id = await _setup_run(sqlite_storage, process)

        decision_engine.evaluate = AsyncMock(
            return_value=DecisionResult(
                selected_edge="approve-end",
                mode="ai",
                confidence=0.5,
                escalated=True,
                reasoning="Not sure",
            )
        )

        runner = _make_runner(run_id, sqlite_storage, invoker, decision_engine, emitter)
        await runner.run_to_completion()

        esc = await sqlite_storage.get_pending_escalation(run_id)
        assert esc is not None
        assert "input" in esc.work_item_snapshot
