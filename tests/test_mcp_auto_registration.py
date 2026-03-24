"""Tests for MCP Agent Auto-Registration (US-005)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from roots.agents.types import AgentType


def _make_mock_tool(
    name: str = "test-tool",
    description: str = "A test tool",
    input_schema: dict | None = None,
) -> dict:
    return {
        "name": name,
        "description": description,
        "input_schema": input_schema or {"type": "object", "properties": {}},
    }


def _make_roots_instance():
    """Create a Roots instance with mocked storage."""
    from roots import Roots

    storage = AsyncMock()
    storage.close = AsyncMock()
    return Roots(storage=storage)


class TestRegisterMcpServerUrl:
    @pytest.mark.asyncio
    async def test_auto_discovers_and_registers_tools(self) -> None:
        roots = _make_roots_instance()
        tools = [
            _make_mock_tool("add", "Add numbers", {"type": "object", "properties": {"a": {"type": "integer"}}}),
            _make_mock_tool("multiply", "Multiply numbers"),
        ]

        mock_connection = MagicMock()
        roots._mcp_gateway.connect_url = AsyncMock(return_value=mock_connection)
        roots._mcp_gateway.discover_tools = AsyncMock(return_value=tools)

        names = await roots.register_mcp_server(url="http://localhost:8000/sse")

        assert names == ["mcp_add", "mcp_multiply"]
        roots._mcp_gateway.connect_url.assert_awaited_once_with("http://localhost:8000/sse")
        roots._mcp_gateway.discover_tools.assert_awaited_once_with(mock_connection)

        # Verify registrations in the registry
        reg_add = roots._agent_registry.get("mcp_add")
        assert reg_add is not None
        assert reg_add.agent_type == AgentType.MCP
        assert reg_add.mcp_tool_name == "add"
        assert reg_add.mcp_server_url == "http://localhost:8000/sse"
        assert reg_add.input_schema == {"type": "object", "properties": {"a": {"type": "integer"}}}

        reg_mul = roots._agent_registry.get("mcp_multiply")
        assert reg_mul is not None
        assert reg_mul.mcp_tool_name == "multiply"

    @pytest.mark.asyncio
    async def test_tool_filter_limits_registration(self) -> None:
        roots = _make_roots_instance()
        tools = [
            _make_mock_tool("add"),
            _make_mock_tool("multiply"),
            _make_mock_tool("divide"),
        ]

        roots._mcp_gateway.connect_url = AsyncMock(return_value=MagicMock())
        roots._mcp_gateway.discover_tools = AsyncMock(return_value=tools)

        names = await roots.register_mcp_server(
            url="http://localhost:8000/sse",
            tool_filter=["add", "divide"],
        )

        assert names == ["mcp_add", "mcp_divide"]
        assert roots._agent_registry.get("mcp_multiply") is None

    @pytest.mark.asyncio
    async def test_name_prefix_customization(self) -> None:
        roots = _make_roots_instance()
        tools = [_make_mock_tool("greet")]

        roots._mcp_gateway.connect_url = AsyncMock(return_value=MagicMock())
        roots._mcp_gateway.discover_tools = AsyncMock(return_value=tools)

        names = await roots.register_mcp_server(
            url="http://localhost:8000/sse",
            name_prefix="weather",
        )

        assert names == ["weather_greet"]
        assert roots._agent_registry.get("weather_greet") is not None

    @pytest.mark.asyncio
    async def test_sanitizes_tool_names(self) -> None:
        roots = _make_roots_instance()
        tools = [
            _make_mock_tool("my-tool.v2"),
            _make_mock_tool("tool with spaces"),
            _make_mock_tool("tool@special#chars!"),
        ]

        roots._mcp_gateway.connect_url = AsyncMock(return_value=MagicMock())
        roots._mcp_gateway.discover_tools = AsyncMock(return_value=tools)

        names = await roots.register_mcp_server(url="http://localhost:8000/sse")

        assert names == ["mcp_my_tool_v2", "mcp_tool_with_spaces", "mcp_tool_special_chars_"]

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_tools(self) -> None:
        roots = _make_roots_instance()

        roots._mcp_gateway.connect_url = AsyncMock(return_value=MagicMock())
        roots._mcp_gateway.discover_tools = AsyncMock(return_value=[])

        names = await roots.register_mcp_server(url="http://localhost:8000/sse")

        assert names == []

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_filter_matches_nothing(self) -> None:
        roots = _make_roots_instance()
        tools = [_make_mock_tool("add")]

        roots._mcp_gateway.connect_url = AsyncMock(return_value=MagicMock())
        roots._mcp_gateway.discover_tools = AsyncMock(return_value=tools)

        names = await roots.register_mcp_server(
            url="http://localhost:8000/sse",
            tool_filter=["nonexistent"],
        )

        assert names == []


class TestRegisterMcpServerCommand:
    @pytest.mark.asyncio
    async def test_command_based_registration(self) -> None:
        roots = _make_roots_instance()
        tools = [_make_mock_tool("run_query")]

        mock_connection = MagicMock()
        roots._mcp_gateway.connect_command = AsyncMock(return_value=mock_connection)
        roots._mcp_gateway.discover_tools = AsyncMock(return_value=tools)

        names = await roots.register_mcp_server(
            command=["python3", "-m", "my_server"]
        )

        assert names == ["mcp_run_query"]
        roots._mcp_gateway.connect_command.assert_awaited_once_with(
            ["python3", "-m", "my_server"]
        )

        reg = roots._agent_registry.get("mcp_run_query")
        assert reg is not None
        assert reg.mcp_server_command == ["python3", "-m", "my_server"]
        assert reg.mcp_server_url is None


class TestRegisterMcpServerValidation:
    @pytest.mark.asyncio
    async def test_raises_when_both_url_and_command(self) -> None:
        roots = _make_roots_instance()

        with pytest.raises(ValueError, match="exactly one of url or command"):
            await roots.register_mcp_server(
                url="http://localhost:8000/sse",
                command=["my-server"],
            )

    @pytest.mark.asyncio
    async def test_raises_when_neither_url_nor_command(self) -> None:
        roots = _make_roots_instance()

        with pytest.raises(ValueError, match="exactly one of url or command"):
            await roots.register_mcp_server()


class TestInputSchemaPopulation:
    @pytest.mark.asyncio
    async def test_input_schema_from_mcp_tool(self) -> None:
        roots = _make_roots_instance()
        schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        }
        tools = [_make_mock_tool("search", input_schema=schema)]

        roots._mcp_gateway.connect_url = AsyncMock(return_value=MagicMock())
        roots._mcp_gateway.discover_tools = AsyncMock(return_value=tools)

        names = await roots.register_mcp_server(url="http://localhost:8000/sse")

        reg = roots._agent_registry.get("mcp_search")
        assert reg is not None
        assert reg.input_schema == schema

    @pytest.mark.asyncio
    async def test_input_schema_none_when_not_provided(self) -> None:
        roots = _make_roots_instance()
        tools = [{"name": "bare_tool", "description": "No schema", "input_schema": None}]

        roots._mcp_gateway.connect_url = AsyncMock(return_value=MagicMock())
        roots._mcp_gateway.discover_tools = AsyncMock(return_value=tools)

        names = await roots.register_mcp_server(url="http://localhost:8000/sse")

        reg = roots._agent_registry.get("mcp_bare_tool")
        assert reg is not None
        assert reg.input_schema is None
