"""Run management routes."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from roots import Roots

logger = logging.getLogger(__name__)
from roots.api.deps import get_roots
from roots.api.models import HistoryEventResponse, RunCreateRequest, RunResponse
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
        process_version=run.process_version,
        metadata=run.metadata,
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
    run = await roots.start_run(body.process_id, body.work_item, metadata=body.metadata)

    task = asyncio.create_task(roots.execute_run(run.id))

    def _on_task_done(t: asyncio.Task[None]) -> None:
        if not t.cancelled() and t.exception():
            logger.error("Background run execution failed: %s", t.exception())

    task.add_done_callback(_on_task_done)

    if not hasattr(request.app.state, "_background_tasks"):
        request.app.state._background_tasks = set()
    request.app.state._background_tasks.add(task)
    task.add_done_callback(request.app.state._background_tasks.discard)

    return _run_to_response(run)


@router.get("", response_model=list[RunResponse])
async def list_runs(
    process_id: str | None = None,
    run_status: str | None = None,
    metadata_filter: str | None = None,
    roots: Roots = Depends(get_roots),
) -> list[RunResponse]:
    """List runs with optional filters."""
    parsed_filter: dict[str, Any] | None = None
    if metadata_filter is not None:
        try:
            raw = json.loads(metadata_filter)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid JSON in metadata_filter: {exc}",
            )
        if not isinstance(raw, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="metadata_filter must be a JSON object",
            )
        parsed_filter = raw
    runs = await roots.storage.list_runs(
        process_id=process_id, status=run_status, metadata_filter=parsed_filter
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


@router.post("/{run_id}/pause", response_model=RunResponse)
async def pause_run(
    run_id: str,
    roots: Roots = Depends(get_roots),
) -> RunResponse:
    """Pause a running run."""
    run = await roots.storage.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found",
        )

    try:
        transition(RunStatus(run.status), RunStatus.PAUSED)
    except InvalidTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot transition from {exc.current} to {exc.target}. "
                f"Valid targets: {[str(t) for t in exc.valid_targets]}"
            ),
        )

    await roots.storage.update_run_status(run_id, RunStatus.PAUSED)
    updated = await roots.storage.get_run(run_id)
    return _run_to_response(updated)


@router.post("/{run_id}/resume", response_model=RunResponse)
async def resume_run(
    run_id: str,
    request: Request,
    roots: Roots = Depends(get_roots),
) -> RunResponse:
    """Resume a paused run and restart background execution."""
    run = await roots.storage.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found",
        )

    try:
        transition(RunStatus(run.status), RunStatus.RUNNING)
    except InvalidTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot transition from {exc.current} to {exc.target}. "
                f"Valid targets: {[str(t) for t in exc.valid_targets]}"
            ),
        )

    await roots.storage.update_run_status(run_id, RunStatus.RUNNING)

    task = asyncio.create_task(roots.execute_run(run_id))

    def _on_task_done(t: asyncio.Task[None]) -> None:
        if not t.cancelled() and t.exception():
            logger.error("Background run execution failed: %s", t.exception())

    task.add_done_callback(_on_task_done)

    if not hasattr(request.app.state, "_background_tasks"):
        request.app.state._background_tasks = set()
    request.app.state._background_tasks.add(task)
    task.add_done_callback(request.app.state._background_tasks.discard)

    updated = await roots.storage.get_run(run_id)
    return _run_to_response(updated)


@router.get("/{run_id}/history", response_model=list[HistoryEventResponse])
async def get_run_history(
    run_id: str,
    roots: Roots = Depends(get_roots),
) -> list[HistoryEventResponse]:
    """Get history events for a run, ordered by timestamp."""
    run = await roots.storage.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found",
        )

    events = await roots.storage.list_history_events(run_id)
    return [
        HistoryEventResponse(
            event_type=e.event_type,
            node_id=e.node_id,
            data=e.data,
            created_at=e.created_at,
        )
        for e in events
    ]
