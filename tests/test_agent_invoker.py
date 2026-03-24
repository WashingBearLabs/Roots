"""Tests for the agent invoker."""

import pytest

from roots.agents.invoker import (
    AgentInvocationError,
    AgentInvoker,
    AgentNotFoundError,
)
from roots.agents.registry import AgentRegistry
from roots.agents.types import AgentInput, AgentType


def _make_input() -> AgentInput:
    return AgentInput(
        work_item_state={"key": "value"},
        node_config={"step": 1},
        run_id="run-001",
    )


def _sync_agent(data: dict) -> dict:
    return {"output": {"echo": data["work_item_state"]}}


async def _async_agent(data: dict) -> dict:
    return {"output": {"echo": data["work_item_state"]}}


def _failing_agent(data: dict) -> dict:
    raise RuntimeError("boom")


async def _async_failing_agent(data: dict) -> dict:
    raise RuntimeError("async boom")


def _escalating_agent(data: dict) -> dict:
    return {
        "output": {"result": "needs help"},
        "escalate": True,
        "escalation_reason": "too complex",
    }


def _make_registry_with(name: str, fn) -> AgentRegistry:
    registry = AgentRegistry()
    registry.register_local(name, fn)
    return registry


class TestInvokeSyncCallable:
    @pytest.mark.asyncio
    async def test_sync_callable_returns_agent_output(self) -> None:
        registry = _make_registry_with("sync-agent", _sync_agent)
        invoker = AgentInvoker(registry)
        result = await invoker.invoke("sync-agent", _make_input())
        assert result.output == {"echo": {"key": "value"}}
        assert result.escalate is False
        assert result.escalation_reason is None

    @pytest.mark.asyncio
    async def test_sync_callable_does_not_block_event_loop(self) -> None:
        """Sync callables should be run via asyncio.to_thread."""
        import asyncio
        import time

        call_thread_ids: list[int] = []

        def _slow_agent(data: dict) -> dict:
            import threading

            call_thread_ids.append(threading.current_thread().ident or 0)
            time.sleep(0.01)
            return {"output": {"done": True}}

        registry = _make_registry_with("slow-agent", _slow_agent)
        invoker = AgentInvoker(registry)

        loop = asyncio.get_event_loop()
        main_thread_id = id(loop)

        result = await invoker.invoke("slow-agent", _make_input())
        assert result.output == {"done": True}
        # The callable ran in a different thread (not main)
        assert len(call_thread_ids) == 1


class TestInvokeAsyncCallable:
    @pytest.mark.asyncio
    async def test_async_callable_returns_agent_output(self) -> None:
        registry = _make_registry_with("async-agent", _async_agent)
        invoker = AgentInvoker(registry)
        result = await invoker.invoke("async-agent", _make_input())
        assert result.output == {"echo": {"key": "value"}}
        assert result.escalate is False

    @pytest.mark.asyncio
    async def test_async_callable_with_escalation(self) -> None:
        async def _async_escalating(data: dict) -> dict:
            return {
                "output": {"result": "needs help"},
                "escalate": True,
                "escalation_reason": "too complex",
            }

        registry = _make_registry_with("esc-agent", _async_escalating)
        invoker = AgentInvoker(registry)
        result = await invoker.invoke("esc-agent", _make_input())
        assert result.escalate is True
        assert result.escalation_reason == "too complex"


class TestExceptionHandling:
    @pytest.mark.asyncio
    async def test_sync_exception_wrapped_in_invocation_error(self) -> None:
        registry = _make_registry_with("fail-agent", _failing_agent)
        invoker = AgentInvoker(registry)
        with pytest.raises(AgentInvocationError, match="boom") as exc_info:
            await invoker.invoke("fail-agent", _make_input())
        assert exc_info.value.agent_name == "fail-agent"
        assert isinstance(exc_info.value.original, RuntimeError)

    @pytest.mark.asyncio
    async def test_async_exception_wrapped_in_invocation_error(self) -> None:
        registry = _make_registry_with("async-fail", _async_failing_agent)
        invoker = AgentInvoker(registry)
        with pytest.raises(AgentInvocationError, match="async boom") as exc_info:
            await invoker.invoke("async-fail", _make_input())
        assert exc_info.value.agent_name == "async-fail"
        assert isinstance(exc_info.value.original, RuntimeError)


class TestAgentNotFound:
    @pytest.mark.asyncio
    async def test_unknown_agent_raises_not_found(self) -> None:
        registry = AgentRegistry()
        invoker = AgentInvoker(registry)
        with pytest.raises(AgentNotFoundError, match="ghost") as exc_info:
            await invoker.invoke("ghost", _make_input())
        assert exc_info.value.agent_name == "ghost"


class TestInputPassthrough:
    @pytest.mark.asyncio
    async def test_full_input_dict_passed_to_callable(self) -> None:
        received: list[dict] = []

        def _capturing_agent(data: dict) -> dict:
            received.append(data)
            return {"output": {"captured": True}}

        registry = _make_registry_with("capture-agent", _capturing_agent)
        invoker = AgentInvoker(registry)
        agent_input = _make_input()
        await invoker.invoke("capture-agent", agent_input)

        assert len(received) == 1
        assert received[0]["work_item_state"] == {"key": "value"}
        assert received[0]["node_config"] == {"step": 1}
        assert received[0]["run_id"] == "run-001"
