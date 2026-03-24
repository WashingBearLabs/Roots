"""MCP Gateway for MCP server connections and tool discovery."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client

from roots.agents.invoker import AgentInvocationError

SUBPROCESS_SHUTDOWN_TIMEOUT = 5


@dataclass
class MCPConnection:
    """Represents an active connection to an MCP server."""

    url: str
    session: ClientSession
    _cleanup: Any = field(repr=False, default=None)
    command: list[str] | None = field(repr=False, default=None)
    process: asyncio.subprocess.Process | None = field(repr=False, default=None)


class MCPGateway:
    """Gateway for connecting to MCP servers, discovering tools, and calling them."""

    def __init__(self) -> None:
        self._connections: dict[str, MCPConnection] = {}
        self._command_connections: dict[str, MCPConnection] = {}

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

    async def connect_command(self, command: list[str]) -> MCPConnection:
        """Connect to a command-based MCP server via stdio subprocess.

        Returns a cached connection if one already exists for the command.
        Raises AgentInvocationError on connection failure.
        """
        cmd_key = " ".join(command)
        if cmd_key in self._command_connections:
            return self._command_connections[cmd_key]

        try:
            server_params = StdioServerParameters(
                command=command[0],
                args=command[1:],
            )

            stdio_cm = stdio_client(server_params)
            streams = await stdio_cm.__aenter__()

            session_cm = ClientSession(*streams)
            session = await session_cm.__aenter__()

            await session.initialize()

            connection = MCPConnection(
                url=cmd_key,
                session=session,
                _cleanup=(session_cm, stdio_cm),
                command=command,
            )
            self._command_connections[cmd_key] = connection
            return connection

        except Exception as exc:
            raise AgentInvocationError(
                agent_name="mcp",
                message=(
                    f"Failed to start MCP subprocess "
                    f"'{cmd_key}': {exc}"
                ),
                original=exc,
            ) from exc

    async def disconnect_command(self, connection: MCPConnection) -> None:
        """Disconnect a command-based MCP server and terminate its subprocess.

        Sends MCP shutdown, terminates subprocess gracefully, and force-kills
        after SUBPROCESS_SHUTDOWN_TIMEOUT seconds if still running.
        """
        cmd_key = " ".join(connection.command or [])
        self._command_connections.pop(cmd_key, None)

        if connection._cleanup:
            session_cm, stdio_cm = connection._cleanup
            try:
                await session_cm.__aexit__(None, None, None)
            except Exception:
                pass
            try:
                await stdio_cm.__aexit__(None, None, None)
            except Exception:
                pass

        if connection.process and connection.process.returncode is None:
            connection.process.terminate()
            try:
                await asyncio.wait_for(
                    connection.process.wait(),
                    timeout=SUBPROCESS_SHUTDOWN_TIMEOUT,
                )
            except asyncio.TimeoutError:
                connection.process.kill()
                await connection.process.wait()

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
        """Disconnect all connections and terminate all subprocesses."""
        connections = list(self._connections.values())
        for conn in connections:
            await self.disconnect(conn)

        cmd_connections = list(self._command_connections.values())
        for conn in cmd_connections:
            await self.disconnect_command(conn)
