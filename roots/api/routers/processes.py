"""Process management routes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError

from roots import Roots
from roots.api.deps import get_roots
from roots.api.models import (
    ProcessCreateRequest,
    ProcessCreateResponse,
    ProcessDetail,
    ProcessSummary,
    ProcessValidationResponse,
    ProcessVersionSummary,
)
from roots.core.validator import (
    format_validation_errors,
    parse_process_dict,
    validate_structure,
)

router = APIRouter(prefix="/processes", tags=["processes"])


@router.post(
    "",
    response_model=ProcessCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_process(
    body: ProcessCreateRequest,
    roots: Roots = Depends(get_roots),
) -> ProcessCreateResponse:
    """Create a new process from a definition dict."""
    try:
        process = parse_process_dict(body.definition)
    except ValidationError as exc:
        errors = format_validation_errors(exc, raw_data=body.definition)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=errors,
        )

    await roots.storage.save_process(process)
    return ProcessCreateResponse(
        id=process.id,
        name=process.name,
        version=process.version,
        created_at=datetime.now(timezone.utc),
    )


@router.get("", response_model=list[ProcessSummary])
async def list_processes(
    roots: Roots = Depends(get_roots),
) -> list[ProcessSummary]:
    """List all processes."""
    processes = await roots.storage.list_processes()
    return [
        ProcessSummary(
            id=p.id,
            name=p.name,
            version=p.version,
            description=p.description,
        )
        for p in processes
    ]


@router.get("/{process_id}", response_model=ProcessDetail)
async def get_process(
    process_id: str,
    roots: Roots = Depends(get_roots),
) -> ProcessDetail:
    """Get a process by ID."""
    process = await roots.storage.get_process(process_id)
    if process is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Process '{process_id}' not found",
        )
    return ProcessDetail(
        id=process.id,
        name=process.name,
        version=process.version,
        description=process.description,
        entry_point=process.entry_point,
        nodes=[_node_to_dict(n) for n in process.nodes],
        edges=[_edge_to_dict(e) for e in process.edges],
    )


@router.put("/{process_id}", response_model=ProcessDetail)
async def update_process(
    process_id: str,
    body: ProcessCreateRequest,
    roots: Roots = Depends(get_roots),
) -> ProcessDetail:
    """Update an existing process."""
    existing = await roots.storage.get_process(process_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Process '{process_id}' not found",
        )

    try:
        process = parse_process_dict(body.definition)
    except ValidationError as exc:
        errors = format_validation_errors(exc, raw_data=body.definition)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=errors,
        )

    if process.id != process_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Definition id '{process.id}' does not match URL id '{process_id}'",
        )

    await roots.storage.save_process(process)
    return ProcessDetail(
        id=process.id,
        name=process.name,
        version=process.version,
        description=process.description,
        entry_point=process.entry_point,
        nodes=[_node_to_dict(n) for n in process.nodes],
        edges=[_edge_to_dict(e) for e in process.edges],
    )


@router.delete(
    "/{process_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_process(
    process_id: str,
    roots: Roots = Depends(get_roots),
) -> None:
    """Delete a process."""
    deleted = await roots.storage.delete_process(process_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Process '{process_id}' not found",
        )


@router.get(
    "/{process_id}/validate",
    response_model=ProcessValidationResponse,
)
async def validate_process(
    process_id: str,
    roots: Roots = Depends(get_roots),
) -> ProcessValidationResponse:
    """Validate a stored process without modifying it."""
    process = await roots.storage.get_process(process_id)
    if process is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Process '{process_id}' not found",
        )
    errors = validate_structure(process)
    return ProcessValidationResponse(valid=len(errors) == 0, errors=errors)


@router.get(
    "/{process_id}/versions",
    response_model=list[ProcessVersionSummary],
)
async def list_process_versions(
    process_id: str,
    roots: Roots = Depends(get_roots),
) -> list[ProcessVersionSummary]:
    """List all versions for a process."""
    process = await roots.storage.get_process(process_id)
    if process is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Process '{process_id}' not found",
        )
    versions = await roots.storage.list_process_versions(process_id)
    return [
        ProcessVersionSummary(
            id=v.id,
            version=v.version,
            created_at=v.created_at,
        )
        for v in versions
    ]


@router.get(
    "/{process_id}/versions/{version}",
    response_model=ProcessDetail,
)
async def get_process_version(
    process_id: str,
    version: str,
    roots: Roots = Depends(get_roots),
) -> ProcessDetail:
    """Get a specific version of a process."""
    process = await roots.storage.get_process_version(process_id, version)
    if process is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Process '{process_id}' version '{version}' not found",
        )
    return ProcessDetail(
        id=process.id,
        name=process.name,
        version=process.version,
        description=process.description,
        entry_point=process.entry_point,
        nodes=[_node_to_dict(n) for n in process.nodes],
        edges=[_edge_to_dict(e) for e in process.edges],
    )


# --- Helpers ---


def _node_to_dict(node: Any) -> dict[str, Any]:
    """Serialize a NodeDefinition to a plain dict."""
    data = node.model_dump()
    data["type"] = str(node.type)
    return data


def _edge_to_dict(edge: Any) -> dict[str, Any]:
    """Serialize an EdgeDefinition to a plain dict."""
    return edge.model_dump(by_alias=True)
