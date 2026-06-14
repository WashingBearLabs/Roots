"""Tests for event type catalog and envelope model (US-001)."""

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from roots.events.types import EventEnvelope, EventType, create_event


# --- EventType enum ---


class TestEventType:
    def test_all_event_types_defined(self) -> None:
        # 19 base + 5 iterator + 3 subprocess lifecycle events
        assert len(EventType) == 27

    def test_run_events(self) -> None:
        assert EventType.RUN_STARTED == "roots.run.started"
        assert EventType.RUN_COMPLETED == "roots.run.completed"
        assert EventType.RUN_FAILED == "roots.run.failed"
        assert EventType.RUN_PAUSED == "roots.run.paused"
        assert EventType.RUN_ESCALATED == "roots.run.escalated"

    def test_node_events(self) -> None:
        assert EventType.NODE_ENTERED == "roots.node.entered"
        assert EventType.NODE_COMPLETED == "roots.node.completed"
        assert EventType.NODE_FAILED == "roots.node.failed"
        assert EventType.NODE_RETRYING == "roots.node.retrying"

    def test_agent_events(self) -> None:
        assert EventType.AGENT_INVOKED == "roots.agent.invoked"
        assert EventType.AGENT_RETURNED == "roots.agent.returned"
        assert EventType.AGENT_FAILED == "roots.agent.failed"

    def test_decision_events(self) -> None:
        assert EventType.DECISION_EVALUATED == "roots.decision.evaluated"
        assert EventType.DECISION_TAKEN == "roots.decision.taken"
        assert EventType.DECISION_ESCALATED == "roots.decision.escalated"

    def test_checkpoint_events(self) -> None:
        assert EventType.CHECKPOINT_REACHED == "roots.checkpoint.reached"
        assert EventType.CHECKPOINT_RESOLVED == "roots.checkpoint.resolved"

    def test_escalation_events(self) -> None:
        assert EventType.ESCALATION_TRIGGERED == "roots.escalation.triggered"
        assert EventType.ESCALATION_RESOLVED == "roots.escalation.resolved"

    def test_subprocess_events(self) -> None:
        assert EventType.SUBPROCESS_STARTED == "roots.subprocess.started"
        assert EventType.SUBPROCESS_COMPLETED == "roots.subprocess.completed"
        assert EventType.SUBPROCESS_FAILED == "roots.subprocess.failed"

    def test_event_type_is_str_enum(self) -> None:
        assert isinstance(EventType.RUN_STARTED, str)


# --- EventEnvelope model ---


class TestEventEnvelope:
    def test_required_fields(self) -> None:
        ts = datetime.now(timezone.utc)
        envelope = EventEnvelope(
            event="roots.run.started",
            timestamp=ts,
            run_id="run-1",
            process_id="proc-1",
        )
        assert envelope.event == "roots.run.started"
        assert envelope.timestamp == ts
        assert envelope.run_id == "run-1"
        assert envelope.process_id == "proc-1"

    def test_optional_fields_default_none(self) -> None:
        envelope = EventEnvelope(
            event="roots.run.started",
            timestamp=datetime.now(timezone.utc),
            run_id="run-1",
            process_id="proc-1",
        )
        assert envelope.node_id is None
        assert envelope.node_type is None
        assert envelope.work_item_id is None
        assert envelope.duration_ms is None
        assert envelope.metadata == {}

    def test_all_optional_fields_set(self) -> None:
        envelope = EventEnvelope(
            event="roots.node.completed",
            timestamp=datetime.now(timezone.utc),
            run_id="run-1",
            process_id="proc-1",
            node_id="node-1",
            node_type="agent",
            work_item_id="wi-1",
            duration_ms=150,
            metadata={"key": "value"},
        )
        assert envelope.node_id == "node-1"
        assert envelope.node_type == "agent"
        assert envelope.work_item_id == "wi-1"
        assert envelope.duration_ms == 150
        assert envelope.metadata == {"key": "value"}

    def test_missing_required_field_raises(self) -> None:
        with pytest.raises(ValidationError):
            EventEnvelope(
                event="roots.run.started",
                timestamp=datetime.now(timezone.utc),
                run_id="run-1",
                # missing process_id
            )

    def test_json_serialization_iso_datetime(self) -> None:
        ts = datetime(2026, 3, 23, 12, 0, 0, tzinfo=timezone.utc)
        envelope = EventEnvelope(
            event="roots.run.started",
            timestamp=ts,
            run_id="run-1",
            process_id="proc-1",
        )
        data = json.loads(envelope.model_dump_json())
        assert data["timestamp"] == "2026-03-23T12:00:00Z"
        assert isinstance(data["event"], str)
        assert isinstance(data["run_id"], str)

    def test_json_round_trip(self) -> None:
        ts = datetime(2026, 3, 23, 12, 0, 0, tzinfo=timezone.utc)
        envelope = EventEnvelope(
            event="roots.node.entered",
            timestamp=ts,
            run_id="run-1",
            process_id="proc-1",
            node_id="n-1",
            metadata={"foo": "bar"},
        )
        json_str = envelope.model_dump_json()
        restored = EventEnvelope.model_validate_json(json_str)
        assert restored == envelope


