"""Tests for US-003: Opt-in context injection mechanism."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from roots import Roots
from roots.agents.context import AgentContext
from roots.agents.invoker import AgentInvoker
from roots.agents.registry import AgentRegistry
from roots.agents.types import AgentInput, AgentRegistration, AgentType, InvocationContext
from roots.storage.sqlite import SqliteBackend


# --- Process YAML for end-to-end tests ---

SEQUENCER_PROCESS_YAML = """\
id: sequencer-proc
name: Sequencer Process
version: "1.0.0"
description: Tests context injection with agent that starts a child run
nodes:
  - id: seq
    type: agent
    label: Sequencer
    config:
      agent: sequencer
      output_key: result
  - id: end
    type: end
    label: End
    config:
      status: completed
edges:
  - from: seq
    to: end
entry_point: seq
"""

CHILD_PROCESS_YAML = """\
id: child-proc
name: Child Process
version: "1.0.0"
description: Child process started by sequencer agent
nodes:
  - id: end
    type: end
    label: End
    config:
      status: completed
edges: []
entry_point: end
"""

NO_CONTEXT_PROCESS_YAML = """\
id: no-context-proc
name: No Context Process
version: "1.0.0"
description: Tests that agents without needs_context receive no context key
nodes:
  - id: step
    type: agent
    label: Step
    config:
      agent: plain-agent
      output_key: result
  - id: end
    type: end
    label: End
    config:
      status: completed
edges:
  - from: step
    to: end
