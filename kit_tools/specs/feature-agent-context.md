<!-- Template Version: 2.2.0 -->
---
feature: agent-context
status: active
session_ready: true
depends_on: [event-subscriptions, run-metadata]
vision_ref: "Framework Consumer (Embedded) persona"
type: epic-child
size: M
epic: embedding-enhancements
epic_seq: 3
epic_final: false
created: 2026-06-01
updated: 2026-06-01
---

# Feature Spec: Agent Context Injection

## Overview

Agents currently receive `AgentInput` (work_item_state, node_config, run_id) but have no reference to the Roots instance or any way to interact with the orchestration system. An agent cannot start a child run, query another run's status, or resolve a checkpoint.

This feature adds an opt-in `AgentContext` object injected into agents at invocation time, providing controlled access to Roots operations. This enables patterns like a "sequencer" agent that starts subprocess runs for each item in a list and waits for each to complete.

## Goals

- Enable agents to interact with the orchestration system via a controlled, limited API
- Support agent-initiated subprocess execution (start child run, wait for completion)
- Maintain backward compatibility — agents that don't need context work unchanged
- Prevent unbounded nesting via depth guards unified with existing subprocess depth tracking

## User Stories

### US-001: AgentContext class with limited API

**Description:** As a framework consumer, I want a context object that gives agents controlled access to Roots operations so that agents can start child runs and wait for their completion.

