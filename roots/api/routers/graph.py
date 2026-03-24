"""Graph data read endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from roots import Roots
from roots.api.deps import get_roots
from roots.api.models import GraphEdgeResponse, GraphNodeResponse, GraphResponse
from roots.core.orchestrator import OrchestrationError

router = APIRouter(tags=["graph"])


@router.get("/processes/{process_id}/graph", response_model=GraphResponse)
async def get_process_graph(
    process_id: str,
    roots: Roots = Depends(get_roots),
) -> GraphResponse:
    """Return a process as a node/edge graph with all statuses pending."""
    process = await roots.storage.get_process(process_id)
    if process is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Process '{process_id}' not found",
        )

    nodes = []
    for node_def in process.nodes:
        metadata = node_def.metadata or {}
        position = metadata.get("position", {"x": 0, "y": 0})
        nodes.append(
            GraphNodeResponse(
                id=node_def.id,
                type=node_def.type.value,
                label=node_def.label,
                status="pending",
                started_at=None,
                completed_at=None,
                position=position,
                metadata=metadata,
            )
        )

    edges = []
    for edge_def in process.edges:
        edges.append(
            GraphEdgeResponse(
                id=edge_def.id,
                from_node=edge_def.from_node,
                to_node=edge_def.to_node,
                condition=edge_def.condition,
                status="pending",
                label=edge_def.label,
            )
        )

    return GraphResponse(
        process_id=process.id,
        run_id=None,
        run_status=None,
        nodes=nodes,
        edges=edges,
    )


@router.get("/runs/{run_id}/graph", response_model=GraphResponse)
async def get_run_graph(
    run_id: str,
    roots: Roots = Depends(get_roots),
) -> GraphResponse:
    """Return graph data for a run with execution state merged in."""
    try:
        graph = await roots.get_run_graph(run_id)
    except OrchestrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    nodes = [
        GraphNodeResponse(
            id=n["id"],
            type=n["type"],
            label=n["label"],
            status=n["status"],
            started_at=n["started_at"],
            completed_at=n["completed_at"],
            position=n["position"],
            metadata=n["metadata"],
        )
        for n in graph["nodes"]
    ]

    edges = [
        GraphEdgeResponse(
            id=e["id"],
            from_node=e["from"],
            to_node=e["to"],
            condition=e["condition"],
            status=e["status"],
            label=e["label"],
        )
        for e in graph["edges"]
    ]

    return GraphResponse(
        process_id=graph["process_id"],
        run_id=graph["run_id"],
        run_status=graph["run_status"],
        nodes=nodes,
        edges=edges,
    )
