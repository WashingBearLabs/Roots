"""Tests for Decision History API Routes (US-004)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from roots import Roots
from roots.api.app import create_app
from roots.storage.sqlite import SqliteBackend


@pytest.fixture
async def app_with_decisions():
    backend = SqliteBackend(":memory:")
    await backend.initialize()
    roots = Roots(storage=backend)
    application = create_app(roots)

    run = await backend.create_run("proc-1", {})
    await backend.append_decision(
        run.id, "proc-1", "node-a", "ai_bounded", {"state": 1}, {"selected_edge": "yes"}, 0.9
    )
    await backend.append_decision(
        run.id, "proc-1", "node-a", "manual", {"state": 2}, {"selected_edge": "no"}, 0.7
    )
    await backend.append_decision(
        run.id, "proc-1", "node-b", "ai_bounded", {"state": 3}, {"selected_edge": "yes"}, 0.8
    )

    yield application, run.id
    await roots.close()


@pytest.fixture
async def client(app_with_decisions):
    application, run_id = app_with_decisions
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, run_id


@pytest.mark.asyncio
async def test_list_decisions_no_filters(client):
    """GET /processes/{process_id}/decisions returns all decisions for the process."""
    c, _ = client
    resp = await c.get("/processes/proc-1/decisions")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3


@pytest.mark.asyncio
async def test_list_decisions_filter_by_node_id(client):
    """node_id query param filters decisions to a single node."""
    c, _ = client
    resp = await c.get("/processes/proc-1/decisions?node_id=node-a")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert all(d["node_id"] == "node-a" for d in data)


@pytest.mark.asyncio
async def test_list_decisions_filter_by_run_id(client):
    """run_id query param filters decisions to a single run."""
    c, run_id = client
    resp = await c.get(f"/processes/proc-1/decisions?run_id={run_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    assert all(d["run_id"] == run_id for d in data)


@pytest.mark.asyncio
async def test_list_decisions_filter_by_mode(client):
    """mode query param filters decisions by decision mode."""
    c, _ = client
    resp = await c.get("/processes/proc-1/decisions?mode=ai_bounded")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert all(d["mode"] == "ai_bounded" for d in data)


@pytest.mark.asyncio
async def test_list_decisions_limit(client):
    """limit query param restricts the number of results."""
    c, _ = client
    resp = await c.get("/processes/proc-1/decisions?limit=1")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1


@pytest.mark.asyncio
async def test_list_decisions_combined_filters(client):
    """node_id and mode can be combined."""
    c, _ = client
    resp = await c.get("/processes/proc-1/decisions?node_id=node-a&mode=manual")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["node_id"] == "node-a"
    assert data[0]["mode"] == "manual"


@pytest.mark.asyncio
async def test_list_decisions_empty_result_not_404(client):
    """Returns empty list (not 404) when no decisions match filters."""
    c, _ = client
    resp = await c.get("/processes/proc-1/decisions?node_id=nonexistent")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_decisions_unknown_process_returns_empty(client):
    """Returns empty list for a process with no decisions."""
    c, _ = client
    resp = await c.get("/processes/no-such-proc/decisions")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_decision_response_schema(client):
    """Response includes all required DecisionHistoryResponse fields."""
    c, _ = client
    resp = await c.get("/processes/proc-1/decisions?node_id=node-a&limit=1")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    record = data[0]
    assert "id" in record
    assert "run_id" in record
    assert "process_id" in record
    assert "node_id" in record
    assert "mode" in record
    assert "decision" in record
    assert "confidence" in record
    assert "created_at" in record
    assert record["process_id"] == "proc-1"