entry_point: step
"""


# --- AgentRegistration validation ---


def _noop_agent(_data: dict[str, Any]) -> dict[str, Any]:
    return {"output": {}}


class TestNeedsContextValidation:
    def test_local_agent_needs_context_true(self) -> None:
        reg = AgentRegistration(
            name="ctx-agent",
            agent_type=AgentType.LOCAL,
            callable=_noop_agent,
            needs_context=True,
        )
        assert reg.needs_context is True

    def test_local_agent_needs_context_default_false(self) -> None:
        reg = AgentRegistration(
            name="plain-agent",
            agent_type=AgentType.LOCAL,
            callable=_noop_agent,
        )
        assert reg.needs_context is False

    def test_remote_agent_needs_context_raises(self) -> None:
        with pytest.raises(ValueError, match="needs_context is only supported for LOCAL agents"):
            AgentRegistration(
                name="remote-ctx",
                agent_type=AgentType.REMOTE,
                callback_url="https://example.com/agent",
                needs_context=True,
            )

    def test_mcp_agent_needs_context_raises(self) -> None:
        with pytest.raises(ValueError, match="needs_context is only supported for LOCAL agents"):
            AgentRegistration(
                name="mcp-ctx",
                agent_type=AgentType.MCP,
                mcp_tool_name="some-tool",
                mcp_server_url="http://localhost:8080",
                needs_context=True,
            )


# --- InvocationContext dataclass ---


class TestInvocationContext:
    def test_fields(self) -> None:
        ctx = InvocationContext(run_id="run-1", owner_id="owner-1", subprocess_depth=0)
        assert ctx.run_id == "run-1"
        assert ctx.owner_id == "owner-1"
        assert ctx.subprocess_depth == 0


# --- AgentInvoker context injection ---


class TestContextInjectionInInvoker:
    @pytest.mark.asyncio
    async def test_agent_with_needs_context_receives_roots_context(self) -> None:
        received: list[dict[str, Any]] = []

        async def capturing_agent(data: dict[str, Any]) -> dict[str, Any]:
            received.append(data)
            return {"output": {"done": True}}

        backend = SqliteBackend(":memory:")
        await backend.initialize()
        async with Roots(storage=backend) as roots:
            await roots.register_agent("ctx-agent", capturing_agent, needs_context=True)
            agent_input = AgentInput(
                work_item_state={"x": 1},
                node_config={},
                run_id="run-abc",
            )
            await roots._agent_invoker.invoke("ctx-agent", agent_input)

        assert len(received) == 1
        assert "_roots_context" in received[0]
        assert isinstance(received[0]["_roots_context"], AgentContext)

    @pytest.mark.asyncio
    async def test_agent_without_needs_context_receives_no_roots_context(self) -> None:
        received: list[dict[str, Any]] = []

        async def plain_agent(data: dict[str, Any]) -> dict[str, Any]:
            received.append(data)
            return {"output": {"done": True}}

        backend = SqliteBackend(":memory:")
        await backend.initialize()
        async with Roots(storage=backend) as roots:
            await roots.register_agent("plain-agent", plain_agent)
            agent_input = AgentInput(
                work_item_state={"x": 1},
                node_config={},
                run_id="run-abc",
            )
            await roots._agent_invoker.invoke("plain-agent", agent_input)

        assert len(received) == 1
        assert "_roots_context" not in received[0]

    @pytest.mark.asyncio
    async def test_context_run_id_matches_agent_input_run_id(self) -> None:
        received: list[AgentContext] = []

        async def capturing_agent(data: dict[str, Any]) -> dict[str, Any]:
            received.append(data["_roots_context"])
            return {"output": {}}

        backend = SqliteBackend(":memory:")
        await backend.initialize()
        async with Roots(storage=backend) as roots:
            await roots.register_agent("ctx-agent", capturing_agent, needs_context=True)
            agent_input = AgentInput(
                work_item_state={},
                node_config={},
                run_id="run-xyz",
            )
            await roots._agent_invoker.invoke("ctx-agent", agent_input)

        assert len(received) == 1
        assert received[0]._run_id == "run-xyz"  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_no_context_injected_without_roots_reference(self) -> None:
        """AgentInvoker constructed without roots= never injects context."""
        received: list[dict[str, Any]] = []

        async def capturing_agent(data: dict[str, Any]) -> dict[str, Any]:
            received.append(data)
            return {"output": {}}

        registry = AgentRegistry()
        registry.register_local("ctx-agent", capturing_agent, needs_context=True)
        invoker = AgentInvoker(registry)  # no roots=

        agent_input = AgentInput(work_item_state={}, node_config={}, run_id="r1")
        await invoker.invoke("ctx-agent", agent_input)

        assert "_roots_context" not in received[0]


# --- register_agent needs_context parameter ---


class TestRegisterAgentNeedsContext:
    @pytest.mark.asyncio
    async def test_register_agent_accepts_needs_context(self) -> None:
        backend = SqliteBackend(":memory:")
        await backend.initialize()
        async with Roots(storage=backend) as roots:
            await roots.register_agent(
                "ctx-agent",
                _noop_agent,
                needs_context=True,
            )
            reg = roots._agent_registry.get("ctx-agent")  # noqa: SLF001
            assert reg is not None
            assert reg.needs_context is True

    @pytest.mark.asyncio
    async def test_register_agent_needs_context_default_false(self) -> None:
        backend = SqliteBackend(":memory:")
        await backend.initialize()
        async with Roots(storage=backend) as roots:
            await roots.register_agent("plain-agent", _noop_agent)
            reg = roots._agent_registry.get("plain-agent")  # noqa: SLF001
            assert reg is not None
            assert reg.needs_context is False


# --- End-to-end: agent calls context.start_run() ---


class TestContextInjectionEndToEnd:
    @pytest.mark.asyncio
    async def test_agent_receives_context_and_calls_start_run(self) -> None:
        child_runs_started: list[str] = []

        async def sequencer_agent(data: dict[str, Any]) -> dict[str, Any]:
            ctx: AgentContext = data["_roots_context"]
            child_run = await ctx.start_run("child-proc", {"msg": "hello"})
            child_runs_started.append(child_run.id)
            return {"output": {"child_run_id": child_run.id}}

        backend = SqliteBackend(":memory:")
        await backend.initialize()
        roots = Roots(storage=backend)

        await roots.register_agent("sequencer", sequencer_agent, needs_context=True)

        for yaml_content in [SEQUENCER_PROCESS_YAML, CHILD_PROCESS_YAML]:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
                f.write(yaml_content)
                f.flush()
                path = f.name
            await roots.load_process(path)
            Path(path).unlink()

        run = await roots.start_run("sequencer-proc", {"input": "data"})
        await roots.execute_run(run.id)
        await roots.close()

        assert len(child_runs_started) == 1
        child_run_id = child_runs_started[0]
        assert child_run_id is not None

    @pytest.mark.asyncio
    async def test_agent_without_context_not_affected(self) -> None:
        invocations: list[dict[str, Any]] = []

        async def plain_agent(data: dict[str, Any]) -> dict[str, Any]:
            invocations.append(data)
            return {"output": {"done": True}}

        backend = SqliteBackend(":memory:")
        await backend.initialize()
        roots = Roots(storage=backend)

        await roots.register_agent("plain-agent", plain_agent)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(NO_CONTEXT_PROCESS_YAML)
            f.flush()
            path = f.name
        await roots.load_process(path)
        Path(path).unlink()

        run = await roots.start_run("no-context-proc", {"x": 1})
        await roots.execute_run(run.id)
        await roots.close()

        assert len(invocations) == 1
        assert "_roots_context" not in invocations[0]

    @pytest.mark.asyncio
    async def test_injected_context_run_id_matches_current_run(self) -> None:
        context_run_ids: list[str] = []

        async def run_id_capture_agent(data: dict[str, Any]) -> dict[str, Any]:
            ctx: AgentContext = data["_roots_context"]
            context_run_ids.append(ctx._run_id)  # noqa: SLF001
            return {"output": {}}

        backend = SqliteBackend(":memory:")
        await backend.initialize()
        roots = Roots(storage=backend)

        await roots.register_agent(
            "sequencer", run_id_capture_agent, needs_context=True
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(SEQUENCER_PROCESS_YAML)
            f.flush()
            path = f.name
        await roots.load_process(path)
        Path(path).unlink()

        run = await roots.start_run("sequencer-proc", {})
        await roots.execute_run(run.id)
        await roots.close()

        assert len(context_run_ids) == 1
        assert context_run_ids[0] == run.id
