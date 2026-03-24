"""Escalation trigger logic for Roots orchestration."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from roots.events.emitter import EventEmitter
from roots.events.types import EventType, create_event
from roots.storage.base import StorageBackend


class EscalationTrigger(StrEnum):
    SCHEMA_VALIDATION_FAILURE = "schema_validation_failure"
    CONFIDENCE_BELOW_THRESHOLD = "confidence_below_threshold"
    AGENT_EXPLICIT_SIGNAL = "agent_explicit_signal"


async def create_escalation_from_error(
    storage: StorageBackend,
    run_id: str,
    node_id: str,
    trigger: EscalationTrigger,
    reason: str,
    work_item_state: dict[str, Any],
    emitter: EventEmitter,
    process_id: str = "",
) -> str:
    """Create an escalation record, pause the run, and emit an event.

    Returns the escalation record ID.
    """
    from roots.core.state_machine import RunStatus

    escalation_id = await storage.create_escalation(
        run_id=run_id,
        node_id=node_id,
        trigger_type=trigger.value,
        reason=reason,
        work_item_snapshot=work_item_state,
    )

    await storage.update_run_status(run_id, RunStatus.PAUSED)

    emitter.emit(
        create_event(
            EventType.RUN_ESCALATED,
            run_id=run_id,
            process_id=process_id,
            node_id=node_id,
            metadata={
                "trigger_type": trigger.value,
                "reason": reason,
            },
        )
    )

    return escalation_id
