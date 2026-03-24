<!-- Template Version: 2.0.0 -->
---
feature: mcp-invocation
status: active
session_ready: true
depends_on: [agent-registry]
vision_ref: "T2.5 — MCP Agent Invocation"
type: epic-child
epic: roots-v1
epic_seq: 11
epic_final: true
created: 2026-03-23
updated: 2026-03-23
---

# Feature Spec: MCP Agent Invocation

## Overview

MCP agent invocation extends the agent registry with a third invocation type. MCP tools exposed by MCP servers can be registered as Roots agents and invoked transparently by the orchestrator. This is the lowest-priority spec — it can be dropped without affecting the rest of the framework.

## Goals

- Enable MCP tools to be registered and invoked as standard Roots agents
- Support URL-based (SSE/HTTP) MCP servers
- Support command-based (stdio) MCP servers with subprocess lifecycle management

## User Stories

### US-001: MCP Agent Type and Registration Model

**Description:** As a framework developer, I want an MCP agent type so that MCP tools can be registered alongside local and remote agents.

**Implementation Hints:**
- Add `mcp` to `AgentType` enum in `roots/agents/types.py`
- Extend `AgentRegistration` with: `mcp_server_url` (optional str), `mcp_server_command` (optional list[str]), `mcp_tool_name` (optional str — the tool name within the MCP server)
- Validator: MCP agents require `mcp_tool_name` AND exactly one of (`mcp_server_url`, `mcp_server_command`)
- Error messages: `"MCP agent requires mcp_tool_name"`, `"MCP agent requires exactly one of mcp_server_url or mcp_server_command"`

**Acceptance Criteria:**
- [ ] `AgentType.mcp` is valid
- [ ] MCP registration requires tool name + one connection method
- [ ] Both connection methods simultaneously rejected
- [ ] Neither connection method rejected
- [ ] Tests cover all validation cases

### US-002: URL-Based MCP Connection and Tool Discovery

**Description:** As a framework developer, I want to connect to URL-based MCP servers and discover their tools.

**Implementation Hints:**
- Create `roots/agents/mcp_gateway.py` with `MCPGateway` class
- Use the `mcp` Python package if available (`pip install mcp`). If not available, implement minimal SSE client.
- `async connect_url(url: str) -> MCPConnection`: establish SSE connection to MCP server
- `async discover_tools(connection) -> list[dict]`: call MCP `tools/list`, return `[{name, description, input_schema}]`
- `async call_tool(connection, tool_name, arguments) -> dict`: call MCP `tools/call`, return result
- `async disconnect(connection)`: close connection
- Connection caching: dict mapping url → connection, reuse across invocations
- On connection failure: raise `AgentInvocationError` with connection details

**Acceptance Criteria:**
- [ ] URL-based MCP server connection works
- [ ] Tool discovery returns available tools with schemas
- [ ] Tool calls return results
- [ ] Connections are cached and reused
- [ ] Connection failures produce clear errors
- [ ] Tests use mock MCP server (or skip if mcp package not installed)

### US-003: Command-Based MCP Subprocess Lifecycle

**Description:** As a framework developer, I want to manage stdio-based MCP servers as subprocesses.

**Implementation Hints:**
- In `MCPGateway`:
- `async connect_command(command: list[str]) -> MCPConnection`:
  - Start subprocess: `proc = await asyncio.create_subprocess_exec(*command, stdin=PIPE, stdout=PIPE, stderr=PIPE)`
  - Establish stdio MCP protocol over stdin/stdout
  - Use `mcp` package's stdio transport if available
- `async disconnect_command(connection)`:
  - Send MCP shutdown
  - Terminate subprocess with timeout
  - Kill if not terminated after 5 seconds
- Track subprocesses for cleanup on gateway shutdown
- `async close()`: disconnect all connections, terminate all subprocesses

**Acceptance Criteria:**
- [ ] Subprocess started with command
- [ ] Stdio MCP protocol established
- [ ] Graceful shutdown terminates subprocess
- [ ] Force kill after timeout
- [ ] Gateway close cleans up all connections
- [ ] Tests verify subprocess lifecycle (can use mock command)

### US-004: MCP Tool Invocation via AgentInvoker

**Description:** As a framework developer, I want MCP tools invoked through the standard AgentInvoker interface.

**Implementation Hints:**
- Extend `AgentInvoker.invoke` to handle `AgentType.mcp`:
  - Get or create connection via MCPGateway (url or command based on registration)
  - Map `AgentInput.work_item_state` to MCP tool arguments: use the full state dict as arguments (MCP tools define their own input schema)
  - Call `gateway.call_tool(connection, registration.mcp_tool_name, arguments)`
  - Map MCP result to `AgentOutput`: `AgentOutput(output=result_dict)`
  - Handle MCP errors → `AgentInvocationError`
  - Enforce `timeout_seconds` from registration

**Acceptance Criteria:**
- [ ] MCP tools invoked via standard `invoker.invoke()` interface
- [ ] State maps to tool arguments
- [ ] MCP result maps to AgentOutput
- [ ] Timeout enforced
- [ ] MCP errors produce AgentInvocationError
- [ ] Tests verify invocation with mock MCP

### US-005: MCP Agent Auto-Registration

**Description:** As a framework consumer, I want to register an MCP server and auto-discover its tools as agents.

**Implementation Hints:**
- Add to `Roots` class (or `AgentRegistry`):
- `async register_mcp_server(url: str = None, command: list[str] = None, tool_filter: list[str] = None, name_prefix: str = "mcp") -> list[str]`:
  - Connect to server (url or command)
  - Discover tools
  - For each tool (filtered by tool_filter if provided):
    - Agent name: `f"{name_prefix}_{tool_name}"` (sanitize: replace non-alphanumeric with `_`)
    - Create registration with type=mcp, tool_name, input_schema from MCP discovery
    - Register in agent registry
  - Return list of registered agent names

**Acceptance Criteria:**
- [ ] Tools auto-discovered and registered
- [ ] tool_filter limits registration
- [ ] Agent names use prefix + sanitized tool name
- [ ] Input schemas populated from MCP tool definitions
- [ ] Returns list of names
- [ ] Tests verify auto-registration

## Out of Scope

- MCP resources/prompts (only tools)
- MCP authentication
- MCP server implementation (Roots is a client)

## Technical Considerations

- The `mcp` package may not be installed — make it an optional dependency. Use try/except ImportError and raise clear error if MCP features are used without the package.
- This entire spec is drop-safe — if time is constrained, skip it. The rest of Roots works without MCP.

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
