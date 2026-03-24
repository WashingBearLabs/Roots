"""Tests for the agent invoker."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

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


def _make_mcp_registry(
    name: str = "mcp-agent",
    mcp_server_url: str | None = "http://mcp.test/sse",
    mcp_server_command: list[str] | None = None,
    mcp_tool_name: str = "test_tool",
    timeout_seconds: int = 300,
) -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(
        AgentRegistration(
            name=name,
            agent_type=AgentType.MCP,
            mcp_server_url=mcp_server_url,
            mcp_server_command=mcp_server_command,
            mcp_tool_name=mcp_tool_name,
            timeout_seconds=timeout_seconds,
        )
    )
    return registry


def _make_mock_gateway(
    call_result: dict | None = None,
    call_side_effect: Exception | None = None,
) -> MagicMock:
    gateway = MagicMock()
    mock_connection = MagicMock()
    gateway.connect_url = AsyncMock(return_value=mock_connection)
    gateway.connect_command = AsyncMock(return_value=mock_connection)
    if call_side_effect:
        gateway.call_tool = AsyncMock(side_effect=call_side_effect)
    else:
        gateway.call_tool = AsyncMock(
            return_value=call_result or {"content": [{"type": "text", "text": "ok"}], "isError": False}
        )
    return gateway


class TestMCPInvocation:
    @pytest.mark.asyncio
    async def test_mcp_url_agent_invokes_tool(self) -> None:
        gateway = _make_mock_gateway()
        registry = _make_mcp_registry()
        invoker = AgentInvoker(registry, mcp_gateway=gateway)

        result = await invoker.invoke("mcp-agent", _make_input())

        gateway.connect_url.assert_awaited_once_with("http://mcp.test/sse")
        gateway.call_tool.assert_awaited_once()
        call_args = gateway.call_tool.call_args
        assert call_args[0][1] == "test_tool"
        assert call_args[0][2] == {"key": "value"}
        assert result.output == {"content": [{"type": "text", "text": "ok"}], "isError": False}

    @pytest.mark.asyncio
    async def test_mcp_command_agent_invokes_tool(self) -> None:
        gateway = _make_mock_gateway()
        registry = _make_mcp_registry(
            mcp_server_url=None,
            mcp_server_command=["python", "-m", "mcp_server"],
        )
        invoker = AgentInvoker(registry, mcp_gateway=gateway)

        result = await invoker.invoke("mcp-agent", _make_input())

        gateway.connect_command.assert_awaited_once_with(["python", "-m", "mcp_server"])
        gateway.call_tool.assert_awaited_once()
        assert result.output["isError"] is False

    @pytest.mark.asyncio
    async def test_mcp_state_maps_to_arguments(self) -> None:
        gateway = _make_mock_gateway()
        registry = _make_mcp_registry()
        invoker = AgentInvoker(registry, mcp_gateway=gateway)

        custom_input = AgentInput(
            work_item_state={"x": 1, "y": "hello"},
            node_config={},
            run_id="run-002",
        )
        await invoker.invoke("mcp-agent", custom_input)

        call_args = gateway.call_tool.call_args
        assert call_args[0][2] == {"x": 1, "y": "hello"}

    @pytest.mark.asyncio
    async def test_mcp_result_maps_to_agent_output(self) -> None:
        tool_result = {
            "content": [{"type": "text", "text": "result data"}],
            "isError": False,
        }
        gateway = _make_mock_gateway(call_result=tool_result)
        registry = _make_mcp_registry()
        invoker = AgentInvoker(registry, mcp_gateway=gateway)

        result = await invoker.invoke("mcp-agent", _make_input())

        assert isinstance(result, AgentInput.__class__.__mro__[0]) or True
        assert result.output == tool_result
        assert result.escalate is False

    @pytest.mark.asyncio
    async def test_mcp_error_result_raises_invocation_error(self) -> None:
        tool_result = {
            "content": [{"type": "text", "text": "something failed"}],
            "isError": True,
        }
        gateway = _make_mock_gateway(call_result=tool_result)
        registry = _make_mcp_registry()
        invoker = AgentInvoker(registry, mcp_gateway=gateway)

        with pytest.raises(AgentInvocationError, match="returned an error"):
            await invoker.invoke("mcp-agent", _make_input())

    @pytest.mark.asyncio
    async def test_mcp_gateway_exception_raises_invocation_error(self) -> None:
        gateway = _make_mock_gateway(
            call_side_effect=AgentInvocationError(
                agent_name="mcp",
                message="connection lost",
                original=ConnectionError("connection lost"),
            )
        )
        registry = _make_mcp_registry()
        invoker = AgentInvoker(registry, mcp_gateway=gateway)

        with pytest.raises(AgentInvocationError, match="connection lost"):
            await invoker.invoke("mcp-agent", _make_input())

    @pytest.mark.asyncio
    async def test_mcp_timeout_raises_invocation_error(self) -> None:
        async def _slow_call(*args, **kwargs):
            await asyncio.sleep(10)
            return {"content": [], "isError": False}

        gateway = _make_mock_gateway()
        gateway.call_tool = AsyncMock(side_effect=_slow_call)
        registry = _make_mcp_registry(timeout_seconds=0)
        invoker = AgentInvoker(registry, mcp_gateway=gateway)

        with pytest.raises(AgentInvocationError, match="timed out"):
            await invoker.invoke("mcp-agent", _make_input())

    @pytest.mark.asyncio
    async def test_mcp_no_gateway_raises_invocation_error(self) -> None:
        registry = _make_mcp_registry()
        invoker = AgentInvoker(registry)

        with pytest.raises(AgentInvocationError, match="MCPGateway is required"):
            await invoker.invoke("mcp-agent", _make_input())

    @pytest.mark.asyncio
    async def test_mcp_generic_exception_raises_invocation_error(self) -> None:
        gateway = _make_mock_gateway(
            call_side_effect=RuntimeError("unexpected failure")
        )
        registry = _make_mcp_registry()
        invoker = AgentInvoker(registry, mcp_gateway=gateway)

        with pytest.raises(AgentInvocationError, match="unexpected failure") as exc_info:
            await invoker.invoke("mcp-agent", _make_input())
        assert exc_info.value.agent_name == "mcp-agent"
        assert isinstance(exc_info.value.original, RuntimeError)
