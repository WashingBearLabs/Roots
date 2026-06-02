"""Tests for AgentContext (US-001)."""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from roots import Roots
from roots.agents.context import AgentContext
from roots.core.orchestrator import OrchestrationError
from roots.storage.base import RunRecord
from roots.storage.sqlite import SqliteBackend


ECHO_PROCESS_YAML = """\
id: echo-proc-ctx
name: Echo Process Context
version: "1.0.0"
description: Simple echo process for AgentContext tests
nodes:
  - id: start
    type: agent
    label: Start
    config:
      agent: echo
      output_key: result
  - id: end
    type: end
    label: End
    config:
      status: completed
edges:
  - from: start
    to: end
entry_point: start
"""

FAILING_END_PROCESS_YAML = """\
id: fail-end-proc-ctx
name: Failing End Process Context
version: "1.0.0"
description: Process with failing end node for AgentContext tests
nodes:
  - id: end
    type: end
    label: End
    config:
      status: failed
edges: []
entry_point: end
"""

CHECKPOINT_PROCESS_YAML = """\
id: checkpoint-proc-ctx
name: Checkpoint Process Context
version: "1.0.0"
description: Process that pauses at a checkpoint for AgentContext tests
nodes:
  - id: check
    type: checkpoint
    label: Check
    config:
      prompt: Please review
  - id: end
    type: end
    label: End
    config:
      status: completed
edges:
  - from: check
    to: end
entry_point: check
"""


async def echo_agent(input: dict[str, Any]) -> dict[str, Any]:
    return {"output": {"echo": input["work_item_state"]}, "escalate": False}


@pytest.fixture
async def roots_with_processes() -> AsyncIterator[Roots]:
    backend = SqliteBackend(":memory:")
    await backend.initialize()
    roots = Roots(storage=backend)
    await roots.register_agent("echo", echo_agent)

    for yaml_content in [
        ECHO_PROCESS_YAML,
        FAILING_END_PROCESS_YAML,
        CHECKPOINT_PROCESS_YAML,
    ]:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            path = f.name
        await roots.load_process(path)
        Path(path).unlink()

    yield roots
    await roots.close()


# --- Construction ---


class TestAgentContextConstruction:
    async def test_stores_roots_and_run_id(self) -> None:
        backend = SqliteBackend(":memory:")
        await backend.initialize()
        async with Roots(storage=backend) as roots:
            ctx = AgentContext(roots, "run-abc")
            assert ctx._roots is roots
            assert ctx._run_id == "run-abc"

    async def test_no_admin_methods(self) -> None:
        """AgentContext must not expose admin/mutation methods."""
        backend = SqliteBackend(":memory:")
        await backend.initialize()
        async with Roots(storage=backend) as roots:
            ctx = AgentContext(roots, "run-xyz")
            for admin_method in [
                "load_process",
                "register_agent",
                "register_mcp_server",
                "close",
                "pack_process",
                "install_package",
                "get_run_graph",
            ]:
                assert not hasattr(ctx, admin_method), (
                    f"AgentContext must not expose {admin_method!r}"
                )


# --- start_run ---


class TestStartRun:
    async def test_start_run_delegates_to_roots(
        self, roots_with_processes: Roots
    ) -> None:
        ctx = AgentContext(roots_with_processes, "parent-run")
        run = await ctx.start_run("echo-proc-ctx", {"value": 42})
        assert isinstance(run, RunRecord)
        assert run.process_id == "echo-proc-ctx"
        assert run.status == "pending"

    async def test_start_run_with_metadata(
        self, roots_with_processes: Roots
    ) -> None:
        ctx = AgentContext(roots_with_processes, "parent-run")
        run = await ctx.start_run(
            "echo-proc-ctx", {"value": 1}, metadata={"env": "test"}
        )
        assert isinstance(run, RunRecord)
        assert run.metadata is not None
        assert run.metadata.get("env") == "test"

    async def test_start_run_without_metadata(
        self, roots_with_processes: Roots
    ) -> None:
        ctx = AgentContext(roots_with_processes, "parent-run")
        run = await ctx.start_run("echo-proc-ctx", {"value": 1})
        assert isinstance(run, RunRecord)