**Implementation Hints:**
- Create `roots/agents/context.py` with `AgentContext` class
- Constructor takes the `Roots` instance (strong reference — Python's cycle GC handles circular refs; weakref adds complexity with no benefit) and `run_id: str` (the current run this agent is executing in, needed for lock management)
- Methods wrap `Roots` methods: `start_run` → `self._roots.start_run()`, `get_run` → `self._roots.get_run()`, etc.
- `execute_run(run_id) -> RunRecord` — blocks until child completes or pauses; raises `OrchestrationError` if child fails (with status and error); returns `RunRecord` with final state if completed or paused
- **execute_run failure contract:** If the child run fails, `execute_run` raises `OrchestrationError` with the child run's final status and error details. The agent can catch this to handle failures. If the child run pauses (checkpoint), `execute_run` returns the `RunRecord` normally and the agent must check the returned run status.
- `resolve_checkpoint` delegates to `self._roots.resolve_checkpoint()`
- **Deferred methods (event-subscriptions dependency):** `wait_for` and `start_and_wait` methods will be added after the event-subscriptions feature ships (declared dependency). They are not part of this story.
- Do NOT expose: `load_process`, `register_agent`, `register_mcp_server`, `close`, `pack_process`, `install_package`, `get_run_graph` — admin/UI operations, not agent operations
- Max depth default 5 (matching `SubProcessNodeConfig.max_depth` default)

**Acceptance Criteria:**
- [ ] `AgentContext` class with methods: `start_run`, `get_run`, `execute_run`, `resolve_checkpoint`
- [ ] `start_run(process_id, work_item, metadata=None)` creates and returns `RunRecord`
- [ ] `execute_run(run_id) -> RunRecord` blocks until run completes or pauses; raises `OrchestrationError` if child run fails (with status and error details); returns `RunRecord` with final state
- [ ] `resolve_checkpoint(run_id, decision, notes=None)` resolves paused runs
- [ ] No admin/mutation methods exposed
- [ ] `wait_for` and `start_and_wait` methods added after event-subscriptions feature ships (declared dependency)
- [ ] Tests: each method delegates correctly; execute_run raises on child failure; context cannot access admin methods
- [ ] Full test suite passes
- [ ] Typecheck/lint passes

### US-002: Roots owns AgentInvoker (wiring refactor)

**Description:** Refactor so Roots creates AgentInvoker and passes it to Orchestrator, eliminating the dual-invoker problem.

**Implementation Hints:**
- Currently `Orchestrator` creates its own `AgentInvoker` at `orchestrator.py:1341`. Change: `Roots` creates `AgentInvoker` (already does at `__init__.py:57-59`), passes it to the `Orchestrator` constructor.
- `Orchestrator` stores and uses the passed-in invoker rather than creating its own.
- This also fixes a latent bug where `Orchestrator`'s invoker doesn't receive `mcp_gateway`.

**Acceptance Criteria:**
- [ ] `Roots` creates `AgentInvoker` and passes to `Orchestrator` (single invoker instance)
- [ ] `Orchestrator` no longer creates its own `AgentInvoker`
- [ ] All existing tests pass with the refactored wiring
- [ ] Full test suite passes
- [ ] Typecheck/lint passes

### US-003: Opt-in context injection mechanism

**Description:** Add `needs_context` flag and inject `AgentContext` into agents that opt in.

**Implementation Hints:**
- Add `needs_context: bool = False` to `AgentRegistration` model (`roots/agents/types.py:20-58`)
- **Registration validation:** If `needs_context=True` and `agent_type` is `REMOTE` or `MCP`, raise `ValueError("needs_context is only supported for LOCAL agents")` in the `AgentRegistration` model validator
- Create internal `InvocationContext` dataclass (in `roots/agents/context.py`): carries `run_id`, `owner_id`, `subprocess_depth` — threaded alongside `AgentInput` through `_invoke_local`. Keeps `AgentInput` clean as the public contract.
- In `_invoke_local`: after input schema validation, if `needs_context=True`, create `AgentContext(roots_instance, invocation_context)` and add to `input_dict["_roots_context"]`
- **Inject AFTER input schema validation** — `_invoke_local` validates input schema at lines 92-95 before invocation. The `_roots_context` key must be added AFTER this validation step, otherwise schemas with `additionalProperties: false` will reject it

**Acceptance Criteria:**
- [ ] `register_agent` accepts `needs_context: bool = False` parameter
- [ ] `AgentRegistration` model has `needs_context: bool = False` field
- [ ] `needs_context=True` on REMOTE or MCP agent raises `ValueError` at registration
- [ ] `InvocationContext` carries `run_id`, `owner_id`, `subprocess_depth` (not leaked into `AgentInput`)
- [ ] `_roots_context` injected AFTER input schema validation (not before)
- [ ] End-to-end test: agent registered with `needs_context=True` receives context, calls `context.start_run()`
- [ ] Agents registered without `needs_context` receive no context key (backward compatible)
- [ ] Full test suite passes
- [ ] Typecheck/lint passes

### US-004: Depth guard for nested execute_run

**Description:** As a framework consumer, I want a depth limit on nested execute_run calls so that agents can't create unbounded recursive execution chains.

**Implementation Hints:**
- **Use `_subprocess_depth` exclusively** — the codebase already tracks nesting via `_subprocess_depth` in `work_item_state` (`orchestrator.py:1218-1235`). Do NOT add a separate `ContextVar` counter. Single source of truth.
- `AgentContext.execute_run(run_id)`: read `_subprocess_depth` from the current run's `work_item_state`, increment it for the child run's state, check against max_depth (default 5 from `SubProcessNodeConfig`, ge=1, le=20). If exceeded, raise `OrchestrationError`.
- **Lock management during execute_run:** The agent is mid-invocation inside a tick that holds the parent run lock. `execute_run` must release the parent lock before child ticks and reacquire after, same as subprocess handler (`orchestrator.py:1270-1275`). The context needs `self._run_id` (set at construction) and the lock `owner_id` (from `InvocationContext`) to call `release_run_lock`/`acquire_run_lock`.
- **Lock reacquisition failure:** If lock reacquisition fails after child execution (lock stolen), raise `OrchestrationError` which propagates to the tick-level error handler marking the run failed.
- `owner_id` is threaded via `InvocationContext` (see US-003).
- Max depth default 5 (matching `SubProcessNodeConfig.max_depth` default) — configurable on `AgentContext` construction

**Acceptance Criteria:**
- [ ] `AgentContext.execute_run` reads `_subprocess_depth` from current run's work_item_state and increments for child
- [ ] Depth limit (default 5) raises `OrchestrationError` with clear message including current depth and max
- [ ] Parent run lock released before child execution, reacquired after (matching subprocess handler pattern)
- [ ] Lock reacquisition failure raises `OrchestrationError` (propagates to tick-level error handler, run marked failed)
- [ ] Tests: depth limit enforced at boundary
- [ ] Tests: depth accumulates correctly across mixed subprocess node + context.execute_run chains
- [ ] Tests: lock released during child execution and reacquired after
- [ ] Tests: lock reacquisition failure raises OrchestrationError
- [ ] Full test suite passes
- [ ] Typecheck/lint passes

## Out of Scope

- Agent context for remote HTTP or MCP agents (in-process only; registration with needs_context raises error)
- Process mutation via context (no load_process, register_agent)
- Context-aware retry policies (retry is a node-level concern)
- Per-agent context customization (all agents share the same context API)
- get_run_graph on context (UI/visualization concern, not agent concern)
- `wait_for` and `start_and_wait` methods (deferred until event-subscriptions ships)

## Technical Considerations

- The context holds a strong reference to Roots — Python's cycle GC handles this; weakref adds complexity with no benefit and risks dangling references
- `execute_run` blocks the current agent's execution while the child run completes — documented as intentional; long-running children should use event-subscriptions-based patterns once available
- The `_roots_context` key is injected into the input dict AFTER schema validation to avoid conflicts with `additionalProperties: false` schemas
- `_subprocess_depth` in work_item_state is the single source of truth for nesting depth — no separate in-memory counter
- Lock release/reacquire during child execution is critical for preventing stale locks and matches the existing subprocess handler pattern
- `depends_on: [event-subscriptions, run-metadata]` — wait_for/start_and_wait require event-subscriptions (deferred); start_run metadata parameter requires run-metadata
- `InvocationContext` dataclass threads `run_id`, `owner_id`, `subprocess_depth` through the invocation chain without polluting `AgentInput`

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md)

