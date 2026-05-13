"""Decision history routes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from roots import Roots
from roots.api.deps import get_roots

router = APIRouter(prefix="/processes", tags=["decisions"])


class DecisionHistoryResponse(BaseModel):
    id: int
    run_id: str
    process_id: str
    node_id: str
    mode: str
    decision: dict[str, Any]
    confidence: float
    created_at: datetime


@router.get("/{process_id}/decisions", response_model=list[DecisionHistoryResponse])
async def list_process_decisions(
    process_id: str,
    node_id: str | None = None,
    run_id: str | None = None,
    limit: int | None = None,
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
