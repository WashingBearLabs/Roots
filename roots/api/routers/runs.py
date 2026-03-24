"""Run management routes."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from roots import Roots
from roots.api.deps import get_roots
from roots.api.models import RunCreateRequest, RunResponse
from roots.core.state_machine import InvalidTransitionError, RunStatus, transition

router = APIRouter(prefix="/runs", tags=["runs"])


def _run_to_response(run: Any) -> RunResponse:
    return RunResponse(
        id=run.id,
        process_id=run.process_id,
        status=run.status,
        current_node_id=run.current_node_id,
        work_item_state=run.work_item_state,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


@router.post(
    "",
    response_model=RunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_run(
    body: RunCreateRequest,
    request: Request,
    roots: Roots = Depends(get_roots),
) -> RunResponse:
    """Create a new run and start background execution."""
    run = await roots.start_run(body.process_id, body.work_item)

    task = asyncio.create_task(roots.execute_run(run.id))

    if not hasattr(request.app.state, "_background_tasks"):
        request.app.state._background_tasks = set()
    request.app.state._background_tasks.add(task)
    task.add_done_callback(request.app.state._background_tasks.discard)

    return _run_to_response(run)


@router.get("", response_model=list[RunResponse])
async def list_runs(
    process_id: str | None = None,
    run_status: str | None = None,
    roots: Roots = Depends(get_roots),
) -> list[RunResponse]:
    """List runs with optional filters."""
    runs = await roots.storage.list_runs(
        process_id=process_id, status=run_status
    )
    return [_run_to_response(r) for r in runs]


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: str,
    roots: Roots = Depends(get_roots),
) -> RunResponse:
    """Get a run by ID."""
    run = await roots.storage.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found",
        )
    return _run_to_response(run)


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_run(
    run_id: str,
    roots: Roots = Depends(get_roots),
) -> None:
    """Cancel a run."""
    run = await roots.storage.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found",
        )

    try:
        transition(RunStatus(run.status), RunStatus.CANCELLED)
    except InvalidTransitionError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel run in '{run.status}' state",
        )

    await roots.storage.update_run_status(run_id, RunStatus.CANCELLED)
