"""Tests for Agent Registry Routes (US-006)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from roots import Roots
from roots.api.app import create_app
from roots.storage.sqlite import SqliteBackend


@pytest.fixture
async def fastapi_app():
    backend = SqliteBackend(":memory:")
    await backend.initialize()
    roots = Roots(storage=backend)

    # Register a local agent so we can test mixed listing
    async def echo_agent(input: dict) -> dict:  # noqa: A002
        return {"output": {"echo": input["work_item_state"]}, "escalate": False}

    await roots.register_agent("echo", echo_agent)

    application = create_app(roots)
    yield application
    await roots.close()


@pytest.fixture
async def client(fastapi_app):
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


REMOTE_AGENT_BODY = {
    "name": "remote-worker",
    "type": "remote",
    "callback_url": "http://example.com/agent",
    "input_schema": {"type": "object"},
    "output_schema": {"type": "object"},
    "timeout_seconds": 30,
}


# --- Registration ---


@pytest.mark.asyncio
async def test_register_remote_agent(client):
    """POST /agents registers a remote agent and returns 201."""
    resp = await client.post("/agents", json=REMOTE_AGENT_BODY)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "remote-worker"
    assert data["type"] == "remote"
    assert data["callback_url"] == "http://example.com/agent"
    assert "created_at" in data


@pytest.mark.asyncio
async def test_register_duplicate_agent(client):
    """POST /agents with duplicate name returns 409."""
    await client.post("/agents", json=REMOTE_AGENT_BODY)
    resp = await client.post("/agents", json=REMOTE_AGENT_BODY)
    assert resp.status_code == 409


# --- Listing ---


@pytest.mark.asyncio
async def test_list_agents_includes_local_and_remote(client):
    """GET /agents returns both local and remote agents."""
    # Register a remote agent
    await client.post("/agents", json=REMOTE_AGENT_BODY)

    resp = await client.get("/agents")
    assert resp.status_code == 200
    data = resp.json()
    names = {a["name"] for a in data}
    assert "echo" in names  # local agent from fixture
    assert "remote-worker" in names  # remote agent just registered


@pytest.mark.asyncio
async def test_list_agents_empty_returns_local_only(client):
    """GET /agents with no remote agents still returns local agents."""
    resp = await client.get("/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "echo"
    assert data[0]["type"] == "local"


# --- Deregister ---


@pytest.mark.asyncio
async def test_deregister_agent(client):
    """DELETE /agents/{name} removes the agent."""
    await client.post("/agents", json=REMOTE_AGENT_BODY)
    resp = await client.delete("/agents/remote-worker")
    assert resp.status_code == 204

    # Verify it's gone
    resp = await client.get("/agents")
    names = {a["name"] for a in resp.json()}
    assert "remote-worker" not in names


@pytest.mark.asyncio
async def test_deregister_not_found(client):
    """DELETE /agents/{name} returns 404 if agent not found."""
    resp = await client.delete("/agents/nonexistent")
    assert resp.status_code == 404


# --- Health check ---


@pytest.mark.asyncio
async def test_health_check_local_agent(client):
    """GET /agents/{name}/health for local agent returns healthy."""
    resp = await client.get("/agents/echo/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "echo"
    assert data["status"] == "healthy"


@pytest.mark.asyncio
async def test_health_check_remote_unhealthy(client):
    """GET /agents/{name}/health returns unhealthy on unreachable URL."""
    # Register agent with unreachable callback URL
    body = {
        "name": "unreachable",
        "type": "remote",
        "callback_url": "http://192.0.2.1:9999/nope",  # RFC 5737 TEST-NET
        "timeout_seconds": 5,
    }
    await client.post("/agents", json=body)

    resp = await client.get("/agents/unreachable/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "unreachable"
    assert data["status"] == "unhealthy"
    assert data["error"] is not None
    assert data["response_time_ms"] is not None


@pytest.mark.asyncio
async def test_health_check_not_found(client):
    """GET /agents/{name}/health returns 404 for unknown agent."""
    resp = await client.get("/agents/nonexistent/health")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_agent_persisted_to_storage(fastapi_app):
    """Registered remote agents are persisted to storage."""
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/agents", json=REMOTE_AGENT_BODY)

    # Check storage directly
    roots: Roots = fastapi_app.state.roots
    stored = await roots.storage.get_agent("remote-worker")
    assert stored is not None
    assert stored["name"] == "remote-worker"
    assert stored["callback_url"] == "http://example.com/agent"
