"""Tests for the agent invoker."""

import json

import httpx
import pytest

from roots.agents.invoker import (
    AgentInvocationError,
    AgentInvoker,
    AgentNotFoundError,
)
from roots.agents.registry import AgentRegistry
from roots.agents.types import AgentInput, AgentRegistration, AgentType


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


def _make_remote_registry(
    name: str = "remote-agent",
    callback_url: str = "http://remote.test/invoke",
    timeout_seconds: int = 300,
) -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(
        AgentRegistration(
            name=name,
            agent_type=AgentType.REMOTE,
            callback_url=callback_url,
            timeout_seconds=timeout_seconds,
        )
    )
    return registry


class TestRemoteInvocation:
    @pytest.mark.asyncio
    async def test_remote_agent_posts_correct_payload(self) -> None:
        captured_requests: list[httpx.Request] = []

        async def _handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return httpx.Response(
                200,
                json={"output": {"result": "ok"}},
            )

        transport = httpx.MockTransport(_handler)
        client = httpx.AsyncClient(transport=transport)
        registry = _make_remote_registry()
        invoker = AgentInvoker(registry, http_client=client)

        agent_input = _make_input()
        result = await invoker.invoke("remote-agent", agent_input)

        assert result.output == {"result": "ok"}
        assert result.escalate is False
        assert len(captured_requests) == 1
        req = captured_requests[0]
        assert req.method == "POST"
        assert str(req.url) == "http://remote.test/invoke"
        body = json.loads(req.content)
        assert body["work_item_state"] == {"key": "value"}
        assert body["run_id"] == "run-001"

    @pytest.mark.asyncio
    async def test_remote_agent_parses_escalation(self) -> None:
        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "output": {"result": "needs help"},
                    "escalate": True,
                    "escalation_reason": "too complex",
                },
            )

        transport = httpx.MockTransport(_handler)
        client = httpx.AsyncClient(transport=transport)
        registry = _make_remote_registry()
        invoker = AgentInvoker(registry, http_client=client)

        result = await invoker.invoke("remote-agent", _make_input())
        assert result.escalate is True
        assert result.escalation_reason == "too complex"

    @pytest.mark.asyncio
    async def test_remote_agent_http_error_raises_invocation_error(self) -> None:
        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                500,
                text="Internal Server Error",
            )

        transport = httpx.MockTransport(_handler)
        client = httpx.AsyncClient(transport=transport)
        registry = _make_remote_registry()
        invoker = AgentInvoker(registry, http_client=client)

        with pytest.raises(AgentInvocationError, match="HTTP 500") as exc_info:
            await invoker.invoke("remote-agent", _make_input())
        assert exc_info.value.agent_name == "remote-agent"
        assert isinstance(exc_info.value.original, httpx.HTTPStatusError)

    @pytest.mark.asyncio
    async def test_remote_agent_timeout_raises_invocation_error(self) -> None:
        async def _handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out")

        transport = httpx.MockTransport(_handler)
        client = httpx.AsyncClient(transport=transport)
        registry = _make_remote_registry(timeout_seconds=5)
        invoker = AgentInvoker(registry, http_client=client)

        with pytest.raises(AgentInvocationError, match="timed out") as exc_info:
            await invoker.invoke("remote-agent", _make_input())
        assert exc_info.value.agent_name == "remote-agent"
        assert isinstance(exc_info.value.original, httpx.TimeoutException)

    @pytest.mark.asyncio
    async def test_remote_agent_connection_error_raises_invocation_error(
        self,
    ) -> None:
        async def _handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        transport = httpx.MockTransport(_handler)
        client = httpx.AsyncClient(transport=transport)
        registry = _make_remote_registry()
        invoker = AgentInvoker(registry, http_client=client)

        with pytest.raises(
            AgentInvocationError, match="Connection failed"
        ) as exc_info:
            await invoker.invoke("remote-agent", _make_input())
        assert exc_info.value.agent_name == "remote-agent"
        assert isinstance(exc_info.value.original, httpx.ConnectError)

    @pytest.mark.asyncio
    async def test_remote_agent_respects_timeout_seconds(self) -> None:
        captured_timeouts: list[httpx.Timeout] = []

        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"output": {"ok": True}})

        class _CapturingTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(
                self, request: httpx.Request
            ) -> httpx.Response:
                captured_timeouts.append(request.extensions.get("timeout"))
                return httpx.Response(200, json={"output": {"ok": True}})

        transport = _CapturingTransport()
        client = httpx.AsyncClient(transport=transport)
        registry = _make_remote_registry(timeout_seconds=42)
        invoker = AgentInvoker(registry, http_client=client)

        await invoker.invoke("remote-agent", _make_input())
        assert len(captured_timeouts) == 1

    @pytest.mark.asyncio
    async def test_remote_agent_404_raises_with_status_code(self) -> None:
        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="Not Found")

        transport = httpx.MockTransport(_handler)
        client = httpx.AsyncClient(transport=transport)
        registry = _make_remote_registry()
        invoker = AgentInvoker(registry, http_client=client)

        with pytest.raises(AgentInvocationError, match="HTTP 404") as exc_info:
            await invoker.invoke("remote-agent", _make_input())
        assert "Not Found" in str(exc_info.value)
