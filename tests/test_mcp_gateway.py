"""Tests for the MCP Gateway."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from roots.agents.invoker import AgentInvocationError
from roots.agents.mcp_gateway import MCPConnection, MCPGateway


def _make_mock_tool(
    name: str = "test-tool",
    description: str = "A test tool",
    input_schema: dict | None = None,
) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.inputSchema = input_schema or {"type": "object", "properties": {}}
    return tool


def _make_mock_session() -> AsyncMock:
    session = AsyncMock()
    session.initialize = AsyncMock()
    list_result = MagicMock()
    list_result.tools = [_make_mock_tool()]
    session.list_tools = AsyncMock(return_value=list_result)

    content_item = MagicMock()
    content_item.model_dump.return_value = {"type": "text", "text": "result"}
    call_result = MagicMock()
    call_result.content = [content_item]
    call_result.isError = False
    session.call_tool = AsyncMock(return_value=call_result)

    return session


class TestConnectUrl:
    @pytest.mark.asyncio
    async def test_connect_url_creates_connection(self) -> None:
        gateway = MCPGateway()
        mock_session = _make_mock_session()

        mock_sse_cm = AsyncMock()
        mock_sse_cm.__aenter__ = AsyncMock(
            return_value=(MagicMock(), MagicMock())
        )
        mock_sse_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "roots.agents.mcp_gateway.sse_client",
                return_value=mock_sse_cm,
            ),
            patch(
                "roots.agents.mcp_gateway.ClientSession",
                return_value=mock_session_cm,
            ),
        ):
            connection = await gateway.connect_url("http://localhost:8000/sse")

        assert isinstance(connection, MCPConnection)
        assert connection.url == "http://localhost:8000/sse"
        assert connection.session is mock_session
        mock_session.initialize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_connect_url_caches_connection(self) -> None:
        gateway = MCPGateway()
        mock_session = _make_mock_session()

        mock_sse_cm = AsyncMock()
        mock_sse_cm.__aenter__ = AsyncMock(
            return_value=(MagicMock(), MagicMock())
        )
        mock_sse_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "roots.agents.mcp_gateway.sse_client",
                return_value=mock_sse_cm,
            ),
            patch(
                "roots.agents.mcp_gateway.ClientSession",
                return_value=mock_session_cm,
            ),
        ):
            conn1 = await gateway.connect_url("http://localhost:8000/sse")
            conn2 = await gateway.connect_url("http://localhost:8000/sse")

        assert conn1 is conn2

    @pytest.mark.asyncio
    async def test_connect_url_failure_raises_invocation_error(self) -> None:
        gateway = MCPGateway()

        with patch(
            "roots.agents.mcp_gateway.sse_client",
            side_effect=ConnectionError("refused"),
        ):
            with pytest.raises(
                AgentInvocationError, match="Failed to connect"
            ) as exc_info:
                await gateway.connect_url("http://bad-host:9999/sse")

        assert isinstance(exc_info.value.original, ConnectionError)
        assert "bad-host" in str(exc_info.value)


class TestDiscoverTools:
    @pytest.mark.asyncio
    async def test_discover_tools_returns_tool_descriptors(self) -> None:
        mock_session = _make_mock_session()
        connection = MCPConnection(
            url="http://localhost:8000/sse",
            session=mock_session,
        )
        gateway = MCPGateway()

        tools = await gateway.discover_tools(connection)

        assert len(tools) == 1
        assert tools[0]["name"] == "test-tool"
        assert tools[0]["description"] == "A test tool"
        assert tools[0]["input_schema"] == {
            "type": "object",
            "properties": {},
        }

    @pytest.mark.asyncio
    async def test_discover_tools_multiple(self) -> None:
        mock_session = _make_mock_session()
        list_result = MagicMock()
        list_result.tools = [
            _make_mock_tool("tool-a", "First tool"),
            _make_mock_tool("tool-b", "Second tool"),
        ]
        mock_session.list_tools = AsyncMock(return_value=list_result)

        connection = MCPConnection(
            url="http://localhost:8000/sse",
            session=mock_session,
        )
        gateway = MCPGateway()

        tools = await gateway.discover_tools(connection)

        assert len(tools) == 2
        assert tools[0]["name"] == "tool-a"
        assert tools[1]["name"] == "tool-b"

    @pytest.mark.asyncio
    async def test_discover_tools_handles_none_description(self) -> None:
        mock_session = _make_mock_session()
        list_result = MagicMock()
        list_result.tools = [_make_mock_tool(description=None)]
        # Override the description to be None (MagicMock default won't do it)
        list_result.tools[0].description = None
        mock_session.list_tools = AsyncMock(return_value=list_result)

        connection = MCPConnection(
            url="http://localhost:8000/sse",
            session=mock_session,
        )
        gateway = MCPGateway()

        tools = await gateway.discover_tools(connection)
        assert tools[0]["description"] == ""

    @pytest.mark.asyncio
    async def test_discover_tools_failure_raises_invocation_error(self) -> None:
        mock_session = _make_mock_session()
        mock_session.list_tools = AsyncMock(
            side_effect=RuntimeError("protocol error")
        )

        connection = MCPConnection(
            url="http://localhost:8000/sse",
            session=mock_session,
        )
        gateway = MCPGateway()

        with pytest.raises(
            AgentInvocationError, match="Failed to discover tools"
        ):
            await gateway.discover_tools(connection)


class TestCallTool:
    @pytest.mark.asyncio
    async def test_call_tool_returns_result(self) -> None:
        mock_session = _make_mock_session()
        connection = MCPConnection(
            url="http://localhost:8000/sse",
            session=mock_session,
        )
        gateway = MCPGateway()

        result = await gateway.call_tool(
            connection, "test-tool", {"input": "value"}
        )

        assert result["isError"] is False
        assert len(result["content"]) == 1
        assert result["content"][0] == {"type": "text", "text": "result"}
        mock_session.call_tool.assert_awaited_once_with(
            "test-tool", {"input": "value"}
        )

    @pytest.mark.asyncio
    async def test_call_tool_failure_raises_invocation_error(self) -> None:
        mock_session = _make_mock_session()
        mock_session.call_tool = AsyncMock(
            side_effect=RuntimeError("tool error")
        )
        connection = MCPConnection(
            url="http://localhost:8000/sse",
            session=mock_session,
        )
        gateway = MCPGateway()

        with pytest.raises(
            AgentInvocationError, match="Failed to call tool"
        ):
            await gateway.call_tool(
                connection, "test-tool", {"input": "value"}
            )

    @pytest.mark.asyncio
    async def test_call_tool_error_result(self) -> None:
        mock_session = _make_mock_session()
        content_item = MagicMock()
        content_item.model_dump.return_value = {
            "type": "text",
            "text": "error occurred",
        }
        error_result = MagicMock()
        error_result.content = [content_item]
        error_result.isError = True
        mock_session.call_tool = AsyncMock(return_value=error_result)

        connection = MCPConnection(
            url="http://localhost:8000/sse",
            session=mock_session,
        )
        gateway = MCPGateway()

        result = await gateway.call_tool(
            connection, "test-tool", {"input": "value"}
        )
        assert result["isError"] is True


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_removes_from_cache(self) -> None:
        gateway = MCPGateway()
        connection = MCPConnection(
            url="http://localhost:8000/sse",
            session=AsyncMock(),
        )
        gateway._connections["http://localhost:8000/sse"] = connection

        await gateway.disconnect(connection)

        assert "http://localhost:8000/sse" not in gateway._connections

    @pytest.mark.asyncio
    async def test_disconnect_calls_cleanup(self) -> None:
        gateway = MCPGateway()
        mock_session_cm = AsyncMock()
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)
        mock_sse_cm = AsyncMock()
        mock_sse_cm.__aexit__ = AsyncMock(return_value=False)

        connection = MCPConnection(
            url="http://localhost:8000/sse",
            session=AsyncMock(),
            _cleanup=(mock_session_cm, mock_sse_cm),
        )
        gateway._connections["http://localhost:8000/sse"] = connection

        await gateway.disconnect(connection)

        mock_session_cm.__aexit__.assert_awaited_once()
        mock_sse_cm.__aexit__.assert_awaited_once()


class TestClose:
    @pytest.mark.asyncio
    async def test_close_disconnects_all(self) -> None:
        gateway = MCPGateway()
        conn1 = MCPConnection(
            url="http://host1:8000/sse", session=AsyncMock()
        )
        conn2 = MCPConnection(
            url="http://host2:8000/sse", session=AsyncMock()
        )
        gateway._connections["http://host1:8000/sse"] = conn1
        gateway._connections["http://host2:8000/sse"] = conn2

        await gateway.close()

        assert len(gateway._connections) == 0

    @pytest.mark.asyncio
    async def test_close_on_empty_gateway(self) -> None:
        gateway = MCPGateway()
        await gateway.close()
        assert len(gateway._connections) == 0
