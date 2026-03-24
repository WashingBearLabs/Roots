"""Checkpoint and escalation resolution routes."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, status

from roots import Roots
from roots.api.deps import get_roots
from roots.api.models import CheckpointResolveRequest, CheckpointResponse
from roots.core.orchestrator import OrchestrationError

router = APIRouter(prefix="/runs", tags=["checkpoints"])


@router.get("/{run_id}/checkpoint", response_model=CheckpointResponse)
async def get_checkpoint(
    run_id: str,
    roots: Roots = Depends(get_roots),
) -> CheckpointResponse:
    """Get the pending checkpoint or escalation for a run."""
    run = await roots.storage.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found",
        )

    checkpoint = await roots.storage.get_pending_checkpoint(run_id)
    if checkpoint is not None:
        return CheckpointResponse(
            id=checkpoint.id,
            run_id=checkpoint.run_id,
            node_id=checkpoint.node_id,
            type=checkpoint.checkpoint_type,
            prompt=checkpoint.prompt,
            ai_recommendation=checkpoint.ai_recommendation,
            status=checkpoint.status,
        )

    escalation = await roots.storage.get_pending_escalation(run_id)
    if escalation is not None:
        return CheckpointResponse(
            id=escalation.id,
            run_id=escalation.run_id,
            node_id=escalation.node_id,
            type=escalation.trigger_type,
            prompt=escalation.reason,
            ai_recommendation=None,
            status=escalation.status,
        )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"No pending checkpoint or escalation for run '{run_id}'",
    )


@router.post("/{run_id}/checkpoint", response_model=CheckpointResponse)
async def resolve_checkpoint(
    run_id: str,
    body: CheckpointResolveRequest,
    request: Request,
    roots: Roots = Depends(get_roots),
) -> CheckpointResponse:
    """Resolve a pending checkpoint or escalation."""
    run = await roots.storage.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found",
        )

    if body.decision not in ("approve", "reject", "redirect"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid decision '{body.decision}'. Must be 'approve', 'reject', or 'redirect'.",
        )

    if body.decision == "redirect" and body.redirect_to is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="redirect_to is required when decision is 'redirect'",
        )

    if body.decision == "redirect" and body.redirect_to is not None:
        process = await roots.storage.get_process(run.process_id)
        if process is not None:
            target_node = process.get_node(body.redirect_to)
            if target_node is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Redirect target '{body.redirect_to}' is not a valid node",
                )

    # Get the current pending item for the response before resolving
    checkpoint = await roots.storage.get_pending_checkpoint(run_id)
    escalation = await roots.storage.get_pending_escalation(run_id)

    if checkpoint is None and escalation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No pending checkpoint or escalation for run '{run_id}'",
        )

    # Build response from whichever is pending
    if checkpoint is not None:
        response = CheckpointResponse(
            id=checkpoint.id,
            run_id=checkpoint.run_id,
            node_id=checkpoint.node_id,
            type=checkpoint.checkpoint_type,
            prompt=checkpoint.prompt,
            ai_recommendation=checkpoint.ai_recommendation,
            status="resolved",
        )
    else:
        assert escalation is not None
        response = CheckpointResponse(
            id=escalation.id,
            run_id=escalation.run_id,
            node_id=escalation.node_id,
            type=escalation.trigger_type,
            prompt=escalation.reason,
            ai_recommendation=None,
            status="resolved",
        )

    try:
        await roots.resolve_checkpoint(
            run_id,
            body.decision,
            notes=body.notes,
            redirect_to=body.redirect_to,
        )
    except OrchestrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    # If the run was resumed (approve or redirect), start background execution
    if body.decision in ("approve", "redirect"):
        updated_run = await roots.storage.get_run(run_id)
        if updated_run and updated_run.status == "running":
            task = asyncio.create_task(roots.execute_run(run_id))
            if not hasattr(request.app.state, "_background_tasks"):
                request.app.state._background_tasks = set()
            request.app.state._background_tasks.add(task)
            task.add_done_callback(request.app.state._background_tasks.discard)

    return response