## Implementation Notes

## Refinement Notes

### Research Conducted
- `AgentInvoker._invoke_local` at `invoker.py:135-156`: validates input schema at lines 92-95 BEFORE invocation — context must inject after
- `AgentRegistration` at `types.py:20-58`: has model_validator for type-specific field validation — add needs_context check there
- `Orchestrator.__init__` at `orchestrator.py:1341` creates its own `AgentInvoker` — this is the dual-invoker problem
- `_subprocess_depth` at `orchestrator.py:1218-1235`: storage-backed depth tracking that survives crashes
- Lock release/reacquire at `orchestrator.py:1270-1275`: pattern for preventing stale locks during child execution
- `SubProcessNodeConfig.max_depth` default 5, ceiling 20 at `schema.py:220`

### Scope Adjustments
- Dropped get_run_graph from context API (no agent use case)
- Dropped ContextVar for depth tracking (use _subprocess_depth exclusively)
- Added registration validation: needs_context=True on REMOTE/MCP raises ValueError
- Specified execute_run failure contract: raises OrchestrationError on child failure
- Split US-002 into two stories: wiring refactor (US-002) and opt-in injection (US-003)
- Removed wait_for/start_and_wait from US-001 (deferred until event-subscriptions ships)
- execute_run returns RunRecord (not None)
- Unified depth default to 5 (matching SubProcessNodeConfig.max_depth)
- Added InvocationContext dataclass to carry run_id/owner_id/subprocess_depth
- Specified lock reacquisition failure behavior (raises OrchestrationError)

### Decisions Made
- Opt-in at registration (`needs_context=True`) rather than always-inject
- Reserved key `_roots_context` in input dict, injected AFTER schema validation
- Context is in-process only — remote and MCP agents cannot receive it (enforced at registration)
- Single depth tracking mechanism via `_subprocess_depth` in work_item_state
- Roots owns AgentInvoker, passes to Orchestrator (no dual-invoker)
- Strong reference to Roots (not weakref) — cycle GC handles it
- execute_run raises OrchestrationError on child failure; returns RunRecord on pause/completion
- Lock release/reacquire during child execution (matching subprocess handler)
- Lock reacquisition failure raises OrchestrationError → tick-level error handler marks run failed
- Depth default 5 across context and subprocess nodes (unified)
- InvocationContext dataclass threads owner_id/run_id/subprocess_depth without polluting AgentInput
- wait_for/start_and_wait deferred until event-subscriptions dependency ships
