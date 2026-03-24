"""Tests for Roots embedded API — Graph and Resolution (US-008)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from roots import Roots, SqliteBackend
from roots.core.orchestrator import OrchestrationError


# --- Helpers ---

CHECKPOINT_PROCESS_YAML = """\
id: test-checkpoint
name: Checkpoint Process
version: "1.0.0"
description: A process with a checkpoint
nodes:
  - id: agent1
    type: agent
    label: First Agent
    config:
      agent: echo
      output_key: step1
  - id: review
    type: checkpoint
    label: Human Review
    config:
      prompt: "Please review the output"
  - id: agent2
    type: agent
    label: Second Agent
    config:
      agent: echo
      output_key: step2
  - id: alt_node
    type: agent
    label: Alt Node
    config:
      agent: echo
      output_key: alt_result
  - id: end
    type: end
    label: End
    config:
      status: completed
  - id: alt_end
    type: end
    label: Alt End
    config:
      status: completed
edges:
  - from: agent1
    to: review
  - from: review
    to: agent2
  - from: agent2
    to: end
  - from: alt_node
    to: alt_end
entry_point: agent1
"""

SIMPLE_PROCESS_YAML = """\
id: test-linear
name: Linear Process
version: "1.0.0"
nodes:
  - id: start
    type: agent
    label: Start
    config:
      agent: echo
      output_key: result
  - id: middle
    type: agent
    label: Middle
    config:
      agent: echo
      output_key: mid_result
  - id: end
    type: end
    label: End
    config:
      status: completed
edges:
  - from: start
    to: middle
  - from: middle
    to: end