# --- create_event factory ---


class TestCreateEvent:
    def test_creates_valid_envelope(self) -> None:
        envelope = create_event(
            EventType.RUN_STARTED,
            run_id="run-1",
            process_id="proc-1",
        )
        assert isinstance(envelope, EventEnvelope)
        assert envelope.event == "roots.run.started"
        assert envelope.run_id == "run-1"
        assert envelope.process_id == "proc-1"

    def test_auto_timestamp(self) -> None:
        before = datetime.now(timezone.utc)
        envelope = create_event(
            EventType.RUN_STARTED,
            run_id="run-1",
            process_id="proc-1",
        )
        after = datetime.now(timezone.utc)
        assert before <= envelope.timestamp <= after

    def test_passes_optional_fields(self) -> None:
        envelope = create_event(
            EventType.NODE_COMPLETED,
            run_id="run-1",
            process_id="proc-1",
            node_id="node-1",
            node_type="agent",
            duration_ms=200,
            metadata={"result": "ok"},
        )
        assert envelope.node_id == "node-1"
        assert envelope.node_type == "agent"
        assert envelope.duration_ms == 200
        assert envelope.metadata == {"result": "ok"}

    def test_run_category(self) -> None:
        for et in [EventType.RUN_STARTED, EventType.RUN_COMPLETED,
                    EventType.RUN_FAILED, EventType.RUN_PAUSED,
                    EventType.RUN_ESCALATED]:
            envelope = create_event(et, run_id="r", process_id="p")
            assert envelope.event.startswith("roots.run.")

    def test_node_category(self) -> None:
        for et in [EventType.NODE_ENTERED, EventType.NODE_COMPLETED,
                    EventType.NODE_FAILED, EventType.NODE_RETRYING]:
            envelope = create_event(et, run_id="r", process_id="p")
            assert envelope.event.startswith("roots.node.")

    def test_agent_category(self) -> None:
        for et in [EventType.AGENT_INVOKED, EventType.AGENT_RETURNED,
                    EventType.AGENT_FAILED]:
            envelope = create_event(et, run_id="r", process_id="p")
            assert envelope.event.startswith("roots.agent.")

    def test_decision_category(self) -> None:
        for et in [EventType.DECISION_EVALUATED, EventType.DECISION_TAKEN,
                    EventType.DECISION_ESCALATED]:
            envelope = create_event(et, run_id="r", process_id="p")
            assert envelope.event.startswith("roots.decision.")

    def test_checkpoint_category(self) -> None:
        for et in [EventType.CHECKPOINT_REACHED, EventType.CHECKPOINT_RESOLVED]:
            envelope = create_event(et, run_id="r", process_id="p")
            assert envelope.event.startswith("roots.checkpoint.")

    def test_escalation_category(self) -> None:
        for et in [EventType.ESCALATION_TRIGGERED, EventType.ESCALATION_RESOLVED]:
            envelope = create_event(et, run_id="r", process_id="p")
            assert envelope.event.startswith("roots.escalation.")

    def test_factory_output_serializes_to_json(self) -> None:
        envelope = create_event(
            EventType.AGENT_INVOKED,
            run_id="run-1",
            process_id="proc-1",
            node_id="agent-node",
        )
        data = json.loads(envelope.model_dump_json())
        assert "timestamp" in data
        # Verify timestamp is ISO format string
        datetime.fromisoformat(data["timestamp"])
