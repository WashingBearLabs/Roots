"""Tests for Run CRUD Routes (US-003)."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from roots import Roots
from roots.api.app import create_app
from roots.storage.sqlite import SqliteBackend


VALID_PROCESS_DEF = {
    "id": "proc-1",
    "name": "Test Process",
    "version": "1.0.0",
    "description": "A simple test process",
    "nodes": [
        {
            "id": "start",
            "type": "agent",
            "label": "Start",
            "config": {"agent": "echo", "output_key": "result"},
        },
        {
            "id": "end",
            "type": "end",
            "label": "End",
            "config": {"status": "completed"},
        },
    ],
    "edges": [{"from": "start", "to": "end"}],
    "entry_point": "start",
}


@pytest.fixture
async def fastapi_app():
    backend = SqliteBackend(":memory:")
    await backend.initialize()
    roots = Roots(storage=backend)

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


async def _create_process(client: AsyncClient) -> None:
    await client.post("/processes", json={"definition": VALID_PROCESS_DEF})


# --- Create ---


@pytest.mark.asyncio
async def test_create_run_returns_201(client):
    """POST /runs returns 201 with run details."""
    await _create_process(client)
    resp = await client.post(
        "/runs", json={"process_id": "proc-1", "work_item": {"key": "value"}}
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["process_id"] == "proc-1"
    assert data["status"] in ("pending", "running")
    assert "id" in data
    assert "created_at" in data
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_create_run_starts_background_execution(client, fastapi_app):
    """Creating a run starts background execution that completes."""
    await _create_process(client)
    resp = await client.post(
        "/runs", json={"process_id": "proc-1", "work_item": {"msg": "hello"}}
    )
    assert resp.status_code == 201
    run_id = resp.json()["id"]

    # Wait for background task to complete
    await asyncio.sleep(0.5)

    resp = await client.get(f"/runs/{run_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_background_tasks_tracked(client, fastapi_app):
    """Background tasks are stored on app.state._background_tasks."""
    await _create_process(client)
    await client.post(
        "/runs", json={"process_id": "proc-1", "work_item": {"x": 1}}
    )
    assert hasattr(fastapi_app.state, "_background_tasks")
    assert isinstance(fastapi_app.state._background_tasks, set)


# --- List ---


@pytest.mark.asyncio
async def test_list_runs_empty(client):
    """GET /runs returns empty list when no runs exist."""
    resp = await client.get("/runs")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_runs_with_process_filter(client):
    """GET /runs?process_id=... filters by process."""
    await _create_process(client)
    await client.post(
        "/runs", json={"process_id": "proc-1", "work_item": {"a": 1}}
    )
    await asyncio.sleep(0.1)

    resp = await client.get("/runs", params={"process_id": "proc-1"})
    assert resp.status_code == 200
    runs = resp.json()
    assert len(runs) >= 1
    assert all(r["process_id"] == "proc-1" for r in runs)

    # Filter by non-existent process returns empty
    resp = await client.get("/runs", params={"process_id": "nonexistent"})
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_runs_with_status_filter(client):
    """GET /runs?run_status=... filters by status."""
    await _create_process(client)
    await client.post(
        "/runs", json={"process_id": "proc-1", "work_item": {"b": 2}}
    )
    await asyncio.sleep(0.5)

    resp = await client.get("/runs", params={"run_status": "completed"})
    assert resp.status_code == 200
    runs = resp.json()
    assert len(runs) >= 1
    assert all(r["status"] == "completed" for r in runs)


# --- Get ---


@pytest.mark.asyncio
async def test_get_run_returns_details(client):
    """GET /runs/{run_id} returns run details."""
    await _create_process(client)
    create_resp = await client.post(
        "/runs", json={"process_id": "proc-1", "work_item": {"c": 3}}
    )
    run_id = create_resp.json()["id"]

    resp = await client.get(f"/runs/{run_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == run_id
    assert data["process_id"] == "proc-1"


@pytest.mark.asyncio
async def test_get_run_not_found(client):
    """GET /runs/{run_id} returns 404 for unknown run."""
    resp = await client.get("/runs/nonexistent")
    assert resp.status_code == 404


# --- Cancel ---


@pytest.mark.asyncio
async def test_cancel_run(client):
    """DELETE /runs/{run_id} cancels a running run."""
    await _create_process(client)
    create_resp = await client.post(
        "/runs", json={"process_id": "proc-1", "work_item": {"d": 4}}
    )
    run_id = create_resp.json()["id"]

    resp = await client.delete(f"/runs/{run_id}")
    assert resp.status_code == 204

    # Verify status changed
    get_resp = await client.get(f"/runs/{run_id}")
    assert get_resp.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_run_not_found(client):
    """DELETE /runs/{run_id} returns 404 for unknown run."""
    resp = await client.delete("/runs/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cancel_completed_run_returns_409(client):
    """DELETE /runs/{run_id} returns 409 if run is in terminal state."""
    await _create_process(client)
    create_resp = await client.post(
        "/runs", json={"process_id": "proc-1", "work_item": {"e": 5}}
    )
    run_id = create_resp.json()["id"]

    # Wait for run to complete
    await asyncio.sleep(0.5)

    get_resp = await client.get(f"/runs/{run_id}")
    assert get_resp.json()["status"] == "completed"

    # Try to cancel completed run
    resp = await client.delete(f"/runs/{run_id}")
    assert resp.status_code == 409
    assert "Cannot cancel" in resp.json()["detail"]