entry_point: start
"""


async def echo_agent(input: dict) -> dict:
    return {"output": {"echo": input["work_item_state"]}, "escalate": False}


async def _setup_roots(yaml_content: str) -> tuple[Roots, str, str]:
    """Create a Roots instance with a process loaded from YAML. Returns (roots, yaml_path, process_id)."""
    backend = SqliteBackend(":memory:")
    await backend.initialize()
    roots = Roots(storage=backend)
    await roots.register_agent("echo", echo_agent)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    ) as f:
        f.write(yaml_content)
        f.flush()
        yaml_path = f.name

    await roots.load_process(yaml_path)
    return roots, yaml_path, ""


# --- get_run_graph Tests ---


@pytest.mark.asyncio
async def test_graph_structure_completed_run():
    """get_run_graph returns correct JSON structure for a completed run."""
    roots, yaml_path, _ = await _setup_roots(SIMPLE_PROCESS_YAML)
    try:
        run = await roots.start_run("test-linear", {"val": 1})
        await roots.execute_run(run.id)

        graph = await roots.get_run_graph(run.id)

        assert graph["process_id"] == "test-linear"
        assert graph["run_id"] == run.id
        assert graph["run_status"] == "completed"
        assert len(graph["nodes"]) == 3
        assert len(graph["edges"]) == 2

        # Check node fields
        node = graph["nodes"][0]
        assert "id" in node
        assert "type" in node
        assert "label" in node
        assert "status" in node
        assert "started_at" in node
        assert "completed_at" in node
        assert "position" in node
        assert "metadata" in node
    finally:
        await roots.close()
        Path(yaml_path).unlink()


@pytest.mark.asyncio
async def test_graph_node_statuses_partially_completed():
    """Node statuses derived correctly from execution history for a partially-completed run."""
    roots, yaml_path, _ = await _setup_roots(CHECKPOINT_PROCESS_YAML)
    try:
        run = await roots.start_run("test-checkpoint", {"data": "test"})
        # execute_run will run agent1, then hit the checkpoint and pause
        await roots.execute_run(run.id)

        graph = await roots.get_run_graph(run.id)

        # Run should be paused at the checkpoint
        assert graph["run_status"] == "paused"

        node_map = {n["id"]: n for n in graph["nodes"]}

        # agent1 should be completed
        assert node_map["agent1"]["status"] == "completed"
        assert node_map["agent1"]["started_at"] is not None
        assert node_map["agent1"]["completed_at"] is not None

        # review (checkpoint) should be paused (current node, run is paused)
        assert node_map["review"]["status"] == "paused"

        # agent2 should be pending (no events)
        assert node_map["agent2"]["status"] == "pending"

        # end should be pending
        assert node_map["end"]["status"] == "pending"
    finally:
        await roots.close()
        Path(yaml_path).unlink()


@pytest.mark.asyncio
async def test_graph_edge_statuses():
    """Edge statuses derived from traversal history."""
    roots, yaml_path, _ = await _setup_roots(SIMPLE_PROCESS_YAML)
    try:
        run = await roots.start_run("test-linear", {"val": 1})
        await roots.execute_run(run.id)

        graph = await roots.get_run_graph(run.id)

        edge_map = {(e["from"], e["to"]): e for e in graph["edges"]}

        # Both edges should be traversed in a completed run
        assert edge_map[("start", "middle")]["status"] == "traversed"
        assert edge_map[("middle", "end")]["status"] == "traversed"
    finally:
        await roots.close()
        Path(yaml_path).unlink()


@pytest.mark.asyncio
async def test_graph_edge_pending_for_untraversed():
    """Edges to unvisited nodes show 'pending' status."""
    roots, yaml_path, _ = await _setup_roots(CHECKPOINT_PROCESS_YAML)
    try:
        run = await roots.start_run("test-checkpoint", {"data": "x"})
        await roots.execute_run(run.id)

        graph = await roots.get_run_graph(run.id)
        edge_map = {(e["from"], e["to"]): e for e in graph["edges"]}

        # agent1 → review: both have events, so traversed
        assert edge_map[("agent1", "review")]["status"] == "traversed"

        # review → agent2: agent2 has no events, so pending
        assert edge_map[("review", "agent2")]["status"] == "pending"
    finally:
        await roots.close()
        Path(yaml_path).unlink()


@pytest.mark.asyncio
async def test_graph_default_position():
    """Nodes without position metadata default to {x: 0, y: 0}."""
    roots, yaml_path, _ = await _setup_roots(SIMPLE_PROCESS_YAML)
    try:
        run = await roots.start_run("test-linear", {"val": 1})
        graph = await roots.get_run_graph(run.id)

        for node in graph["nodes"]:
            assert node["position"] == {"x": 0, "y": 0}
    finally:
        await roots.close()
        Path(yaml_path).unlink()


@pytest.mark.asyncio
async def test_graph_nonexistent_run():
    """get_run_graph raises for nonexistent run."""
    backend = SqliteBackend(":memory:")
    await backend.initialize()
    roots = Roots(storage=backend)
    try:
        with pytest.raises(OrchestrationError, match="not found"):
            await roots.get_run_graph("nonexistent-run")
    finally:
        await roots.close()


# --- resolve_checkpoint Tests ---


@pytest.mark.asyncio
async def test_resolve_checkpoint_approve():
    """Approve resumes run and advances to next node."""
    roots, yaml_path, _ = await _setup_roots(CHECKPOINT_PROCESS_YAML)
    try:
        run = await roots.start_run("test-checkpoint", {"data": "test"})
        await roots.execute_run(run.id)

        # Verify paused at checkpoint
        paused = await roots.get_run(run.id)
        assert paused is not None
        assert paused.status == "paused"
        assert paused.current_node_id == "review"

        # Approve
        await roots.resolve_checkpoint(run.id, "approve", notes="Looks good")

        # Run should now be running with current_node = agent2
        resumed = await roots.get_run(run.id)
        assert resumed is not None
        assert resumed.status == "running"
        assert resumed.current_node_id == "agent2"

        # Checkpoint should be resolved
        cp = await roots.storage.get_pending_checkpoint(run.id)
        assert cp is None
    finally:
        await roots.close()
        Path(yaml_path).unlink()


@pytest.mark.asyncio
async def test_resolve_checkpoint_reject():
    """Reject fails the run."""
    roots, yaml_path, _ = await _setup_roots(CHECKPOINT_PROCESS_YAML)
    try:
        run = await roots.start_run("test-checkpoint", {"data": "test"})
        await roots.execute_run(run.id)

        await roots.resolve_checkpoint(run.id, "reject", notes="Not acceptable")

        failed = await roots.get_run(run.id)
        assert failed is not None
        assert failed.status == "failed"
    finally:
        await roots.close()
        Path(yaml_path).unlink()


@pytest.mark.asyncio
async def test_resolve_checkpoint_redirect():
    """Redirect sends run to specified node."""
    roots, yaml_path, _ = await _setup_roots(CHECKPOINT_PROCESS_YAML)
    try:
        run = await roots.start_run("test-checkpoint", {"data": "test"})
        await roots.execute_run(run.id)

        await roots.resolve_checkpoint(
            run.id, "redirect", redirect_to="alt_node"
        )

        redirected = await roots.get_run(run.id)
        assert redirected is not None
        assert redirected.status == "running"
        assert redirected.current_node_id == "alt_node"
    finally:
        await roots.close()
        Path(yaml_path).unlink()


@pytest.mark.asyncio
async def test_resolve_checkpoint_redirect_invalid_node():
    """Redirect to invalid node raises error."""
    roots, yaml_path, _ = await _setup_roots(CHECKPOINT_PROCESS_YAML)
    try:
        run = await roots.start_run("test-checkpoint", {"data": "test"})
        await roots.execute_run(run.id)

        with pytest.raises(OrchestrationError, match="does not exist in process"):
            await roots.resolve_checkpoint(
                run.id, "redirect", redirect_to="nonexistent"
            )
    finally:
        await roots.close()
        Path(yaml_path).unlink()


@pytest.mark.asyncio
async def test_resolve_checkpoint_redirect_missing_target():
    """Redirect without redirect_to raises error."""
    roots, yaml_path, _ = await _setup_roots(CHECKPOINT_PROCESS_YAML)
    try:
        run = await roots.start_run("test-checkpoint", {"data": "test"})
        await roots.execute_run(run.id)

        with pytest.raises(OrchestrationError, match="redirect_to is required"):
            await roots.resolve_checkpoint(run.id, "redirect")
    finally:
        await roots.close()
        Path(yaml_path).unlink()


@pytest.mark.asyncio
async def test_resolve_no_pending_checkpoint():
    """Resolving when no pending checkpoint raises error."""
    roots, yaml_path, _ = await _setup_roots(SIMPLE_PROCESS_YAML)
    try:
        run = await roots.start_run("test-linear", {"val": 1})
        await roots.execute_run(run.id)

        with pytest.raises(OrchestrationError, match="No pending checkpoint"):
            await roots.resolve_checkpoint(run.id, "approve")
    finally:
        await roots.close()
        Path(yaml_path).unlink()


@pytest.mark.asyncio
async def test_resolve_checkpoint_then_continue():
    """After approve, run can continue to completion."""
    roots, yaml_path, _ = await _setup_roots(CHECKPOINT_PROCESS_YAML)
    try:
        run = await roots.start_run("test-checkpoint", {"data": "test"})
        await roots.execute_run(run.id)

        # Approve checkpoint
        await roots.resolve_checkpoint(run.id, "approve")

        # Continue execution
        await roots.execute_run(run.id)

        completed = await roots.get_run(run.id)
        assert completed is not None
        assert completed.status == "completed"
    finally:
        await roots.close()
        Path(yaml_path).unlink()
