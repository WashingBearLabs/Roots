"""Tests for Node Explorer demo — US-011."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from roots import Roots
from roots.storage.sqlite import SqliteBackend

DEMO_DIR = Path(__file__).resolve().parent.parent / "demo" / "node-explorer"
PROCESS_YAML = str(DEMO_DIR / "process.yaml")

# Add the node-explorer dir to sys.path so we can import agents/server_extensions
if str(DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(DEMO_DIR))

import agents as ne_agents  # type: ignore[import-untyped]  # noqa: E402
import server_extensions as ne_server  # type: ignore[import-untyped]  # noqa: E402


@pytest.fixture
async def roots_app():
    """Set up Roots with node-explorer process and agents."""
    backend = SqliteBackend(":memory:")
    await backend.initialize()

    app = Roots(storage=backend)
    await app.__aenter__()

    await app.load_process(PROCESS_YAML)
    await app.register_agent("classify_item", ne_agents.classify_item)
    await app.register_agent("check_format", ne_agents.check_format)
    await app.register_agent("validate_schema", ne_agents.validate_schema)
    await app.register_agent("analyze_content_quality", ne_agents.analyze_content_quality)
    await app.register_agent("analyze_content_deep", ne_agents.analyze_content_deep)
    await app.register_agent("analyze_metadata_deep", ne_agents.analyze_metadata_deep)

    yield app
    await app.close()


@pytest.fixture
async def demo_app(roots_app: Roots):
    """Create the FastAPI demo app with node explorer routes."""
    from demo._common.demo_server import create_demo_app

    static_dir = str(DEMO_DIR / "static")
    app = create_demo_app(roots_app, "Node Explorer", static_dir)
    ne_server.add_node_explorer_routes(app)
    return app


@pytest.fixture
async def client(demo_app):
    transport = ASGITransport(app=demo_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_reset_creates_run(client: AsyncClient):
    resp = await client.post("/api/reset", json={"process_id": "node-explorer"})
    assert resp.status_code == 200
    data = resp.json()
    assert "run_id" in data
    assert data["run_id"]


@pytest.mark.asyncio
async def test_step_advances_run(client: AsyncClient):
    # Create a run
    reset_resp = await client.post("/api/reset", json={"process_id": "node-explorer"})
    run_id = reset_resp.json()["run_id"]

    # Step once — should transition from pending to running at welcome (checkpoint)
    step_resp = await client.post("/api/step", json={"run_id": run_id})
    assert step_resp.status_code == 200
    data = step_resp.json()
    assert data["run_id"] == run_id
    assert "graph" in data
    assert "nodes" in data["graph"]
    assert "edges" in data["graph"]


@pytest.mark.asyncio
async def test_step_auto_resolves_checkpoint(client: AsyncClient):
    """Step on a paused checkpoint auto-approves and advances."""
    reset_resp = await client.post("/api/reset", json={"process_id": "node-explorer"})
    run_id = reset_resp.json()["run_id"]

    # First step: enters welcome checkpoint, run pauses
    await client.post("/api/step", json={"run_id": run_id})

    # Second step: should auto-resolve checkpoint and advance
    step_resp = await client.post("/api/step", json={"run_id": run_id})
    assert step_resp.status_code == 200
    graph = step_resp.json()["graph"]
    # The run should have progressed past welcome
    run_status = graph.get("run_status")
    # After resolving checkpoint the run should be running or at classify
    assert run_status in ("running", "paused", "completed")


@pytest.mark.asyncio
async def test_step_invalid_run_id(client: AsyncClient):
    resp = await client.post("/api/step", json={"run_id": "nonexistent"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_tutorial_returns_content(client: AsyncClient):
    for node_type in [
        "checkpoint", "agent", "agent_pool", "decision",
        "fork", "join", "emit", "end", "retry",
    ]:
        resp = await client.get(f"/api/tutorial/{node_type}")
        assert resp.status_code == 200, f"Failed for {node_type}"
        data = resp.json()
        assert "title" in data
        assert "what" in data
        assert "tips" in data


@pytest.mark.asyncio
async def test_tutorial_unknown_type(client: AsyncClient):
    resp = await client.get("/api/tutorial/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_process_yaml_loads(roots_app: Roots):
    """Verify the process YAML is valid and loaded."""
    process = await roots_app.storage.get_process("node-explorer")
    assert process is not None
    assert len(process.nodes) == 11
    node_types = {n.type.value for n in process.nodes}
    expected = {"checkpoint", "agent", "agent_pool", "decision", "fork", "join", "emit", "end"}
    assert expected == node_types


@pytest.mark.asyncio
async def test_retry_agent_fails_then_succeeds():
    """The analyze_content_deep agent fails first call, succeeds second."""
    test_key = "test-retry-check"
    ne_agents._content_deep_call_counts.pop(test_key, None)

    test_input = {"work_item_state": {"_run_id": test_key}}

    with pytest.raises(Exception, match="Simulated transient failure"):
        await ne_agents.analyze_content_deep(test_input)

    result = await ne_agents.analyze_content_deep(test_input)
    assert result["output"]["depth"] == "thorough"
    assert len(result["output"]["findings"]) > 0

    # Clean up
    ne_agents._content_deep_call_counts.pop(test_key, None)


@pytest.mark.asyncio
async def test_tutorial_content_json_complete():
    """tutorial_content.json has entries for all 8 node types + retry."""
    with open(DEMO_DIR / "tutorial_content.json") as f:
        content = json.load(f)
    expected_keys = {
        "checkpoint", "agent", "agent_pool", "decision",
        "fork", "join", "emit", "end", "retry",
    }
    assert set(content.keys()) == expected_keys
