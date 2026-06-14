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


# --- process_version in response ---


@pytest.mark.asyncio
async def test_run_response_includes_process_version(client):
    """RunResponse includes process_version field (pinned at run creation)."""
    await _create_process(client)
    resp = await client.post(
        "/runs", json={"process_id": "proc-1", "work_item": {"x": 1}}
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "process_version" in data
    assert data["process_version"] == "1.0.0"


# --- Metadata ---


@pytest.mark.asyncio
async def test_create_run_with_metadata(client):
    """POST /runs with metadata stores and returns metadata."""
    await _create_process(client)
    resp = await client.post(
        "/runs",
        json={"process_id": "proc-1", "work_item": {"x": 1}, "metadata": {"env": "test", "priority": 1}},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["metadata"] == {"env": "test", "priority": 1}


@pytest.mark.asyncio
async def test_create_run_without_metadata(client):
    """POST /runs without metadata returns metadata as None."""
    await _create_process(client)
    resp = await client.post(
        "/runs", json={"process_id": "proc-1", "work_item": {"x": 1}}
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["metadata"] is None


@pytest.mark.asyncio
async def test_create_run_with_nested_metadata_returns_422(client):
    """POST /runs with nested dict in metadata returns 422."""
    await _create_process(client)
    resp = await client.post(
        "/runs",
        json={"process_id": "proc-1", "work_item": {"x": 1}, "metadata": {"nested": {"a": 1}}},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_run_with_list_metadata_value_returns_422(client):
    """POST /runs with list value in metadata returns 422."""
    await _create_process(client)
    resp = await client.post(
        "/runs",
        json={"process_id": "proc-1", "work_item": {"x": 1}, "metadata": {"tags": ["a", "b"]}},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_run_includes_metadata(client):
    """GET /runs/{run_id} returns metadata in response."""
    await _create_process(client)
    create_resp = await client.post(
        "/runs",
        json={"process_id": "proc-1", "work_item": {"x": 1}, "metadata": {"env": "prod"}},
    )
    run_id = create_resp.json()["id"]

    resp = await client.get(f"/runs/{run_id}")
    assert resp.status_code == 200
    assert resp.json()["metadata"] == {"env": "prod"}


@pytest.mark.asyncio
async def test_list_runs_with_metadata_filter(client):
    """GET /runs?metadata_filter=... filters by metadata."""
    await _create_process(client)
    await client.post(
        "/runs",
        json={"process_id": "proc-1", "work_item": {"x": 1}, "metadata": {"env": "test"}},
    )
    await client.post(
        "/runs",
        json={"process_id": "proc-1", "work_item": {"x": 2}, "metadata": {"env": "prod"}},
    )

    import json as json_mod

    resp = await client.get(
        "/runs", params={"metadata_filter": json_mod.dumps({"env": "test"})}
    )
    assert resp.status_code == 200
    runs = resp.json()
    assert len(runs) == 1
    assert runs[0]["metadata"]["env"] == "test"


@pytest.mark.asyncio
async def test_list_runs_invalid_json_filter_returns_422(client):
    """GET /runs?metadata_filter=<invalid JSON> returns 422."""
    resp = await client.get("/runs", params={"metadata_filter": "not-valid-json{"})
    assert resp.status_code == 422
    assert "metadata_filter" in resp.json()["detail"].lower() or "invalid" in resp.json()["detail"].lower()


# --- Parent/child fields ---


@pytest.mark.asyncio
async def test_run_response_includes_parent_fields(client):
    """RunResponse includes parent_run_id and parent_node_id fields (null for top-level runs)."""
    await _create_process(client)
    resp = await client.post(
        "/runs", json={"process_id": "proc-1", "work_item": {"x": 1}}
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "parent_run_id" in data
    assert "parent_node_id" in data
    assert data["parent_run_id"] is None
    assert data["parent_node_id"] is None


# --- Children endpoint ---


@pytest.mark.asyncio
async def test_get_children_returns_empty_list_when_no_children(client):
    """GET /runs/{id}/children returns empty list when run has no children."""
    await _create_process(client)
    create_resp = await client.post(
        "/runs", json={"process_id": "proc-1", "work_item": {"x": 1}}
    )
    run_id = create_resp.json()["id"]

    resp = await client.get(f"/runs/{run_id}/children")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_children_returns_child_runs(client, fastapi_app):
    """GET /runs/{id}/children returns list of child runs."""
    await _create_process(client)
    create_resp = await client.post(
        "/runs", json={"process_id": "proc-1", "work_item": {"x": 1}}
    )
    parent_id = create_resp.json()["id"]

    storage = fastapi_app.state.roots.storage
    child = await storage.create_run(
        "proc-1",
        {"child_key": "val"},
        parent_run_id=parent_id,
        parent_node_id="subprocess-node",
    )

    resp = await client.get(f"/runs/{parent_id}/children")
    assert resp.status_code == 200
    children = resp.json()
    assert len(children) == 1
    assert children[0]["id"] == child.id
    assert children[0]["parent_run_id"] == parent_id
    assert children[0]["parent_node_id"] == "subprocess-node"


@pytest.mark.asyncio
async def test_get_children_returns_404_for_unknown_run(client):
    """GET /runs/{id}/children returns 404 when parent run does not exist."""
    resp = await client.get("/runs/nonexistent/children")
    assert resp.status_code == 404
