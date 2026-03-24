"""Checkpoint and escalation resolution logic for Roots orchestration."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from roots.core.schema import ProcessDefinition
from roots.core.state_machine import RunStatus
from roots.events.emitter import EventEmitter
from roots.events.types import EventType, create_event
from roots.storage.base import StorageBackend


class ResolutionDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REDIRECT = "redirect"


class ResolutionError(Exception):
    """Raised when a resolution operation fails."""


async def resolve_pending(
    storage: StorageBackend,
    run_id: str,
    decision: ResolutionDecision,
    process: ProcessDefinition,
    emitter: EventEmitter,
    notes: str | None = None,
    redirect_to: str | None = None,
) -> str:
    """Resolve a pending checkpoint or escalation for a run.

    Checks for a pending checkpoint first, then a pending escalation.
    Returns the next node ID the run will resume from.

    Raises:
        ResolutionError: If no pending checkpoint/escalation exists,
            or if required parameters are missing.
    """
    checkpoint = await storage.get_pending_checkpoint(run_id)
    escalation = await storage.get_pending_escalation(run_id)

    if checkpoint is not None:
        # If checkpoint is an escalation-type and there's a matching
        # escalation, resolve both together via the escalation path.
        if checkpoint.checkpoint_type == "escalation" and escalation is not None:
            return await _resolve_escalation(
                storage, run_id, escalation, decision, process, emitter,
                notes, redirect_to,
            )
        return await _resolve_checkpoint(
            storage, run_id, checkpoint, decision, process, emitter,
            notes, redirect_to,
        )
    elif escalation is not None:
        return await _resolve_escalation(
            storage, run_id, escalation, decision, process, emitter,
            notes, redirect_to,
        )
    else:
        raise ResolutionError(
            f"No pending checkpoint or escalation found for run '{run_id}'"
        )


async def _resolve_checkpoint(
    storage: StorageBackend,
    run_id: str,
    checkpoint: Any,
    decision: ResolutionDecision,
    process: ProcessDefinition,
    emitter: EventEmitter,
    notes: str | None,
    redirect_to: str | None,
) -> str:
    """Resolve a pending planned checkpoint."""
    if decision == ResolutionDecision.APPROVE:
        edges = process.get_outbound_edges(checkpoint.node_id)
        if not edges:
            raise ResolutionError(
                f"Checkpoint node '{checkpoint.node_id}' has no outbound edges"
            )
        first_edge = edges[0]
        next_node = getattr(first_edge, "to_node", None) or getattr(
            first_edge, "target", None
        )
    elif decision == ResolutionDecision.REJECT:
        next_node = None
    elif decision == ResolutionDecision.REDIRECT:
        if redirect_to is None:
            raise ResolutionError(
                "redirect_to is required for redirect decision"
            )
        if process.get_node(redirect_to) is None:
            raise ResolutionError(
                f"Redirect target '{redirect_to}' does not exist in process"
            )
        next_node = redirect_to
    else:
        raise ResolutionError(f"Unknown decision: {decision}")

    resolution = {
        "decision": decision.value,
        "notes": notes,
        "next_node": next_node,
    }
    await storage.resolve_checkpoint(checkpoint.id, resolution)

    if decision == ResolutionDecision.REJECT:
        await storage.update_run_status(run_id, RunStatus.FAILED)
    else:
        await storage.update_run_status(
            run_id, RunStatus.RUNNING, next_node
        )

    emitter.emit(
        create_event(
            EventType.CHECKPOINT_RESOLVED,
            run_id=run_id,
            process_id=process.id,
            node_id=checkpoint.node_id,
            metadata={
                "decision": decision.value,
                "next_node": next_node,
                "notes": notes,
            },
        )
    )

    return next_node or checkpoint.node_id


async def _resolve_escalation(
    storage: StorageBackend,
    run_id: str,
    escalation: Any,
    decision: ResolutionDecision,
    process: ProcessDefinition,
    emitter: EventEmitter,
    notes: str | None,
    redirect_to: str | None,
) -> str:
    """Resolve a pending escalation."""
    next_node: str | None = None

    if decision == ResolutionDecision.APPROVE:
        # Check for a checkpoint with ai_recommendation (confidence escalations)
        checkpoint = await storage.get_pending_checkpoint(run_id)
        if checkpoint and checkpoint.ai_recommendation:
            candidate = checkpoint.ai_recommendation.get(
                "selected_edge_target"
            )
            if candidate and process.get_node(candidate) is not None:
                next_node = candidate
            # Also resolve the associated checkpoint
            await storage.resolve_checkpoint(
                checkpoint.id,
                {
                    "decision": decision.value,
                    "notes": notes,
                    "next_node": next_node,
                },
            )
        if next_node is None:
            # No AI recommendation — redirect_to is required
            if redirect_to is None:
                raise ResolutionError(
                    "redirect_to is required when approving an "
                    "escalation with no AI recommendation"
                )
            next_node = redirect_to
    elif decision == ResolutionDecision.REJECT:
        next_node = None
    elif decision == ResolutionDecision.REDIRECT:
        if redirect_to is None:
            raise ResolutionError(
                "redirect_to is required for redirect decision"
            )
        if process.get_node(redirect_to) is None:
            raise ResolutionError(
                f"Redirect target '{redirect_to}' does not exist in process"
            )
        next_node = redirect_to

    resolution = {
        "decision": decision.value,
        "notes": notes,
        "next_node": next_node,
    }
    await storage.resolve_escalation(escalation.id, resolution)

    if decision == ResolutionDecision.REJECT:
        await storage.update_run_status(run_id, RunStatus.FAILED)
    else:
        assert next_node is not None
        if process.get_node(next_node) is None:
            raise ResolutionError(
                f"Redirect target '{next_node}' does not exist in process"
            )
        await storage.update_run_status(
            run_id, RunStatus.RUNNING, next_node
        )

    emitter.emit(
        create_event(
            EventType.ESCALATION_RESOLVED,
            run_id=run_id,
            process_id=process.id,
            node_id=escalation.node_id,
            metadata={
                "decision": decision.value,
                "next_node": next_node,
                "notes": notes,
            },
        )
    )

    return next_node or escalation.node_id
