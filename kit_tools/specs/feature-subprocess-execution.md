<!-- Template Version: 2.2.0 -->
---
feature: subprocess-execution
status: active
session_ready: true
depends_on: [subprocess-schema]
vision_ref: "T3.3 — Process Composition"
type: epic-child
size: L
epic: process-composition
epic_seq: 2
epic_final: true
created: 2026-05-13
updated: 2026-05-13
---

# Feature Spec: Subprocess Execution & Lifecycle

## Overview

With the SUBPROCESS node type and storage foundation in place (feature-subprocess-schema), this spec implements the actual execution: the orchestrator handler that creates and runs child processes, pause cascading between parent and child, failure propagation, depth limit enforcement, and API visibility for parent/child relationships.

## Goals

- Execute subprocess nodes by creating and running child processes to completion
- Cascade pause/escalation from child to parent and resume correctly
- Propagate child failure to parent node
- Enforce max_depth limit at runtime to prevent runaway nesting
- Expose parent/child relationships via API

## User Stories

### US-001: Implement subprocess handler — happy path

**Description:** As a process author, I want subprocess nodes to execute a child process and map its output back to my parent state so that I can compose workflows from reusable sub-processes.

**Implementation Hints:**
- Dispatch table at `roots/core/orchestrator.py:369-388` — add NodeType.SUBPROCESS: self._handle_subprocess
- Handler follows existing pattern: `async def _handle_subprocess(self, node: NodeDefinition, state: dict[str, Any]) -> dict[str, Any] | None`
- Create child run via storage.create_run with mapped input state and parent_run_id/parent_node_id
- Execute child via inner ProcessRunner loop: `while await runner.tick(): pass`
- ProcessRunner constructor at orchestrator.py:72-95 — child runner needs same dependencies (storage, invoker, decision engine, emitter)
- After child completes, extract output via output_mapping from child's final work_item_state
- Store child_run_id in parent state (e.g., `state["_subprocess_run_" + node.id]`) for pause/resume tracking

**Acceptance Criteria:**
- [x] _handle_subprocess added to orchestrator dispatch table (replaces stub handler from schema spec)
- [x] Handler creates child run with input_mapping applied: for each (parent_key, child_key) in input_mapping, child_state[child_key] = parent_state[parent_key]; missing parent keys raise OrchestrationError (not KeyError)
- [x] Child run executes to completion via inner ProcessRunner tick loop; parent lock refreshed between child ticks to prevent lock theft by other orchestrator instances
- [x] Output mapped back: for each (child_key, parent_key) in output_mapping, result[parent_key] = child_state[child_key]; missing child keys produce None (not KeyError); stored at output_key
- [x] Child runner uses parent's owner_id for lock acquisition
- [x] `_subprocess_depth` injected into child initial state (current depth + 1) for depth tracking by US-004
- [x] SUBPROCESS_STARTED and SUBPROCESS_COMPLETED events emitted with child_run_id in metadata
- [x] Tests written/updated for new functionality
- [x] Full test suite passes
- [x] Typecheck/lint passes

### US-002: Handle subprocess pause cascading

**Description:** As a platform operator, I want parent runs to pause when a child process hits a checkpoint or escalation so that human review flows work correctly through composed processes.

**Implementation Hints:**
- When inner tick loop ends and child status is PAUSED, trigger parent escalation via _trigger_escalation (orchestrator.py:585-606)
- Use EscalationTrigger.SUBPROCESS_PAUSED from schema spec
- Store child_run_id in parent's work_item_state before pausing (key: `_subprocess_run_{node.id}`)
- On parent resume: _handle_subprocess re-enters, checks for existing child run via stored child_run_id
- If child is still PAUSED, re-pause parent (operator must resolve child first)
- If child completed since pause, extract output normally
- If child run ID found but child no longer exists, fail gracefully

