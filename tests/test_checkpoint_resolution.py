"""Tests for checkpoint and escalation resolution (US-005).

Verifies:
- `approve` resumes run from the expected next node
- `reject` transitions run to `failed`
- `redirect` resumes run from the specified node
- Resolution updates the checkpoint/escalation record
- Appropriate resolved event is emitted
- Invalid redirect target raises error
- Tests cover approve, reject, and redirect paths for both checkpoints and escalations
"""

from __future__ import annotations

import pytest

from roots.core.checkpoint import (
    ResolutionDecision,
    ResolutionError,
    resolve_pending,
)
from roots.core.escalation import EscalationTrigger, create_escalation_from_error
from roots.core.schema import (
    AgentNodeConfig,
    CheckpointNodeConfig,
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
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def emit(self, event: EventEnvelope) -> None:
        self.events.append(event)


def make_checkpoint_process() -> ProcessDefinition:
    """Process with: agent -> checkpoint -> end."""
    return ProcessDefinition(
        id="cp-proc",
        name="Checkpoint Process",
        version="1.0.0",
        nodes=[
            NodeDefinition(
                id="agent-node",
                type=NodeType.AGENT,
                label="Agent",
                config=AgentNodeConfig(agent="echo", output_key="result"),
            ),
            NodeDefinition(
                id="cp-node",
                type=NodeType.CHECKPOINT,
                label="Review",
                config=CheckpointNodeConfig(prompt="Review this"),
            ),
            NodeDefinition(
                id="done",
                type=NodeType.END,
                label="Done",
                config=EndNodeConfig(status=EndStatus.COMPLETED),
            ),
            NodeDefinition(
                id="alt-node",
                type=NodeType.AGENT,
                label="Alt Agent",
                config=AgentNodeConfig(agent="echo", output_key="alt"),
            ),
        ],
        edges=[
            EdgeDefinition(from_node="agent-node", to_node="cp-node"),
            EdgeDefinition(from_node="cp-node", to_node="done"),
            EdgeDefinition(from_node="alt-node", to_node="done"),
        ],
        entry_point="agent-node",
    )


def make_escalation_process() -> ProcessDefinition:
    """Process with decision node for escalation testing."""
    return ProcessDefinition(
        id="esc-proc",
        name="Escalation Process",
        version="1.0.0",
        nodes=[
            NodeDefinition(
                id="agent-node",
                type=NodeType.AGENT,
                label="Agent",
                config=AgentNodeConfig(agent="echo", output_key="result"),
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
            EdgeDefinition(from_node="agent-node", to_node="approve-end"),
        ],
        entry_point="agent-node",
    )


@pytest.fixture
async def sqlite_storage():
    from roots.storage.sqlite import SqliteBackend

    backend = SqliteBackend(":memory:")
    await backend.initialize()
    yield backend
    await backend.close()


@pytest.fixture
def sink() -> CollectorSink:
    return CollectorSink()


@pytest.fixture
def emitter(sink: CollectorSink) -> EventEmitter:
    return EventEmitter(sinks=[sink])


async def _setup_paused_checkpoint_run(
    storage: StorageBackend,
    process: ProcessDefinition,
) -> str:
    """Create a run paused at the checkpoint node."""
    await storage.save_process(process)
    run = await storage.create_run(process.id, {"input": "test"})
    # Simulate: run reached checkpoint and paused
    await storage.update_run_status(run.id, RunStatus.RUNNING, "cp-node")
    await storage.update_run_status(run.id, RunStatus.PAUSED, "cp-node")
    await storage.create_checkpoint(
        run.id, "cp-node", "planned", "Review this"
    )
    return run.id


async def _setup_paused_escalation_run(
    storage: StorageBackend,
    process: ProcessDefinition,
    emitter: EventEmitter,
    trigger: EscalationTrigger = EscalationTrigger.SCHEMA_VALIDATION_FAILURE,
    with_ai_recommendation: bool = False,
) -> str:
    """Create a run paused with an escalation record."""
    await storage.save_process(process)
    run = await storage.create_run(process.id, {"input": "test"})
    await storage.update_run_status(run.id, RunStatus.RUNNING, "agent-node")

    if with_ai_recommendation:
        # Create a checkpoint with AI recommendation (confidence escalation)
        await storage.create_checkpoint(
            run.id,
            "agent-node",
            "escalation",
            "AI confidence too low",
            ai_recommendation={"selected_edge_target": "approve-end"},
        )

    await create_escalation_from_error(
        storage=storage,
        run_id=run.id,
        node_id="agent-node",
        trigger=trigger,
        reason="test escalation",
        work_item_state={"input": "test"},
        emitter=emitter,
        process_id=process.id,
    )
    return run.id


# --- Planned Checkpoint Tests ---


class TestCheckpointApprove:
    @pytest.mark.asyncio
    async def test_approve_resumes_from_next_node(
        self,
        sqlite_storage: StorageBackend,
        emitter: EventEmitter,
    ) -> None:
        process = make_checkpoint_process()
        run_id = await _setup_paused_checkpoint_run(sqlite_storage, process)

        next_node = await resolve_pending(
            sqlite_storage, run_id, ResolutionDecision.APPROVE,
            process, emitter,
        )

        assert next_node == "done"
        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.RUNNING
        assert run.current_node_id == "done"

    @pytest.mark.asyncio
    async def test_approve_updates_checkpoint_record(
        self,
        sqlite_storage: StorageBackend,
        emitter: EventEmitter,
    ) -> None:
        process = make_checkpoint_process()
        run_id = await _setup_paused_checkpoint_run(sqlite_storage, process)

        await resolve_pending(
            sqlite_storage, run_id, ResolutionDecision.APPROVE,
            process, emitter,
        )

        # Checkpoint should be resolved (no longer pending)
        cp = await sqlite_storage.get_pending_checkpoint(run_id)
        assert cp is None

    @pytest.mark.asyncio
    async def test_approve_emits_checkpoint_resolved_event(
        self,
        sqlite_storage: StorageBackend,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        process = make_checkpoint_process()
        run_id = await _setup_paused_checkpoint_run(sqlite_storage, process)

        await resolve_pending(
            sqlite_storage, run_id, ResolutionDecision.APPROVE,
            process, emitter,
        )
        await emitter.close()

        resolved = [
            e for e in sink.events
            if e.event == "roots.checkpoint.resolved"
        ]
        assert len(resolved) == 1
        assert resolved[0].metadata["decision"] == "approve"
        assert resolved[0].metadata["next_node"] == "done"


class TestCheckpointReject:
    @pytest.mark.asyncio
    async def test_reject_fails_run(
        self,
        sqlite_storage: StorageBackend,
        emitter: EventEmitter,
    ) -> None:
        process = make_checkpoint_process()
        run_id = await _setup_paused_checkpoint_run(sqlite_storage, process)

        await resolve_pending(
            sqlite_storage, run_id, ResolutionDecision.REJECT,
            process, emitter,
        )

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.FAILED

    @pytest.mark.asyncio
    async def test_reject_updates_checkpoint_record(
        self,
        sqlite_storage: StorageBackend,
        emitter: EventEmitter,
    ) -> None:
        process = make_checkpoint_process()
        run_id = await _setup_paused_checkpoint_run(sqlite_storage, process)

        await resolve_pending(
            sqlite_storage, run_id, ResolutionDecision.REJECT,
            process, emitter,
        )

        cp = await sqlite_storage.get_pending_checkpoint(run_id)
        assert cp is None

    @pytest.mark.asyncio
    async def test_reject_emits_checkpoint_resolved_event(
        self,
        sqlite_storage: StorageBackend,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        process = make_checkpoint_process()
        run_id = await _setup_paused_checkpoint_run(sqlite_storage, process)

        await resolve_pending(
            sqlite_storage, run_id, ResolutionDecision.REJECT,
            process, emitter,
        )
        await emitter.close()

        resolved = [
            e for e in sink.events
            if e.event == "roots.checkpoint.resolved"
        ]
        assert len(resolved) == 1
        assert resolved[0].metadata["decision"] == "reject"


class TestCheckpointRedirect:
    @pytest.mark.asyncio
    async def test_redirect_resumes_from_specified_node(
        self,
        sqlite_storage: StorageBackend,
        emitter: EventEmitter,
    ) -> None:
        process = make_checkpoint_process()
        run_id = await _setup_paused_checkpoint_run(sqlite_storage, process)

        next_node = await resolve_pending(
            sqlite_storage, run_id, ResolutionDecision.REDIRECT,
            process, emitter, redirect_to="alt-node",
        )

        assert next_node == "alt-node"
        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.RUNNING
        assert run.current_node_id == "alt-node"

    @pytest.mark.asyncio
    async def test_redirect_invalid_target_raises_error(
        self,
        sqlite_storage: StorageBackend,
        emitter: EventEmitter,
    ) -> None:
        process = make_checkpoint_process()
        run_id = await _setup_paused_checkpoint_run(sqlite_storage, process)

        with pytest.raises(ResolutionError, match="does not exist"):
            await resolve_pending(
                sqlite_storage, run_id, ResolutionDecision.REDIRECT,
                process, emitter, redirect_to="nonexistent",
            )

    @pytest.mark.asyncio
    async def test_redirect_missing_target_raises_error(
        self,
        sqlite_storage: StorageBackend,
        emitter: EventEmitter,
    ) -> None:
        process = make_checkpoint_process()
        run_id = await _setup_paused_checkpoint_run(sqlite_storage, process)

        with pytest.raises(ResolutionError, match="redirect_to is required"):
            await resolve_pending(
                sqlite_storage, run_id, ResolutionDecision.REDIRECT,
                process, emitter,
            )


# --- Escalation Tests ---


class TestEscalationApprove:
    @pytest.mark.asyncio
    async def test_approve_with_ai_recommendation(
        self,
        sqlite_storage: StorageBackend,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        """Approve escalation with AI recommendation uses recommended edge."""
        process = make_escalation_process()
        run_id = await _setup_paused_escalation_run(
            sqlite_storage, process, emitter,
            trigger=EscalationTrigger.CONFIDENCE_BELOW_THRESHOLD,
            with_ai_recommendation=True,
        )
        sink.events.clear()

        next_node = await resolve_pending(
            sqlite_storage, run_id, ResolutionDecision.APPROVE,
            process, emitter,
        )

        assert next_node == "approve-end"
        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.RUNNING
        assert run.current_node_id == "approve-end"

    @pytest.mark.asyncio
    async def test_approve_without_recommendation_requires_redirect_to(
        self,
        sqlite_storage: StorageBackend,
        emitter: EventEmitter,
    ) -> None:
        """Approve escalation without AI recommendation requires redirect_to."""
        process = make_escalation_process()
        run_id = await _setup_paused_escalation_run(
            sqlite_storage, process, emitter,
        )

        with pytest.raises(ResolutionError, match="redirect_to is required"):
            await resolve_pending(
                sqlite_storage, run_id, ResolutionDecision.APPROVE,
                process, emitter,
            )

    @pytest.mark.asyncio
    async def test_approve_with_redirect_to_fallback(
        self,
        sqlite_storage: StorageBackend,
        emitter: EventEmitter,
    ) -> None:
        """Approve escalation without recommendation uses redirect_to."""
        process = make_escalation_process()
        run_id = await _setup_paused_escalation_run(
            sqlite_storage, process, emitter,
        )

        next_node = await resolve_pending(
            sqlite_storage, run_id, ResolutionDecision.APPROVE,
            process, emitter, redirect_to="approve-end",
        )

        assert next_node == "approve-end"
        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.RUNNING

    @pytest.mark.asyncio
    async def test_approve_updates_escalation_record(
        self,
        sqlite_storage: StorageBackend,
        emitter: EventEmitter,
    ) -> None:
        process = make_escalation_process()
        run_id = await _setup_paused_escalation_run(
            sqlite_storage, process, emitter,
            trigger=EscalationTrigger.CONFIDENCE_BELOW_THRESHOLD,
            with_ai_recommendation=True,
        )

        await resolve_pending(
            sqlite_storage, run_id, ResolutionDecision.APPROVE,
            process, emitter,
        )

        esc = await sqlite_storage.get_pending_escalation(run_id)
        assert esc is None

    @pytest.mark.asyncio
    async def test_approve_emits_escalation_resolved_event(
        self,
        sqlite_storage: StorageBackend,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        process = make_escalation_process()
        run_id = await _setup_paused_escalation_run(
            sqlite_storage, process, emitter,
            trigger=EscalationTrigger.CONFIDENCE_BELOW_THRESHOLD,
            with_ai_recommendation=True,
        )
        sink.events.clear()

        await resolve_pending(
            sqlite_storage, run_id, ResolutionDecision.APPROVE,
            process, emitter,
        )
        await emitter.close()

        resolved = [
            e for e in sink.events
            if e.event == "roots.escalation.resolved"
        ]
        assert len(resolved) == 1
        assert resolved[0].metadata["decision"] == "approve"
        assert resolved[0].metadata["next_node"] == "approve-end"


class TestEscalationReject:
    @pytest.mark.asyncio
    async def test_reject_fails_run(
        self,
        sqlite_storage: StorageBackend,
        emitter: EventEmitter,
    ) -> None:
        process = make_escalation_process()
        run_id = await _setup_paused_escalation_run(
            sqlite_storage, process, emitter,
        )

        await resolve_pending(
            sqlite_storage, run_id, ResolutionDecision.REJECT,
            process, emitter,
        )

        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.FAILED

    @pytest.mark.asyncio
    async def test_reject_updates_escalation_record(
        self,
        sqlite_storage: StorageBackend,
        emitter: EventEmitter,
    ) -> None:
        process = make_escalation_process()
        run_id = await _setup_paused_escalation_run(
            sqlite_storage, process, emitter,
        )

        await resolve_pending(
            sqlite_storage, run_id, ResolutionDecision.REJECT,
            process, emitter,
        )

        esc = await sqlite_storage.get_pending_escalation(run_id)
        assert esc is None

    @pytest.mark.asyncio
    async def test_reject_emits_escalation_resolved_event(
        self,
        sqlite_storage: StorageBackend,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        process = make_escalation_process()
        run_id = await _setup_paused_escalation_run(
            sqlite_storage, process, emitter,
        )
        sink.events.clear()

        await resolve_pending(
            sqlite_storage, run_id, ResolutionDecision.REJECT,
            process, emitter,
        )
        await emitter.close()

        resolved = [
            e for e in sink.events
            if e.event == "roots.escalation.resolved"
        ]
        assert len(resolved) == 1
        assert resolved[0].metadata["decision"] == "reject"


class TestEscalationRedirect:
    @pytest.mark.asyncio
    async def test_redirect_resumes_from_specified_node(
        self,
        sqlite_storage: StorageBackend,
        emitter: EventEmitter,
    ) -> None:
        process = make_escalation_process()
        run_id = await _setup_paused_escalation_run(
            sqlite_storage, process, emitter,
        )

        next_node = await resolve_pending(
            sqlite_storage, run_id, ResolutionDecision.REDIRECT,
            process, emitter, redirect_to="approve-end",
        )

        assert next_node == "approve-end"
        run = await sqlite_storage.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.RUNNING
        assert run.current_node_id == "approve-end"

    @pytest.mark.asyncio
    async def test_redirect_invalid_target_raises_error(
        self,
        sqlite_storage: StorageBackend,
        emitter: EventEmitter,
    ) -> None:
        process = make_escalation_process()
        run_id = await _setup_paused_escalation_run(
            sqlite_storage, process, emitter,
        )

        with pytest.raises(ResolutionError, match="does not exist"):
            await resolve_pending(
                sqlite_storage, run_id, ResolutionDecision.REDIRECT,
                process, emitter, redirect_to="nonexistent",
            )

    @pytest.mark.asyncio
    async def test_redirect_missing_target_raises_error(
        self,
        sqlite_storage: StorageBackend,
        emitter: EventEmitter,
    ) -> None:
        process = make_escalation_process()
        run_id = await _setup_paused_escalation_run(
            sqlite_storage, process, emitter,
        )

        with pytest.raises(ResolutionError, match="redirect_to is required"):
            await resolve_pending(
                sqlite_storage, run_id, ResolutionDecision.REDIRECT,
                process, emitter,
            )

    @pytest.mark.asyncio
    async def test_redirect_updates_escalation_record(
        self,
        sqlite_storage: StorageBackend,
        emitter: EventEmitter,
    ) -> None:
        process = make_escalation_process()
        run_id = await _setup_paused_escalation_run(
            sqlite_storage, process, emitter,
        )

        await resolve_pending(
            sqlite_storage, run_id, ResolutionDecision.REDIRECT,
            process, emitter, redirect_to="approve-end",
        )

        esc = await sqlite_storage.get_pending_escalation(run_id)
        assert esc is None


# --- Edge Cases ---


class TestResolutionEdgeCases:
    @pytest.mark.asyncio
    async def test_no_pending_raises_error(
        self,
        sqlite_storage: StorageBackend,
        emitter: EventEmitter,
    ) -> None:
        """Resolving when nothing is pending raises ResolutionError."""
        process = make_checkpoint_process()
        await sqlite_storage.save_process(process)
        run = await sqlite_storage.create_run(process.id, {"input": "test"})

        with pytest.raises(ResolutionError, match="No pending"):
            await resolve_pending(
                sqlite_storage, run.id, ResolutionDecision.APPROVE,
                process, emitter,
            )

    @pytest.mark.asyncio
    async def test_checkpoint_takes_priority_over_escalation(
        self,
        sqlite_storage: StorageBackend,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        """When both checkpoint and escalation exist, checkpoint is resolved."""
        process = make_checkpoint_process()
        await sqlite_storage.save_process(process)
        run = await sqlite_storage.create_run(process.id, {"input": "test"})
        await sqlite_storage.update_run_status(
            run.id, RunStatus.RUNNING, "cp-node"
        )
        await sqlite_storage.update_run_status(
            run.id, RunStatus.PAUSED, "cp-node"
        )
        # Create both
        await sqlite_storage.create_checkpoint(
            run.id, "cp-node", "planned", "Review this"
        )
        await create_escalation_from_error(
            storage=sqlite_storage,
            run_id=run.id,
            node_id="cp-node",
            trigger=EscalationTrigger.AGENT_EXPLICIT_SIGNAL,
            reason="also escalated",
            work_item_state={"input": "test"},
            emitter=emitter,
            process_id=process.id,
        )
        sink.events.clear()

        await resolve_pending(
            sqlite_storage, run.id, ResolutionDecision.APPROVE,
            process, emitter,
        )
        await emitter.close()

        # Should emit checkpoint.resolved, not escalation.resolved
        resolved = [
            e for e in sink.events
            if e.event == "roots.checkpoint.resolved"
        ]
        assert len(resolved) == 1

    @pytest.mark.asyncio
    async def test_approve_with_notes(
        self,
        sqlite_storage: StorageBackend,
        sink: CollectorSink,
        emitter: EventEmitter,
    ) -> None:
        """Notes are included in the resolution metadata."""
        process = make_checkpoint_process()
        run_id = await _setup_paused_checkpoint_run(sqlite_storage, process)

        await resolve_pending(
            sqlite_storage, run_id, ResolutionDecision.APPROVE,
            process, emitter, notes="Looks good",
        )
        await emitter.close()

        resolved = [
            e for e in sink.events
            if e.event == "roots.checkpoint.resolved"
        ]
        assert resolved[0].metadata["notes"] == "Looks good"
