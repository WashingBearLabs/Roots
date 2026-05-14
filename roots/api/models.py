"""Request and response models for the Roots HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_validator


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
    process_version: str | None = None


class ProcessVersionSummary(BaseModel):
    id: str
    version: str
    created_at: datetime


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


# --- Agent models ---


class AgentRegisterRequest(BaseModel):
    """Body for POST /agents — register a remote agent."""

    name: str
    type: str = "remote"
    callback_url: str
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    timeout_seconds: int = 300


class AgentSummary(BaseModel):
    name: str
    type: str
    callback_url: str | None = None
    created_at: datetime


class AgentHealthResponse(BaseModel):
    name: str
    status: str
    response_time_ms: float | None = None
    error: str | None = None


# --- Webhook models ---


class WebhookCreateRequest(BaseModel):
    """Body for POST /webhooks."""

    url: str
    events: list[str] = ["roots.run.*"]
    secret: str | None = None

    @field_validator("url")
    @classmethod
    def validate_webhook_url(cls, v: str) -> str:
        from roots.core.url_validator import validate_url
        return validate_url(v)


class WebhookResponse(BaseModel):
    id: str
    url: str
    events: list[str]
    secret: str | None = None
    created_at: datetime


class WebhookTestResult(BaseModel):
    status: str  # "delivered" | "failed"
    response_code: int | None = None
    error: str | None = None


# --- Graph models ---


class GraphNodeResponse(BaseModel):
    id: str
    type: str
    label: str
    status: str
    started_at: str | None = None
    completed_at: str | None = None
    position: dict[str, Any]
    metadata: dict[str, Any]


class GraphEdgeResponse(BaseModel):
    id: str
    from_node: str
    to_node: str
    condition: str | None = None
    status: str
    label: str | None = None


class GraphResponse(BaseModel):
    process_id: str
    run_id: str | None = None
    run_status: str | None = None
    nodes: list[GraphNodeResponse]
    edges: list[GraphEdgeResponse]


# --- Graph mutation models ---


class NodeCreateRequest(BaseModel):
    id: str
    type: str
    label: str
    config: dict[str, Any]
    metadata: dict[str, Any] | None = None


class NodeUpdateRequest(BaseModel):
    label: str | None = None
    config: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class EdgeCreateRequest(BaseModel):
    from_node: str
    to_node: str
    label: str | None = None
    condition: str | None = None


class PositionUpdateRequest(BaseModel):
    x: float
    y: float


class NodeMutationResponse(BaseModel):
    id: str
    type: str
    label: str
    config: dict[str, Any]
    metadata: dict[str, Any]


class EdgeMutationResponse(BaseModel):
    id: str
    from_node: str
    to_node: str
    label: str | None = None
    condition: str | None = None