# --- get_run ---


class TestGetRun:
    async def test_get_run_returns_record(
        self, roots_with_processes: Roots
    ) -> None:
        ctx = AgentContext(roots_with_processes, "parent-run")
        run = await ctx.start_run("echo-proc-ctx", {"value": 5})
        fetched = await ctx.get_run(run.id)
        assert fetched is not None
        assert fetched.id == run.id

    async def test_get_run_returns_none_for_missing(
        self, roots_with_processes: Roots
    ) -> None:
        ctx = AgentContext(roots_with_processes, "parent-run")
        result = await ctx.get_run("nonexistent-run-id")
        assert result is None

    async def test_get_run_delegates_to_roots(self) -> None:
        backend = SqliteBackend(":memory:")
        await backend.initialize()
        async with Roots(storage=backend) as roots:
            ctx = AgentContext(roots, "parent-run")
            roots.get_run = AsyncMock(return_value=None)  # type: ignore[method-assign]
            await ctx.get_run("some-run-id")
            roots.get_run.assert_called_once_with("some-run-id")


# --- execute_run ---


class TestExecuteRun:
    async def test_execute_run_returns_run_record_on_success(
        self, roots_with_processes: Roots
    ) -> None:
        ctx = AgentContext(roots_with_processes, "parent-run")
        run = await ctx.start_run("echo-proc-ctx", {"msg": "hello"})
        result = await ctx.execute_run(run.id)
        assert isinstance(result, RunRecord)
        assert result.id == run.id
        assert result.status == "completed"

    async def test_execute_run_raises_on_child_failure(
        self, roots_with_processes: Roots
    ) -> None:
        ctx = AgentContext(roots_with_processes, "parent-run")
        run = await ctx.start_run("fail-end-proc-ctx", {})
        with pytest.raises(OrchestrationError, match="failed"):
            await ctx.execute_run(run.id)

    async def test_execute_run_raises_includes_run_id(
        self, roots_with_processes: Roots
    ) -> None:
        ctx = AgentContext(roots_with_processes, "parent-run")
        run = await ctx.start_run("fail-end-proc-ctx", {})
        with pytest.raises(OrchestrationError) as exc_info:
            await ctx.execute_run(run.id)
        assert run.id in str(exc_info.value)

    async def test_execute_run_returns_paused_run(
        self, roots_with_processes: Roots
    ) -> None:
        ctx = AgentContext(roots_with_processes, "parent-run")
        run = await ctx.start_run("checkpoint-proc-ctx", {})
        result = await ctx.execute_run(run.id)
        assert result.status == "paused"


# --- resolve_checkpoint ---


class TestResolveCheckpoint:
    async def test_resolve_checkpoint_delegates_to_roots(self) -> None:
        backend = SqliteBackend(":memory:")
        await backend.initialize()
        async with Roots(storage=backend) as roots:
            ctx = AgentContext(roots, "parent-run")
            roots.resolve_checkpoint = AsyncMock()  # type: ignore[method-assign]
            await ctx.resolve_checkpoint("run-1", "approve", notes="ok")
            roots.resolve_checkpoint.assert_called_once_with(
                "run-1", "approve", notes="ok"
            )

    async def test_resolve_checkpoint_without_notes(self) -> None:
        backend = SqliteBackend(":memory:")
        await backend.initialize()
        async with Roots(storage=backend) as roots:
            ctx = AgentContext(roots, "parent-run")
            roots.resolve_checkpoint = AsyncMock()  # type: ignore[method-assign]
            await ctx.resolve_checkpoint("run-1", "approve")
            roots.resolve_checkpoint.assert_called_once_with(
                "run-1", "approve", notes=None
            )

    async def test_resolve_checkpoint_end_to_end(
        self, roots_with_processes: Roots
    ) -> None:
        ctx = AgentContext(roots_with_processes, "parent-run")
        run = await ctx.start_run("checkpoint-proc-ctx", {})
        paused = await ctx.execute_run(run.id)
        assert paused.status == "paused"

        await ctx.resolve_checkpoint(run.id, "approve")
        await roots_with_processes.execute_run(run.id)
        final = await ctx.get_run(run.id)
        assert final is not None
        assert final.status == "completed"
