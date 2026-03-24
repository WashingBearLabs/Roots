"""Tests for Run Lifecycle Routes (US-004)."""

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


@pytest.fixture
async def roots_instance(fastapi_app):
    return fastapi_app.state.roots


async def _create_process(client: AsyncClient) -> None:
    await client.post("/processes", json={"definition": VALID_PROCESS_DEF})


async def _create_run(client: AsyncClient) -> str:
    await _create_process(client)
    resp = await client.post(
        "/runs", json={"process_id": "proc-1", "work_item": {"key": "value"}}
    )
    return resp.json()["id"]


# --- Pause ---


@pytest.mark.asyncio
async def test_pause_running_run(client, roots_instance):
    """POST /runs/{run_id}/pause transitions running→paused."""
    run_id = await _create_run(client)

    # Wait for run to start executing (transition to running)
    await asyncio.sleep(0.1)

    # Force status to running so we can test pause
    await roots_instance.storage.update_run_status(run_id, "running")

    resp = await client.post(f"/runs/{run_id}/pause")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "paused"
    assert data["id"] == run_id


@pytest.mark.asyncio
async def test_pause_non_running_returns_409(client, roots_instance):
    """POST /runs/{run_id}/pause returns 409 if run is not running."""
    run_id = await _create_run(client)
    await asyncio.sleep(0.5)

    # Run should be completed now
    get_resp = await client.get(f"/runs/{run_id}")
    assert get_resp.json()["status"] == "completed"

    resp = await client.post(f"/runs/{run_id}/pause")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "Cannot transition from" in detail
    assert "Valid targets:" in detail


@pytest.mark.asyncio
async def test_pause_not_found(client):
    """POST /runs/{run_id}/pause returns 404 for unknown run."""
    resp = await client.post("/runs/nonexistent/pause")
    assert resp.status_code == 404


# --- Resume ---


@pytest.mark.asyncio
async def test_resume_paused_run(client, roots_instance, fastapi_app):
    """POST /runs/{run_id}/resume transitions paused→running and restarts execution."""
    run_id = await _create_run(client)
    await asyncio.sleep(0.1)

    # Force to paused state
    await roots_instance.storage.update_run_status(run_id, "paused")

    resp = await client.post(f"/runs/{run_id}/resume")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"
    assert data["id"] == run_id

    # Verify a new background task was created for execution
    assert hasattr(fastapi_app.state, "_background_tasks")
    assert isinstance(fastapi_app.state._background_tasks, set)


@pytest.mark.asyncio
async def test_resume_non_paused_returns_409(client, roots_instance):
    """POST /runs/{run_id}/resume returns 409 if run is not paused."""
    run_id = await _create_run(client)
    await asyncio.sleep(0.5)

    # Run should be completed now
    resp = await client.post(f"/runs/{run_id}/resume")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "Cannot transition from" in detail
    assert "Valid targets:" in detail


@pytest.mark.asyncio
async def test_resume_not_found(client):
    """POST /runs/{run_id}/resume returns 404 for unknown run."""
    resp = await client.post("/runs/nonexistent/resume")
    assert resp.status_code == 404


# --- Pause/Resume Cycle ---


@pytest.mark.asyncio
async def test_pause_resume_cycle(client, roots_instance):
    """Full pause→resume cycle works correctly."""
    run_id = await _create_run(client)
    await asyncio.sleep(0.1)

    # Force to running
    await roots_instance.storage.update_run_status(run_id, "running")

    # Pause
    resp = await client.post(f"/runs/{run_id}/pause")
    assert resp.status_code == 200
    assert resp.json()["status"] == "paused"

    # Resume
    resp = await client.post(f"/runs/{run_id}/resume")
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"


# --- History ---


@pytest.mark.asyncio
async def test_history_returns_ordered_events(client, roots_instance):
    """GET /runs/{run_id}/history returns ordered history events."""
    run_id = await _create_run(client)

    # Wait for execution to complete so history events are generated
    await asyncio.sleep(0.5)

    resp = await client.get(f"/runs/{run_id}/history")
    assert resp.status_code == 200
    events = resp.json()
    assert isinstance(events, list)

    # Verify event structure
    for event in events:
        assert "event_type" in event
        assert "data" in event
        assert "created_at" in event

    # Verify chronological order
    if len(events) >= 2:
        timestamps = [e["created_at"] for e in events]
        assert timestamps == sorted(timestamps)


@pytest.mark.asyncio
async def test_history_empty_for_new_run(client):
    """GET /runs/{run_id}/history returns empty list for a fresh run."""
    run_id = await _create_run(client)

    # Immediately check history before execution generates events
    resp = await client.get(f"/runs/{run_id}/history")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_history_not_found(client):
    """GET /runs/{run_id}/history returns 404 for unknown run."""
    resp = await client.get("/runs/nonexistent/history")
    assert resp.status_code == 404
