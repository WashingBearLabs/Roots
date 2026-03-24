"""Tests for Graph Data Read Endpoints (US-008)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from roots import Roots
from roots.api.app import create_app
from roots.storage.sqlite import SqliteBackend


SIMPLE_PROCESS = {
    "id": "test-linear",
    "name": "Linear Process",
    "version": "1.0.0",
    "description": "A simple linear process",
    "entry_point": "start",
    "nodes": [
        {
            "id": "start",
            "type": "agent",
            "label": "Start",
            "config": {"agent": "echo", "output_key": "result"},
        },
        {
            "id": "middle",
            "type": "agent",
            "label": "Middle",
            "config": {"agent": "echo", "output_key": "mid_result"},
        },
        {
            "id": "end",
            "type": "end",
            "label": "End",
            "config": {"status": "completed"},
        },
    ],
    "edges": [
        {"from": "start", "to": "middle"},
        {"from": "middle", "to": "end"},
    ],
}

CHECKPOINT_PROCESS = {
    "id": "test-checkpoint",
    "name": "Checkpoint Process",
    "version": "1.0.0",
    "description": "Process with a checkpoint",
    "entry_point": "agent1",
    "nodes": [
        {
            "id": "agent1",
            "type": "agent",
            "label": "First Agent",
            "config": {"agent": "echo", "output_key": "step1"},
        },
        {
            "id": "review",
            "type": "checkpoint",
            "label": "Human Review",
            "config": {"prompt": "Please review the output"},
        },
        {
            "id": "agent2",
            "type": "agent",
            "label": "Second Agent",
            "config": {"agent": "echo", "output_key": "step2"},
        },
        {
            "id": "end",
            "type": "end",
            "label": "End",
            "config": {"status": "completed"},
        },
    ],
    "edges": [
        {"from": "agent1", "to": "review"},
        {"from": "review", "to": "agent2"},
        {"from": "agent2", "to": "end"},
    ],
}


async def echo_agent(input: dict) -> dict:
    return {"output": {"echo": input["work_item_state"]}, "escalate": False}


@pytest.fixture
async def fastapi_app():
    backend = SqliteBackend(":memory:")
    await backend.initialize()
    roots = Roots(storage=backend)
    await roots.register_agent("echo", echo_agent)
    application = create_app(roots)
    yield application
    await roots.close()


@pytest.fixture
async def client(fastapi_app):
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _create_process(client, process_def=None):
    """Helper to create a process via the API."""
    body = process_def or SIMPLE_PROCESS
    resp = await client.post("/processes", json={"definition": body})
    assert resp.status_code == 201
    return resp.json()


# --- Process graph ---


@pytest.mark.asyncio
async def test_process_graph_structure(client):
    """GET /processes/{id}/graph returns correct node/edge structure."""
    await _create_process(client)

    resp = await client.get("/processes/test-linear/graph")
    assert resp.status_code == 200
    data = resp.json()

    assert data["process_id"] == "test-linear"
    assert data["run_id"] is None
    assert data["run_status"] is None
    assert len(data["nodes"]) == 3
    assert len(data["edges"]) == 2


@pytest.mark.asyncio
async def test_process_graph_all_pending(client):
    """Process graph nodes and edges all have 'pending' status."""
    await _create_process(client)

    resp = await client.get("/processes/test-linear/graph")
    data = resp.json()

    for node in data["nodes"]:
        assert node["status"] == "pending"
        assert node["started_at"] is None
        assert node["completed_at"] is None

    for edge in data["edges"]:
        assert edge["status"] == "pending"


@pytest.mark.asyncio
async def test_process_graph_node_fields(client):
    """Process graph nodes contain expected fields."""
    await _create_process(client)

    resp = await client.get("/processes/test-linear/graph")
    node = resp.json()["nodes"][0]

    assert "id" in node
    assert "type" in node
    assert "label" in node
    assert "status" in node
    assert "started_at" in node
    assert "completed_at" in node
    assert "position" in node
    assert "metadata" in node
    assert node["position"] == {"x": 0, "y": 0}


@pytest.mark.asyncio
async def test_process_graph_edge_fields(client):
    """Process graph edges contain expected fields."""
    await _create_process(client)

    resp = await client.get("/processes/test-linear/graph")
    edge = resp.json()["edges"][0]

    assert "id" in edge
    assert "from_node" in edge
    assert "to_node" in edge
    assert "status" in edge


@pytest.mark.asyncio
async def test_process_graph_not_found(client):
    """GET /processes/{id}/graph returns 404 for non-existent process."""
    resp = await client.get("/processes/nonexistent/graph")
    assert resp.status_code == 404


# --- Run graph ---


@pytest.mark.asyncio
async def test_run_graph_completed(client):
    """GET /runs/{id}/graph returns execution state for a completed run."""
    await _create_process(client)

    # Start and execute a run
    run_resp = await client.post(
        "/runs", json={"process_id": "test-linear", "work_item": {"val": 1}}
    )
    assert run_resp.status_code == 201
    run_id = run_resp.json()["id"]

    # Wait briefly for background execution, then poll
    import asyncio

    for _ in range(20):
        await asyncio.sleep(0.1)
        status_resp = await client.get(f"/runs/{run_id}")
        if status_resp.json()["status"] == "completed":
            break

    resp = await client.get(f"/runs/{run_id}/graph")
    assert resp.status_code == 200
    data = resp.json()

    assert data["process_id"] == "test-linear"
    assert data["run_id"] == run_id
    assert data["run_status"] == "completed"
    assert len(data["nodes"]) == 3
    assert len(data["edges"]) == 2


@pytest.mark.asyncio
async def test_run_graph_node_statuses(client):
    """Run graph contains execution status on nodes."""
    await _create_process(client, CHECKPOINT_PROCESS)

    run_resp = await client.post(
        "/runs",
        json={"process_id": "test-checkpoint", "work_item": {"data": "test"}},
    )
    run_id = run_resp.json()["id"]

    import asyncio

    for _ in range(20):
        await asyncio.sleep(0.1)
        status_resp = await client.get(f"/runs/{run_id}")
        if status_resp.json()["status"] == "paused":
            break

    resp = await client.get(f"/runs/{run_id}/graph")
    assert resp.status_code == 200
    data = resp.json()

    node_map = {n["id"]: n for n in data["nodes"]}

    # agent1 completed, review paused, agent2 and end pending
    assert node_map["agent1"]["status"] == "completed"
    assert node_map["review"]["status"] == "paused"
    assert node_map["agent2"]["status"] == "pending"
    assert node_map["end"]["status"] == "pending"


@pytest.mark.asyncio
async def test_run_graph_edge_statuses(client):
    """Run graph edges show traversed/pending status."""
    await _create_process(client)

    run_resp = await client.post(
        "/runs", json={"process_id": "test-linear", "work_item": {"val": 1}}
    )
    run_id = run_resp.json()["id"]

    import asyncio

    for _ in range(20):
        await asyncio.sleep(0.1)
        status_resp = await client.get(f"/runs/{run_id}")
        if status_resp.json()["status"] == "completed":
            break

    resp = await client.get(f"/runs/{run_id}/graph")
    data = resp.json()

    # In a completed run, all edges should be traversed
    for edge in data["edges"]:
        assert edge["status"] == "traversed"


@pytest.mark.asyncio
async def test_run_graph_not_found(client):
    """GET /runs/{id}/graph returns 404 for non-existent run."""
    resp = await client.get("/runs/nonexistent-run/graph")
    assert resp.status_code == 404
