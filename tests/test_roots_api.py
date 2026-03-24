"""Tests for Roots embedded API (US-007)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from roots import Roots, SqliteBackend, PostgresBackend, StdoutSink, FileSink, HttpSink
from roots.storage.base import RunRecord


# --- Helpers ---

SIMPLE_PROCESS_YAML = """\
id: test-echo
name: Echo Process
version: "1.0.0"
description: A simple echo process
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


async def echo_agent(input: dict) -> dict:
    return {"output": {"echo": input["work_item_state"]}, "escalate": False}


# --- Tests ---


@pytest.mark.asyncio
async def test_instantiate_cleanly():
    """Roots(storage=SqliteBackend(':memory:')) instantiates cleanly."""
    backend = SqliteBackend(":memory:")
    await backend.initialize()
    roots = Roots(storage=backend)
    assert roots.storage is backend
    await roots.close()


@pytest.mark.asyncio
async def test_load_process():
    """load_process parses and stores a YAML process."""
    backend = SqliteBackend(":memory:")
    await backend.initialize()
    async with Roots(storage=backend) as roots:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(SIMPLE_PROCESS_YAML)
            f.flush()
            yaml_path = f.name

        await roots.load_process(yaml_path)
        process = await backend.get_process("test-echo")
        assert process is not None
        assert process.name == "Echo Process"
        Path(yaml_path).unlink()


@pytest.mark.asyncio
async def test_register_agent():
    """register_agent registers a local callable."""
    backend = SqliteBackend(":memory:")
    await backend.initialize()
    async with Roots(storage=backend) as roots:
        await roots.register_agent("test-agent", echo_agent)
        reg = roots._agent_registry.get("test-agent")
        assert reg is not None
        assert reg.name == "test-agent"


@pytest.mark.asyncio
async def test_start_and_execute_run():
    """start_run + execute_run drives a run to completion."""
    backend = SqliteBackend(":memory:")
    await backend.initialize()
    async with Roots(storage=backend) as roots:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(SIMPLE_PROCESS_YAML)
            f.flush()
            yaml_path = f.name

        await roots.load_process(yaml_path)
        await roots.register_agent("echo", echo_agent)
        run = await roots.start_run("test-echo", {"message": "hello"})
        assert isinstance(run, RunRecord)
        await roots.execute_run(run.id)

        completed = await roots.get_run(run.id)
        assert completed is not None
        assert completed.status == "completed"
        Path(yaml_path).unlink()


@pytest.mark.asyncio
async def test_public_imports():
    """All public types importable from roots package."""
    assert Roots is not None
    assert SqliteBackend is not None
    assert PostgresBackend is not None
    assert StdoutSink is not None
    assert FileSink is not None
    assert HttpSink is not None


@pytest.mark.asyncio
async def test_close_drains_events():
    """close() drains events and closes storage."""
    backend = SqliteBackend(":memory:")
    await backend.initialize()
    roots = Roots(storage=backend, event_sinks=[StdoutSink(compact=True)])

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    ) as f:
        f.write(SIMPLE_PROCESS_YAML)
        f.flush()
        yaml_path = f.name

    await roots.load_process(yaml_path)
    await roots.register_agent("echo", echo_agent)
    run = await roots.start_run("test-echo", {"data": "test"})
    await roots.execute_run(run.id)
    await roots.close()
    # Storage should be closed — db is None
    assert backend._db is None
    Path(yaml_path).unlink()


@pytest.mark.asyncio
async def test_async_context_manager():
    """Works as async context manager."""
    backend = SqliteBackend(":memory:")
    await backend.initialize()
    async with Roots(storage=backend) as roots:
        assert roots.storage is backend
    # After exiting, storage should be closed
    assert backend._db is None


@pytest.mark.asyncio
async def test_roots_instance_fixture(roots_instance):
    """roots_instance fixture from conftest.py works."""
    assert roots_instance is not None
    reg = roots_instance._agent_registry.get("echo")
    assert reg is not None


@pytest.mark.asyncio
async def test_end_to_end():
    """End-to-end: 5-line script — load process, register agent, run to completion."""
    backend = SqliteBackend(":memory:")
    await backend.initialize()
    async with Roots(storage=backend) as roots:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(SIMPLE_PROCESS_YAML)
            f.flush()
            yaml_path = f.name

        await roots.load_process(yaml_path)                          # 1. load
        await roots.register_agent("echo", echo_agent)               # 2. register
        run = await roots.start_run("test-echo", {"value": 42})      # 3. start
        await roots.execute_run(run.id)                               # 4. execute
        result = await roots.get_run(run.id)                          # 5. check
        assert result is not None
        assert result.status == "completed"
        assert result.work_item_state["result"]["echo"]["value"] == 42
        Path(yaml_path).unlink()