**Acceptance Criteria:**
- [x] Initial child pause: _trigger_escalation called with SUBPROCESS_PAUSED, creates escalation record with child_run_id
- [x] Child_run_id stored in parent work_item_state before parent pauses
- [x] Parent resume: handler detects existing child run from state, checks its status instead of creating new child
- [x] If child completed: output extracted and returned normally (no duplicate child runs)
- [x] If child still paused: parent re-pauses by setting self._escalated = True directly (no duplicate escalation record — distinct from initial pause path)
- [x] If stored child_run_id not found in storage: parent node fails with clear error
- [x] Tests written/updated for new functionality
- [x] Full test suite passes
- [x] Typecheck/lint passes

### US-003: Handle subprocess failure propagation

**Description:** As a framework consumer, I want parent nodes to fail when a child process fails so that errors don't silently disappear in composed workflows.

**Implementation Hints:**
- After inner tick loop ends, check child run final status
- Child FAILED → emit SUBPROCESS_FAILED event, then fail parent node (same pattern as error_key detection at orchestrator.py:255-300)
- Child CANCELLED → treat as failure for parent (child was externally cancelled)
- Include child_run_id and child's error info in parent failure metadata
- History event should record the subprocess failure with child context

**Acceptance Criteria:**
- [x] Child run FAILED → parent node fails with NODE_FAILED and RUN_FAILED events; SUBPROCESS_FAILED event emitted with child_run_id
- [x] Child run CANCELLED → parent node fails (same behavior as child failure)
- [x] Failure metadata includes child_run_id and child's final status
- [x] Tests written/updated for new functionality
- [x] Full test suite passes
- [x] Typecheck/lint passes

### US-004: Enforce subprocess depth limit

**Description:** As a framework consumer, I want a configurable depth limit on subprocess nesting so that circular or deeply nested processes can't run forever.

**Implementation Hints:**
- max_depth is on SubProcessNodeConfig (default 5, from schema spec)
- Track current depth: pass through ProcessRunner or store in run metadata
- Option: add a `_subprocess_depth` key to work_item_state when creating child run; child handler reads it and increments
- At handler entry: check depth against max_depth; if exceeded, fail node immediately
- The circular reference validator (schema spec US-003) catches static cycles; depth limit catches dynamic/runtime depth

**Acceptance Criteria:**
- [x] Current subprocess depth tracked (via state metadata or runner context)
- [x] Depth checked at handler entry before creating child run
- [x] Depth >= max_depth → node fails with clear error message ("Subprocess depth limit exceeded: {depth}/{max_depth}")
- [x] Default depth limit (5) prevents unbounded nesting in tests
- [x] Tests written/updated for new functionality
- [x] Full test suite passes
- [x] Typecheck/lint passes

### US-005: API visibility for subprocess runs

**Description:** As a platform operator, I want to see parent/child run relationships in the API so that I can trace execution through composed processes.

**Implementation Hints:**
- Existing run routes at `roots/api/routers/runs.py` — add child runs endpoint
- Follow pattern from existing run detail endpoint for response model
- RunRecord already has parent_run_id/parent_node_id from schema spec — just expose in API response
- get_child_runs(parent_run_id) from storage spec — wire to endpoint

**Acceptance Criteria:**
- [x] GET /runs/{id}/children endpoint returns list of child runs for a parent run
- [x] Existing RunResponse model (or equivalent) updated to include parent_run_id and parent_node_id fields; _run_to_response helper updated
- [x] Returns empty list (not 404) when run has no children
- [x] Tests written/updated for new functionality
- [x] Full test suite passes
- [x] Typecheck/lint passes

## Out of Scope

- Subprocess cancellation propagation (parent cancel → child cancel)
- Subprocess retry (retry config on subprocess nodes)
- Dynamic process_id selection from state
- Version pinning on subprocess references
- Parallel subprocess execution within a single node (use fork for this)

## Technical Considerations

