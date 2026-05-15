"""Decision history routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from roots import Roots
from roots.api.deps import get_roots
from roots.api.models import DecisionHistoryResponse

router = APIRouter(prefix="/processes", tags=["decisions"])

_MAX_DECISION_LIMIT = 1000


@router.get("/{process_id}/decisions", response_model=list[DecisionHistoryResponse])
async def list_process_decisions(
    process_id: str,
    node_id: str | None = None,
    run_id: str | None = None,
    limit: int | None = Query(default=None, ge=1, le=_MAX_DECISION_LIMIT),
    mode: str | None = None,
    roots: Roots = Depends(get_roots),
) -> list[DecisionHistoryResponse]:
    """List decision history for a process with optional filters."""
    records = await roots.storage.list_decisions(
        process_id,
        node_id,
        run_id=run_id,
        limit=limit,
        mode=mode,
    )
    return [
        DecisionHistoryResponse(
            id=r.id,
            run_id=r.run_id,
            process_id=r.process_id,
            node_id=r.node_id,
            mode=r.mode,
            decision=r.decision,
            confidence=r.confidence,
            created_at=r.created_at,
        )
        for r in records
    ]
