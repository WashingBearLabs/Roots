"""Tests for Checkpoint and Escalation Resolution Routes (US-005)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from roots import Roots
from roots.api.app import create_app
from roots.storage.sqlite import SqliteBackend


VALID_PROCESS_DEF = {
    "id": "proc-1",
    "name": "Test Process",
    "version": "1.0.0",
    "description": "A process with a checkpoint",
    "nodes": [
        {
            "id": "start",
            "type": "agent",
            "label": "Start",
            "config": {"agent": "echo", "output_key": "result"},
        },
        {
            "id": "review",
            "type": "checkpoint",
            "label": "Review",
            "config": {"prompt": "Please review"},
        },
        {
            "id": "end",
            "type": "end",
            "label": "End",
            "config": {"status": "completed"},
        },
        {
            "id": "alt",
            "type": "agent",
            "label": "Alt",
            "config": {"agent": "echo", "output_key": "result"},
        },
    ],
    "edges": [
        {"from": "start", "to": "review"},
        {"from": "review", "to": "end"},
        {"from": "alt", "to": "end"},
    ],
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


async def _setup_run_with_checkpoint(roots_instance: Roots) -> str:
    """Create a process, run, and pending checkpoint. Returns run_id."""
    from roots.core.validator import parse_process_dict

    process = parse_process_dict(VALID_PROCESS_DEF)
    await roots_instance.storage.save_process(process)
    run = await roots_instance.start_run("proc-1", {"key": "value"})
    await roots_instance.storage.update_run_status(run.id, "waiting")
    await roots_instance.storage.create_checkpoint(
        run_id=run.id,
        node_id="review",
        checkpoint_type="planned",
        prompt="Please review",
    )
    return run.id


async def _setup_run_with_escalation(roots_instance: Roots) -> str:
    """Create a process, run, and pending escalation. Returns run_id."""
    from roots.core.validator import parse_process_dict

    process = parse_process_dict(VALID_PROCESS_DEF)
    await roots_instance.storage.save_process(process)
    run = await roots_instance.start_run("proc-1", {"key": "value"})
    await roots_instance.storage.update_run_status(run.id, "waiting")
    await roots_instance.storage.create_escalation(
        run_id=run.id,
        node_id="review",
        trigger_type="confidence_low",
        reason="Low confidence score",
        work_item_snapshot={"key": "value"},
    )
    return run.id


# --- GET /runs/{run_id}/checkpoint ---


@pytest.mark.asyncio
async def test_get_pending_checkpoint(client, roots_instance):
    """GET returns pending checkpoint details."""
    run_id = await _setup_run_with_checkpoint(roots_instance)

    resp = await client.get(f"/runs/{run_id}/checkpoint")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == run_id
    assert data["node_id"] == "review"
    assert data["type"] == "planned"
    assert data["prompt"] == "Please review"
    assert data["status"] == "pending"
    assert "id" in data


@pytest.mark.asyncio
async def test_get_pending_escalation(client, roots_instance):
    """GET returns pending escalation details when no checkpoint."""
    run_id = await _setup_run_with_escalation(roots_instance)

    resp = await client.get(f"/runs/{run_id}/checkpoint")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == run_id
    assert data["node_id"] == "review"
    assert data["type"] == "confidence_low"
    assert data["prompt"] == "Low confidence score"
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_get_checkpoint_404_when_nothing_pending(client, roots_instance):
    """GET returns 404 when no pending checkpoint or escalation."""
    from roots.core.validator import parse_process_dict

    process = parse_process_dict(VALID_PROCESS_DEF)
    await roots_instance.storage.save_process(process)
    run = await roots_instance.start_run("proc-1", {"key": "value"})

    resp = await client.get(f"/runs/{run.id}/checkpoint")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_checkpoint_404_for_unknown_run(client):
    """GET returns 404 for a non-existent run."""
    resp = await client.get("/runs/nonexistent/checkpoint")
    assert resp.status_code == 404


# --- POST /runs/{run_id}/checkpoint — Approve ---


@pytest.mark.asyncio
async def test_approve_checkpoint_resumes_run(client, roots_instance):
    """Approve resolves checkpoint and sets run to running."""
    run_id = await _setup_run_with_checkpoint(roots_instance)

    resp = await client.post(
        f"/runs/{run_id}/checkpoint",
        json={"decision": "approve"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "resolved"

    run = await roots_instance.storage.get_run(run_id)
    assert run.status == "running"


# --- POST /runs/{run_id}/checkpoint — Reject ---


@pytest.mark.asyncio
async def test_reject_checkpoint_fails_run(client, roots_instance):
    """Reject resolves checkpoint and fails the run."""
    run_id = await _setup_run_with_checkpoint(roots_instance)

    resp = await client.post(
        f"/runs/{run_id}/checkpoint",
        json={"decision": "reject", "notes": "Not acceptable"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "resolved"

    run = await roots_instance.storage.get_run(run_id)
    assert run.status == "failed"


# --- POST /runs/{run_id}/checkpoint — Redirect ---


@pytest.mark.asyncio
async def test_redirect_resumes_from_specified_node(client, roots_instance):
    """Redirect resolves checkpoint and resumes from the specified node."""
    run_id = await _setup_run_with_checkpoint(roots_instance)

    resp = await client.post(
        f"/runs/{run_id}/checkpoint",
        json={"decision": "redirect", "redirect_to": "alt"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "resolved"

    run = await roots_instance.storage.get_run(run_id)
    assert run.status == "running"
    assert run.current_node_id == "alt"


@pytest.mark.asyncio
async def test_redirect_missing_redirect_to_returns_422(client, roots_instance):
    """Redirect without redirect_to returns 422."""
    run_id = await _setup_run_with_checkpoint(roots_instance)

    resp = await client.post(
        f"/runs/{run_id}/checkpoint",
        json={"decision": "redirect"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_redirect_invalid_node_returns_400(client, roots_instance):
    """Redirect to a non-existent node returns 400."""
    run_id = await _setup_run_with_checkpoint(roots_instance)

    resp = await client.post(
        f"/runs/{run_id}/checkpoint",
        json={"decision": "redirect", "redirect_to": "nonexistent"},
    )
    assert resp.status_code == 400


# --- Escalation resolution ---


@pytest.mark.asyncio
async def test_approve_escalation_resumes_run(client, roots_instance):
    """Approve resolves escalation and resumes the run."""
    run_id = await _setup_run_with_escalation(roots_instance)

    resp = await client.post(
        f"/runs/{run_id}/checkpoint",
        json={"decision": "approve"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "resolved"

    run = await roots_instance.storage.get_run(run_id)
    assert run.status == "running"
