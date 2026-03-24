"""Request and response models for the Roots HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


# --- Process models ---


class ProcessCreateRequest(BaseModel):
    """Body for POST /processes — a raw process definition dict."""

    definition: dict[str, Any]


class ProcessCreateResponse(BaseModel):
    id: str
    name: str
    version: str
    created_at: datetime


class ProcessSummary(BaseModel):
    id: str
    name: str
    version: str
    description: str | None = None


class ProcessDetail(BaseModel):
    """Full process definition returned as JSON."""

    id: str
    name: str
    version: str
    description: str | None = None
    entry_point: str
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]


class ProcessValidationResponse(BaseModel):
    valid: bool
    errors: list[str]


# --- Run models ---


class RunCreateRequest(BaseModel):
    """Body for POST /runs."""

    process_id: str
    work_item: dict[str, Any]


class RunResponse(BaseModel):
    id: str
    process_id: str
    status: str
    current_node_id: str | None = None
    work_item_state: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class HistoryEventResponse(BaseModel):
    event_type: str
    node_id: str | None = None
    data: dict[str, Any]
    created_at: datetime


# --- Checkpoint models ---


class CheckpointResponse(BaseModel):
    id: str
    run_id: str
    node_id: str
    type: str
    prompt: str
    ai_recommendation: dict[str, Any] | None = None
    status: str


class CheckpointResolveRequest(BaseModel):
    decision: str
    notes: str | None = None
    redirect_to: str | None = None
