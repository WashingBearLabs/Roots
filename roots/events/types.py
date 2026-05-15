"""Event type catalog and envelope model for Roots lifecycle events."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EventType(StrEnum):
    # Run lifecycle
    RUN_STARTED = "roots.run.started"
    RUN_COMPLETED = "roots.run.completed"
    RUN_FAILED = "roots.run.failed"
    RUN_PAUSED = "roots.run.paused"
    RUN_ESCALATED = "roots.run.escalated"

    # Node lifecycle
    NODE_ENTERED = "roots.node.entered"
    NODE_COMPLETED = "roots.node.completed"
    NODE_FAILED = "roots.node.failed"
    NODE_RETRYING = "roots.node.retrying"

    # Agent lifecycle
    AGENT_INVOKED = "roots.agent.invoked"
    AGENT_RETURNED = "roots.agent.returned"
    AGENT_FAILED = "roots.agent.failed"

    # Decision lifecycle
    DECISION_EVALUATED = "roots.decision.evaluated"
    DECISION_TAKEN = "roots.decision.taken"
    DECISION_ESCALATED = "roots.decision.escalated"

    # Checkpoint lifecycle
    CHECKPOINT_REACHED = "roots.checkpoint.reached"
    CHECKPOINT_RESOLVED = "roots.checkpoint.resolved"

    # Escalation lifecycle
    ESCALATION_TRIGGERED = "roots.escalation.triggered"
    ESCALATION_RESOLVED = "roots.escalation.resolved"

    # Subprocess lifecycle
    SUBPROCESS_STARTED = "roots.subprocess.started"
    SUBPROCESS_COMPLETED = "roots.subprocess.completed"
    SUBPROCESS_FAILED = "roots.subprocess.failed"


class EventEnvelope(BaseModel):
    event: str
    timestamp: datetime
    run_id: str
    process_id: str
    node_id: str | None = None
    node_type: str | None = None
    work_item_id: str | None = None
    duration_ms: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def create_event(
    event_type: EventType,
    run_id: str,
    process_id: str,
    **kwargs: Any,
) -> EventEnvelope:
    """Create an EventEnvelope with auto-generated timestamp.

    Args:
        event_type: The event type from the EventType enum.
        run_id: The run identifier.
        process_id: The process identifier.
        **kwargs: Optional fields (node_id, node_type, work_item_id,
                  duration_ms, metadata).

    Returns:
        A fully populated EventEnvelope.
    """
    return EventEnvelope(
        event=event_type,
        timestamp=datetime.now(timezone.utc),
        run_id=run_id,
        process_id=process_id,
        **kwargs,
    )
