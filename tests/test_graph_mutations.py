"""Tests for Graph Mutation Endpoints (US-009)."""

from __future__ import annotations

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
            "id": "end",
            "type": "end",
            "label": "End",
            "config": {"status": "completed"},
        },
    ],
    "edges": [
        {"from": "start", "to": "end"},
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


# --- Add node ---


@pytest.mark.asyncio
async def test_add_node(client):
    """POST /processes/{id}/nodes adds a node that passes validation."""
    await _create_process(client)

    # Add an end node (end nodes don't need outbound edges, so validation passes)
    resp = await client.post(
        "/processes/test-linear/nodes",
        json={
            "id": "alt_end",
            "type": "end",
            "label": "Alt End",
            "config": {"status": "completed"},
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] == "alt_end"
    assert data["type"] == "end"
    assert data["label"] == "Alt End"

    # Verify the node persisted in the graph
    graph_resp = await client.get("/processes/test-linear/graph")
    node_ids = [n["id"] for n in graph_resp.json()["nodes"]]
    assert "alt_end" in node_ids


@pytest.mark.asyncio
async def test_add_node_duplicate_id(client):
    """POST /processes/{id}/nodes returns 400 for duplicate node ID."""
    await _create_process(client)

    resp = await client.post(
        "/processes/test-linear/nodes",
        json={
            "id": "start",
            "type": "agent",
            "label": "Duplicate",
            "config": {"agent": "echo", "output_key": "dup"},
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_add_node_invalid_config(client):
    """POST /processes/{id}/nodes returns 400 for invalid config."""
    await _create_process(client)

    resp = await client.post(
        "/processes/test-linear/nodes",
        json={
            "id": "bad",
            "type": "agent",
            "label": "Bad",
            "config": {"missing_required": True},
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_add_node_not_found_process(client):
    """POST /processes/{id}/nodes returns 404 for non-existent process."""
    resp = await client.post(
        "/processes/nonexistent/nodes",
        json={
            "id": "new",
            "type": "agent",
            "label": "New",
            "config": {"agent": "echo", "output_key": "result"},
        },
    )
    assert resp.status_code == 404


# --- Update node ---


@pytest.mark.asyncio
async def test_update_node_label(client):
    """PUT /processes/{id}/nodes/{node_id} updates label."""
    await _create_process(client)

    resp = await client.put(
        "/processes/test-linear/nodes/start",
        json={"label": "New Label"},
    )
    assert resp.status_code == 200
    assert resp.json()["label"] == "New Label"

    # Verify persistence
    graph_resp = await client.get("/processes/test-linear/graph")
    start_node = next(
        n for n in graph_resp.json()["nodes"] if n["id"] == "start"
    )
    assert start_node["label"] == "New Label"


@pytest.mark.asyncio
async def test_update_node_metadata(client):
    """PUT /processes/{id}/nodes/{node_id} updates metadata."""
    await _create_process(client)

    resp = await client.put(
        "/processes/test-linear/nodes/start",
        json={"metadata": {"color": "blue"}},
    )
    assert resp.status_code == 200
    assert resp.json()["metadata"] == {"color": "blue"}


@pytest.mark.asyncio
async def test_update_node_not_found(client):
    """PUT /processes/{id}/nodes/{node_id} returns 404 for missing node."""
    await _create_process(client)

    resp = await client.put(
        "/processes/test-linear/nodes/nonexistent",
        json={"label": "X"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_node_invalid_config(client):
    """PUT /processes/{id}/nodes/{node_id} returns 400 for invalid config."""
    await _create_process(client)

    resp = await client.put(
        "/processes/test-linear/nodes/start",
        json={"config": {"bad_field": True}},
    )
    assert resp.status_code == 400


# --- Delete node ---


@pytest.mark.asyncio
async def test_delete_node_removes_edges(client):
    """DELETE /processes/{id}/nodes/{node_id} removes node and its edges."""
    # Create process with 3 nodes so deleting middle doesn't break validation
    three_node = {
        **SIMPLE_PROCESS,
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
                "config": {"agent": "echo", "output_key": "mid"},
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
    await _create_process(client, three_node)

    # Add a direct edge from start->end first so validation passes after delete
    await client.post(
        "/processes/test-linear/edges",
        json={"from_node": "start", "to_node": "end"},
    )

    resp = await client.delete("/processes/test-linear/nodes/middle")
    assert resp.status_code == 204

    # Verify node and edges removed
    graph_resp = await client.get("/processes/test-linear/graph")
    data = graph_resp.json()
    node_ids = [n["id"] for n in data["nodes"]]
    assert "middle" not in node_ids

    # No edges should reference "middle"
    for edge in data["edges"]:
        assert edge["from_node"] != "middle"
        assert edge["to_node"] != "middle"


@pytest.mark.asyncio
async def test_delete_node_not_found(client):
    """DELETE /processes/{id}/nodes/{node_id} returns 404 for missing node."""
    await _create_process(client)

    resp = await client.delete("/processes/test-linear/nodes/nonexistent")
    assert resp.status_code == 404


# --- Add edge ---


@pytest.mark.asyncio
async def test_add_edge(client):
    """POST /processes/{id}/edges adds an edge."""
    await _create_process(client)

    # Add a parallel edge from start to end (duplicate edges are valid)
    resp = await client.post(
        "/processes/test-linear/edges",
        json={"from_node": "start", "to_node": "end", "label": "parallel"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["from_node"] == "start"
    assert data["to_node"] == "end"
    assert data["label"] == "parallel"
    assert "id" in data

    # Verify persistence
    graph_resp = await client.get("/processes/test-linear/graph")
    assert len(graph_resp.json()["edges"]) == 2


@pytest.mark.asyncio
async def test_add_edge_invalid_node(client):
    """POST /processes/{id}/edges returns 400 for nonexistent node reference."""
    await _create_process(client)

    resp = await client.post(
        "/processes/test-linear/edges",
        json={"from_node": "start", "to_node": "nonexistent"},
    )
    assert resp.status_code == 400


# --- Delete edge ---


@pytest.mark.asyncio
async def test_delete_edge(client):
    """DELETE /processes/{id}/edges/{edge_id} removes an edge."""
    await _create_process(client)

    # Get edge IDs from graph
    graph_resp = await client.get("/processes/test-linear/graph")
    edges = graph_resp.json()["edges"]
    edge_id = edges[0]["id"]

    # Add a second end node so the process is still valid after removing the edge
    # Actually, let's just add another edge first, then delete one
    # The simple process has start->end. Removing it will cause validation error
    # (start node has no outbound edges). So we need to work around this.
    # Instead, add a parallel edge, then delete the original.
    await client.post(
        "/processes/test-linear/edges",
        json={"from_node": "start", "to_node": "end", "label": "parallel"},
    )

    resp = await client.delete(f"/processes/test-linear/edges/{edge_id}")
    assert resp.status_code == 204

    # Verify the edge was removed
    graph_resp = await client.get("/processes/test-linear/graph")
    edge_ids = [e["id"] for e in graph_resp.json()["edges"]]
    assert edge_id not in edge_ids


@pytest.mark.asyncio
async def test_delete_edge_not_found(client):
    """DELETE /processes/{id}/edges/{edge_id} returns 404 for missing edge."""
    await _create_process(client)

    resp = await client.delete("/processes/test-linear/edges/nonexistent")
    assert resp.status_code == 404


# --- Position update ---


@pytest.mark.asyncio
async def test_update_position(client):
    """PUT /processes/{id}/nodes/{node_id}/position updates position."""
    await _create_process(client)

    resp = await client.put(
        "/processes/test-linear/nodes/start/position",
        json={"x": 100.5, "y": 200.0},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["metadata"]["position"] == {"x": 100.5, "y": 200.0}

    # Verify persistence
    graph_resp = await client.get("/processes/test-linear/graph")
    start_node = next(
        n for n in graph_resp.json()["nodes"] if n["id"] == "start"
    )
    assert start_node["position"] == {"x": 100.5, "y": 200.0}


@pytest.mark.asyncio
async def test_update_position_not_found(client):
    """PUT /processes/{id}/nodes/{node_id}/position returns 404 for missing node."""
    await _create_process(client)

    resp = await client.put(
        "/processes/test-linear/nodes/nonexistent/position",
        json={"x": 0, "y": 0},
    )
    assert resp.status_code == 404


# --- Validation rollback ---


@pytest.mark.asyncio
async def test_mutation_validation_rollback(client):
    """Invalid mutations don't persist changes (rollback)."""
    await _create_process(client)

    # Try to add a node with bad config — should fail
    resp = await client.post(
        "/processes/test-linear/nodes",
        json={
            "id": "bad",
            "type": "agent",
            "label": "Bad",
            "config": {"not_valid": True},
        },
    )
    assert resp.status_code == 400

    # Process should be unchanged
    graph_resp = await client.get("/processes/test-linear/graph")
    node_ids = [n["id"] for n in graph_resp.json()["nodes"]]
    assert "bad" not in node_ids
