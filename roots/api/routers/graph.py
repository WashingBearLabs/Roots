"""Graph data read and mutation endpoints."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError

from roots import Roots
from roots.api.deps import get_roots
from roots.api.models import (
    EdgeCreateRequest,
    EdgeMutationResponse,
    GraphEdgeResponse,
    GraphNodeResponse,
    GraphResponse,
    NodeCreateRequest,
    NodeMutationResponse,
    NodeUpdateRequest,
    PositionUpdateRequest,
)
from roots.core.orchestrator import OrchestrationError
from roots.core.schema import EdgeDefinition, NodeDefinition, ProcessDefinition
from roots.core.validator import validate_structure

router = APIRouter(tags=["graph"])


async def _load_process(
    process_id: str, roots: Roots
) -> ProcessDefinition:
    """Load a process or raise 404."""
    process = await roots.storage.get_process(process_id)
    if process is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Process '{process_id}' not found",
        )
    return process


def _validate_and_save_check(process: ProcessDefinition) -> list[str]:
    """Run validate_structure and return errors."""
    return validate_structure(process)


def _node_to_response(node: NodeDefinition) -> NodeMutationResponse:
    config = node.config
    if not isinstance(config, dict):
        config = config.model_dump()
    return NodeMutationResponse(
        id=node.id,
        type=node.type.value,
        label=node.label,
        config=config,
        metadata=node.metadata or {},
    )


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


# --- Mutation endpoints ---


@router.post(
    "/processes/{process_id}/nodes",
    response_model=NodeMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_node(
    process_id: str,
    body: NodeCreateRequest,
    roots: Roots = Depends(get_roots),
) -> NodeMutationResponse:
    """Add a node to a process definition."""
    process = await _load_process(process_id, roots)

    # Check for duplicate node ID
    if any(n.id == body.id for n in process.nodes):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Node '{body.id}' already exists",
        )

    try:
        node = NodeDefinition(
            id=body.id,
            type=body.type,
            label=body.label,
            config=body.config,
            metadata=body.metadata,
        )
    except (ValidationError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    process.nodes.append(node)
    errors = _validate_and_save_check(process)
    if errors:
        process.nodes.pop()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=errors,
        )

    # Rebuild _node_map
    process._node_map[node.id] = node
    await roots.storage.save_process(process)
    return _node_to_response(node)


@router.put(
    "/processes/{process_id}/nodes/{node_id}",
    response_model=NodeMutationResponse,
)
async def update_node(
    process_id: str,
    node_id: str,
    body: NodeUpdateRequest,
    roots: Roots = Depends(get_roots),
) -> NodeMutationResponse:
    """Update a node's config, label, or metadata."""
    process = await _load_process(process_id, roots)

    node_idx = next(
        (i for i, n in enumerate(process.nodes) if n.id == node_id), None
    )
    if node_idx is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Node '{node_id}' not found",
        )

    orig_node = process.nodes[node_idx]

    # Build updated node data, re-validating via constructor
    try:
        updated_node = NodeDefinition(
            id=orig_node.id,
            type=orig_node.type,
            label=body.label if body.label is not None else orig_node.label,
            config=body.config if body.config is not None else orig_node.config,
            metadata=body.metadata if body.metadata is not None else orig_node.metadata,
            retry=orig_node.retry,
        )
    except (ValidationError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    process.nodes[node_idx] = updated_node
    process._node_map[updated_node.id] = updated_node

    errors = _validate_and_save_check(process)
    if errors:
        process.nodes[node_idx] = orig_node
        process._node_map[orig_node.id] = orig_node
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=errors,
        )

    await roots.storage.save_process(process)
    return _node_to_response(updated_node)


@router.delete(
    "/processes/{process_id}/nodes/{node_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_node(
    process_id: str,
    node_id: str,
    roots: Roots = Depends(get_roots),
) -> None:
    """Remove a node and all edges referencing it."""
    process = await _load_process(process_id, roots)

    node_idx = next(
        (i for i, n in enumerate(process.nodes) if n.id == node_id), None
    )
    if node_idx is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Node '{node_id}' not found",
        )

    # Save for rollback
    orig_nodes = list(process.nodes)
    orig_edges = list(process.edges)

    # Remove the node
    process.nodes.pop(node_idx)
    # Remove edges referencing this node
    process.edges = [
        e
        for e in process.edges
        if e.from_node != node_id and e.to_node != node_id
    ]
    # Rebuild _node_map
    process._node_map = {n.id: n for n in process.nodes}

    errors = _validate_and_save_check(process)
    if errors:
        process.nodes = orig_nodes
        process.edges = orig_edges
        process._node_map = {n.id: n for n in process.nodes}
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=errors,
        )

    await roots.storage.save_process(process)


@router.post(
    "/processes/{process_id}/edges",
    response_model=EdgeMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_edge(
    process_id: str,
    body: EdgeCreateRequest,
    roots: Roots = Depends(get_roots),
) -> EdgeMutationResponse:
    """Add an edge to a process definition."""
    process = await _load_process(process_id, roots)

    edge = EdgeDefinition.model_validate({
        "id": str(uuid4()),
        "from": body.from_node,
        "to": body.to_node,
        "label": body.label,
        "condition": body.condition,
    })

    # Check that edge references valid nodes
    node_ids = {n.id for n in process.nodes}
    if edge.from_node not in node_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Edge references unknown from node '{edge.from_node}'",
        )
    if edge.to_node not in node_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Edge references unknown to node '{edge.to_node}'",
        )

    process.edges.append(edge)
    errors = _validate_and_save_check(process)
    if errors:
        process.edges.pop()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=errors,
        )

    await roots.storage.save_process(process)
    return EdgeMutationResponse(
        id=edge.id,
        from_node=edge.from_node,
        to_node=edge.to_node,
        label=edge.label,
        condition=edge.condition,
    )


@router.delete(
    "/processes/{process_id}/edges/{edge_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_edge(
    process_id: str,
    edge_id: str,
    roots: Roots = Depends(get_roots),
) -> None:
    """Remove an edge from a process definition."""
    process = await _load_process(process_id, roots)

    edge_idx = next(
        (i for i, e in enumerate(process.edges) if e.id == edge_id), None
    )
    if edge_idx is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Edge '{edge_id}' not found",
        )

    orig_edges = list(process.edges)
    process.edges.pop(edge_idx)

    errors = _validate_and_save_check(process)
    if errors:
        process.edges = orig_edges
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=errors,
        )

    await roots.storage.save_process(process)


@router.put(
    "/processes/{process_id}/nodes/{node_id}/position",
    response_model=NodeMutationResponse,
)
async def update_node_position(
    process_id: str,
    node_id: str,
    body: PositionUpdateRequest,
    roots: Roots = Depends(get_roots),
) -> NodeMutationResponse:
    """Update a node's position metadata without re-validation."""
    process = await _load_process(process_id, roots)

    node = next((n for n in process.nodes if n.id == node_id), None)
    if node is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Node '{node_id}' not found",
        )

    if node.metadata is None:
        node.metadata = {}
    node.metadata["position"] = {"x": body.x, "y": body.y}

    await roots.storage.save_process(process)
    return _node_to_response(node)
