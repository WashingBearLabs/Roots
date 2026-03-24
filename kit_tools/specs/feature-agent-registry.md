<!-- Template Version: 2.0.0 -->
---
feature: agent-registry
status: active
session_ready: true
depends_on: [process-schema]
vision_ref: "T1.5 — Agent Registry & Invocation"
type: epic-child
epic: roots-v1
epic_seq: 3
epic_final: false
created: 2026-03-23
updated: 2026-03-23
---

# Feature Spec: Agent Registry & Invocation

## Overview

The agent registry maps agent names to invocation strategies and provides a uniform interface for the orchestrator to call agents regardless of whether they are local callables or remote HTTP services. It handles input/output schema validation and produces typed errors that the orchestrator can handle. MCP invocation is deferred to T2.5.

## Goals

- Provide a registry where agents are registered by name with their invocation type and schemas
- Implement transparent invocation for local callable and remote HTTP agent types
- Validate agent inputs and outputs against declared schemas, raising typed errors on mismatch

## User Stories

### US-001: Agent Registration Models

**Description:** As a framework developer, I want agent registration data models so that agents can be described with their type, schemas, and configuration.

**Implementation Hints:**
- Create `roots/agents/types.py` with:
  - `AgentType` enum: `local`, `remote` (MCP added later in T2.5)
  - `AgentRegistration` model: `name` (str), `agent_type` (AgentType), `callable` (optional — for local), `callback_url` (optional str — for remote), `input_schema` (optional dict — JSON Schema), `output_schema` (optional dict — JSON Schema), `timeout_seconds` (int, default 300), `metadata` (optional dict)
  - Validator: `callable` required when type is `local`, `callback_url` required when type is `remote`
- Define `AgentInput` model: `work_item_state` (dict), `node_config` (dict), `run_id` (str)
- Define `AgentOutput` model: `output` (dict), `escalate` (bool, default False), `escalation_reason` (optional str)

**Acceptance Criteria:**
- [x] `AgentRegistration` validates type-specific required fields
- [x] `AgentInput` and `AgentOutput` models defined
- [x] Local agents require callable, remote agents require callback_url
- [x] Tests cover valid and invalid registration combinations

### US-002: Agent Registry

**Description:** As a framework developer, I want an in-memory registry so that agents can be registered, looked up, and deregistered at runtime.

**Implementation Hints:**
- Create `roots/agents/registry.py` with `AgentRegistry` class
- Internal dict mapping name → AgentRegistration
- Methods: `register(registration: AgentRegistration)`, `get(name: str) -> AgentRegistration | None`, `list() -> list[AgentRegistration]`, `deregister(name: str) -> bool`
- `register` raises `ValueError` if name already registered (no silent overwrite)
- Convenience method: `register_local(name, callable, input_schema=None, output_schema=None)` — shorthand for local agent registration
- The registry is in-memory but can be persisted to storage backend by the orchestrator for standalone mode

**Acceptance Criteria:**
- [ ] Agents can be registered and looked up by name
- [ ] Duplicate registration raises ValueError
- [ ] `list()` returns all registered agents
- [ ] `deregister` removes agent and returns True, or False if not found
- [ ] `register_local` convenience method works correctly
- [ ] Tests cover register, lookup, list, deregister, duplicate handling

### US-003: Local Callable Invocation

**Description:** As a framework developer, I want to invoke local Python callables as agents so that embedded mode consumers can register functions directly.

**Implementation Hints:**
- Create `roots/agents/invoker.py` with `AgentInvoker` class
- Constructor takes `AgentRegistry`
- Method `invoke(agent_name: str, input: AgentInput) -> AgentOutput`:
  - Look up agent in registry
  - For local agents: always pass the full `AgentInput` as a single dict argument: `callable(agent_input.model_dump())`. The callable receives `{"work_item_state": {...}, "node_config": {...}, "run_id": "..."}` and extracts what it needs. No signature inspection magic.
  - Support both sync and async callables: inspect with `asyncio.iscoroutinefunction(fn)`. If sync, wrap in `asyncio.to_thread` to avoid blocking the event loop.
  - Wrap the callable's return value into `AgentOutput`
  - Handle exceptions: catch, wrap in `AgentInvocationError` with context
- Define `AgentInvocationError(Exception)`: includes agent_name, error message, original exception
- Define `AgentNotFoundError(Exception)`: when agent_name not in registry

**Acceptance Criteria:**
- [ ] Sync callables are invoked without blocking the event loop
- [ ] Async callables are awaited directly
- [ ] Callable return dict is wrapped into AgentOutput
- [ ] Exceptions from callables are caught and wrapped in AgentInvocationError
- [ ] Unknown agent names raise AgentNotFoundError
- [ ] Tests cover sync callable, async callable, exception handling

### US-004: Remote HTTP Invocation

**Description:** As a framework developer, I want to invoke remote HTTP agents so that agents in separate services can participate in process execution.

**Implementation Hints:**
- In `AgentInvoker.invoke`, add remote handling:
  - Use `httpx.AsyncClient` to POST to `callback_url`
  - Request body: `AgentInput.model_dump(mode="json")`
  - Response: parse as `AgentOutput`
  - Respect `timeout_seconds` from agent registration
  - On HTTP error (4xx/5xx): raise `AgentInvocationError` with status code and response body
  - On timeout: raise `AgentInvocationError` with timeout context
  - On connection error: raise `AgentInvocationError` with connection context
- Create a shared `httpx.AsyncClient` instance in the invoker (reuse connections)

**Acceptance Criteria:**
- [ ] Remote agents are called via HTTP POST with correct payload
- [ ] Response is parsed into AgentOutput
- [ ] Timeout is enforced per agent registration
- [ ] HTTP errors produce AgentInvocationError with status code
- [ ] Connection failures produce AgentInvocationError
- [ ] Tests use httpx mock transport (no real HTTP calls)

### US-005: Input/Output Schema Validation

**Description:** As a framework developer, I want agent inputs and outputs validated against declared schemas so that bad data never silently corrupts work item state.

**Implementation Hints:**
- Before invocation: if agent has `input_schema`, validate `input.work_item_state` against it
- After invocation: if agent has `output_schema`, validate `output.output` against it
- Use the `jsonschema` library (`jsonschema.validate(instance, schema)`) for JSON Schema validation. This is already in pyproject.toml from T1.1.
- On validation failure: raise `AgentSchemaValidationError(AgentInvocationError)` with the field path and expected/actual values
- `AgentSchemaValidationError` should include: `agent_name`, `direction` (input/output), `validation_errors` (list of error details)
- The orchestrator uses this error type to trigger escalation (T2.1)

**Acceptance Criteria:**
- [ ] Input validation runs before invocation when input_schema is set
- [ ] Output validation runs after invocation when output_schema is set
- [ ] Schema validation failures raise AgentSchemaValidationError
- [ ] Error includes agent name, direction (input/output), and specific field errors
- [ ] No validation occurs when schemas are not set (permissive by default)
- [ ] Tests cover: valid input/output, invalid input (blocks invocation), invalid output (post-invocation error)

## Out of Scope

- MCP agent invocation (T2.5)
- Agent health checking (T2.3 — HTTP API)
- Agent persistence to storage (orchestrator handles this for standalone mode)
- Agent capability discovery or negotiation

## Technical Considerations

- `asyncio.to_thread` for sync callables is important — a blocking agent must not freeze the event loop
- `httpx.AsyncClient` should be created once and reused, not per-request
- JSON Schema validation adds a dependency (`jsonschema`) — add to pyproject.toml
- The `AgentOutput.escalate` field is how agents explicitly signal escalation — the orchestrator checks this

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