- **Inner execution loop:** The subprocess handler runs a full ProcessRunner tick loop synchronously from the parent's perspective. This means a long-running child process blocks the parent's tick. This is acceptable for v1 — the alternative (async child execution with polling) is significantly more complex.
- **Lock refresh during child execution:** The parent holds a run lock during its tick. If the child process has many nodes, the parent's lock could go stale (lock TTL), allowing another orchestrator instance to steal it. The handler must refresh the parent lock between child ticks — e.g., call `storage.acquire_run_lock(parent_run_id, owner_id)` periodically during the child tick loop.
- **State isolation:** input_mapping explicitly selects which parent state keys cross into the child. The child never sees the full parent state. output_mapping explicitly selects which child state keys come back. Missing parent keys in input_mapping are an error (OrchestrationError). Missing child keys in output_mapping produce None (graceful — child may not have written all expected outputs).
- **Pause resume complexity:** When parent is resumed, _handle_subprocess re-enters from scratch. It must detect the existing child run (stored in `_subprocess_run_{node.id}` state key) and check its status. Two distinct pause code paths: initial pause creates escalation record via _trigger_escalation; re-pause (child still paused on resume) sets self._escalated = True directly without creating a duplicate record.
- **Locking:** The parent holds a run lock during its tick. The child ProcessRunner acquires its own lock (different run ID, no contention). Child runner uses parent's owner_id.
- **Fork/join interaction:** Subprocess nodes inside fork branches are a **v1 limitation**: `_execute_branch` does not check `self._escalated` after dispatch, so a subprocess pause inside a fork is silently ignored. This should be documented in GOTCHAS.md. A full fix (escalation-aware branch execution) is deferred.
- **Cancellation behavior:** If a parent run is cancelled while a child is running, the child run is orphaned (keeps running or stays paused). Cancellation propagation is explicitly out of scope. The orphaned child will eventually complete/fail independently.
- **Depth injection:** US-001 injects `_subprocess_depth` into the child's initial state (parent's depth + 1). US-004 reads this value at handler entry. If `_subprocess_depth` is absent (root-level process), depth is 0.

## Design Considerations

N/A — no UI components.

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md)

## Implementation Notes

<!-- Populated during implementation -->

## Refinement Notes

### Research Conducted
- Orchestrator dispatch at orchestrator.py:369-388 — dict-based, add one entry
- ProcessRunner constructor at orchestrator.py:72-95 — needs storage, invoker, decision_engine, emitter, owner_id
- tick() returns bool — False when run is terminal or can't proceed
- Pause mechanism: _escalated flag → status=PAUSED → checkpoint resolution → status=RUNNING
- Fork branch execution at orchestrator.py:804-1053 — branches use _execute_branch with inner tick loops
- Run locking: locked_by/locked_at columns — each run has independent lock

### Scope Adjustments
- Split failure and pause into separate stories (US-002 and US-003) — different lifecycle concerns
- API visibility is its own story (US-005) — different layer from orchestrator
- Fork branch + subprocess pause documented as v1 limitation (not fixed) — _execute_branch doesn't check escalation

### Decisions Made
- Inner tick loop (synchronous from parent) for v1 — simpler than async polling, acceptable performance tradeoff
- Parent lock refreshed between child ticks — prevents lock theft by other orchestrator instances
- Child_run_id stored in parent state at `_subprocess_run_{node.id}` — enables pause/resume without external tracking table
- Two distinct pause paths: initial pause creates escalation record; re-pause sets flag directly (no duplicate records)
- Missing input_mapping keys are OrchestrationError; missing output_mapping keys produce None
- Child runner uses parent's owner_id
- `_subprocess_depth` injected by US-001, consumed by US-004 — explicit cross-story dependency
- Child CANCELLED treated as parent failure — cancelled child means the composed workflow can't complete
- Depth tracked via state metadata (not ProcessRunner constructor) — survives serialization and crash recovery
- Fork branch + subprocess pause is a v1 limitation (add to GOTCHAS.md)

## Open Questions

None — all design questions resolved during planning and validation review.
