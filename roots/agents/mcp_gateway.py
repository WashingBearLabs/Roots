"""MCP Gateway for URL-based MCP server connections and tool discovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession
from mcp.client.sse import sse_client

from roots.agents.invoker import AgentInvocationError


@dataclass
class MCPConnection:
    """Represents an active connection to an MCP server."""

    url: str
    session: ClientSession
    _cleanup: Any = field(repr=False, default=None)


class MCPGateway:
    """Gateway for connecting to MCP servers, discovering tools, and calling them."""

    def __init__(self) -> None:
        self._connections: dict[str, MCPConnection] = {}

    async def connect_url(self, url: str) -> MCPConnection:
        """Connect to a URL-based MCP server via SSE.

        Returns a cached connection if one already exists for the URL.
        Raises AgentInvocationError on connection failure.
        """
        if url in self._connections:
            return self._connections[url]

        try:
            sse_cm = sse_client(url)
            streams = await sse_cm.__aenter__()

            session_cm = ClientSession(*streams)
            session = await session_cm.__aenter__()

            await session.initialize()

            connection = MCPConnection(
                url=url,
                session=session,
                _cleanup=(session_cm, sse_cm),
            )
            self._connections[url] = connection
            return connection

        except Exception as exc:
            raise AgentInvocationError(
                agent_name="mcp",
                message=f"Failed to connect to MCP server at {url}: {exc}",
                original=exc,
            ) from exc

    async def discover_tools(
        self, connection: MCPConnection
    ) -> list[dict[str, Any]]:
        """Discover available tools on the connected MCP server.

        Returns a list of tool descriptors: [{name, description, input_schema}].
        """
        try:
            result = await connection.session.list_tools()
            return [
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.inputSchema,
                }
                for tool in result.tools
            ]
        except Exception as exc:
            raise AgentInvocationError(
                agent_name="mcp",
                message=f"Failed to discover tools on {connection.url}: {exc}",
                original=exc,
            ) from exc

    async def call_tool(
        self,
        connection: MCPConnection,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Call a tool on the connected MCP server.

        Returns the tool result as a dict.
        """
        try:
            result = await connection.session.call_tool(tool_name, arguments)
            content_items = []
            for item in result.content:
                content_items.append(
                    item.model_dump() if hasattr(item, "model_dump") else str(item)
                )
            return {
                "content": content_items,
                "isError": result.isError or False,
            }
        except AgentInvocationError:
            raise
        except Exception as exc:
            raise AgentInvocationError(
                agent_name="mcp",
                message=(
                    f"Failed to call tool '{tool_name}' "
                    f"on {connection.url}: {exc}"
                ),
                original=exc,
            ) from exc

    async def disconnect(self, connection: MCPConnection) -> None:
        """Disconnect from an MCP server and remove from cache."""
        url = connection.url
        self._connections.pop(url, None)

        if connection._cleanup:
            session_cm, sse_cm = connection._cleanup
            try:
                await session_cm.__aexit__(None, None, None)
            except Exception:
                pass
            try:
                await sse_cm.__aexit__(None, None, None)
            except Exception:
                pass

    async def close(self) -> None:
        """Disconnect all connections."""
        connections = list(self._connections.values())
        for conn in connections:
            await self.disconnect(conn)
