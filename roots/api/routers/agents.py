"""Agent registry routes."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from roots import Roots
from roots.agents.types import AgentRegistration, AgentType
from roots.api.deps import get_roots
from roots.api.models import AgentHealthResponse, AgentRegisterRequest, AgentSummary

router = APIRouter(prefix="/agents", tags=["agents"])


def _agent_to_summary(agent: AgentRegistration, created_at: datetime) -> AgentSummary:
    return AgentSummary(
        name=agent.name,
        type=agent.agent_type.value,
        callback_url=agent.callback_url,
        created_at=created_at,
    )


@router.get("", response_model=list[AgentSummary])
async def list_agents(
    roots: Roots = Depends(get_roots),
) -> list[AgentSummary]:
    """List all registered agents (local + remote)."""
    registry_agents = roots._agent_registry.list()
    now = datetime.now(tz=timezone.utc)
    results: list[AgentSummary] = []
    for agent in registry_agents:
        # Try to get created_at from storage
        stored = await roots.storage.get_agent(agent.name)
        created_at = (
            datetime.fromisoformat(stored["created_at"])
            if stored and "created_at" in stored
            else now
        )
        results.append(_agent_to_summary(agent, created_at))
    return results


@router.post(
    "",
    response_model=AgentSummary,
    status_code=status.HTTP_201_CREATED,
)
async def register_agent(
    body: AgentRegisterRequest,
    roots: Roots = Depends(get_roots),
) -> AgentSummary:
    """Register a remote agent."""
    # Check if already registered
    existing = roots._agent_registry.get(body.name)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Agent '{body.name}' is already registered",
        )

    registration = AgentRegistration(
        name=body.name,
        agent_type=AgentType.REMOTE,
        callback_url=body.callback_url,
        input_schema=body.input_schema,
        output_schema=body.output_schema,
        timeout_seconds=body.timeout_seconds,
    )
    roots._agent_registry.register(registration)

    created_at = datetime.now(tz=timezone.utc)

    # Persist to storage for recovery after restart
    await roots.storage.save_agent({
        "name": body.name,
        "type": "remote",
        "callback_url": body.callback_url,
        "input_schema": body.input_schema,
        "output_schema": body.output_schema,
        "timeout_seconds": body.timeout_seconds,
        "created_at": created_at.isoformat(),
    })

    return _agent_to_summary(registration, created_at)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def deregister_agent(
    name: str,
    roots: Roots = Depends(get_roots),
) -> None:
    """Deregister an agent."""
    removed = roots._agent_registry.deregister(name)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{name}' not found",
        )
    await roots.storage.delete_agent(name)


@router.get("/{name}/health", response_model=AgentHealthResponse)
async def agent_health(
    name: str,
    roots: Roots = Depends(get_roots),
) -> AgentHealthResponse:
    """Check agent health. Remote agents are pinged; local agents always healthy."""
    agent = roots._agent_registry.get(name)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{name}' not found",
        )

    if agent.agent_type == AgentType.LOCAL:
        return AgentHealthResponse(name=name, status="healthy")

    # Remote agent — GET callback_url with 5s timeout
    assert agent.callback_url is not None

    # Defense-in-depth SSRF check (registration-time validation is primary)
    from roots.core.url_validator import validate_url, SSRFError
    try:
        validate_url(agent.callback_url)
    except SSRFError as exc:
        return AgentHealthResponse(
            name=name,
            status="unhealthy",
            error=f"SSRF protection: {exc}",
        )

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.get(agent.callback_url)
        elapsed_ms = (time.monotonic() - start) * 1000
        return AgentHealthResponse(
            name=name,
            status="healthy",
            response_time_ms=round(elapsed_ms, 2),
        )
    except Exception as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        return AgentHealthResponse(
            name=name,
            status="unhealthy",
            response_time_ms=round(elapsed_ms, 2),
            error=str(exc),
        )
