<!-- Template Version: 2.2.0 -->
---
feature: iterator-node
status: active
session_ready: true
depends_on: [crash-safe-parallel, agent-context]
vision_ref: "Framework Consumer (Embedded) persona"
type: epic-child
size: L
epic: embedding-enhancements
epic_seq: 5
epic_final: true
created: 2026-06-01
updated: 2026-06-01
---

# Feature Spec: Iterator / For-Each Node Type

## Overview

Roots processes are static YAML graphs. There's no native way to say "for each item in this list, run this subprocess." The subprocess node invokes one specific process once. If you have a list of 30 items to process, you'd need 30 hardcoded subprocess nodes — which defeats the purpose.

This feature adds an `iterator` node type that reads a list from work_item_state at runtime and runs a subprocess for each item, with configurable execution mode (sequential or parallel) and failure handling.

## Goals

- Declarative iteration over runtime-determined lists in the process graph
- Sequential and parallel execution modes
- Configurable failure handling (continue, stop, stop after N failures)
- Crash-safe: completed items preserved on restart
- Visible in the process graph (child runs queryable, lifecycle events emitted)

## User Stories

### US-001: Iterator node schema and config model

**Description:** As a process author, I want an iterator node type in the YAML schema so that I can define dynamic fan-out patterns declaratively.

**Implementation Hints:**
- Add `ITERATOR = "iterator"` to `NodeType` enum (`roots/core/schema.py:15-24`)
- **Reuse existing `ExecutionMode` enum** at `schema.py:54` (already has `PARALLEL` and `SEQUENTIAL`) — add a validator on `IteratorNodeConfig` to reject `FIRST_PASS`
- Create `IteratorNodeConfig` Pydantic model in `schema.py`:
  - `items_key: str` — key in work_item_state containing the list
  - `process_id: str` — subprocess to run per item
  - `execution_mode: ExecutionMode` — reuse existing enum; validator rejects FIRST_PASS
  - `output_key: str` — where to store results list
  - `on_item_failure: ItemFailureMode` — new StrEnum: "continue" | "stop" | "stop_after_n"
  - `max_failures: int = 1` — only for stop_after_n
  - `item_key: str` — key name for current item in child work_item_state
  - `input_mapping: dict[str, str] = {}` — additional parent state keys passed to every child (renamed from context_mapping to match SubProcessNodeConfig convention)
  - `output_mapping: dict[str, str] = {}` — optional per-item output reshaping (maps child output keys to result keys). If empty, full child output stored as-is in envelope.
  - `max_concurrency: int | None = None` — optional cap for parallel mode
  - `max_depth: int = Field(default=5, ge=1, le=20)` — max subprocess depth for iterator children; same field as SubProcessNodeConfig, checked by the same enforcement mechanism at orchestrator.py:1219
- **Register in CONFIG_MAP** at `schema.py:223`: `NodeType.ITERATOR: IteratorNodeConfig`

**Acceptance Criteria:**
- [x] `NodeType.ITERATOR` added to enum
- [x] `IteratorNodeConfig` model with all fields; reuses `ExecutionMode` enum with FIRST_PASS rejected
- [x] `ItemFailureMode` StrEnum: continue, stop, stop_after_n
- [x] Field named `input_mapping` (matching SubProcessNodeConfig convention)
- [x] `output_mapping: dict[str, str] = {}` field for optional per-item output reshaping
- [x] `max_concurrency` optional field for parallel mode
- [x] `max_depth: int = Field(default=5, ge=1, le=20)` field for depth enforcement
- [x] Registered in `CONFIG_MAP` at `schema.py:223`
- [x] Tests: schema parsing; FIRST_PASS rejection; CONFIG_MAP registration
- [x] Full test suite passes
- [x] Typecheck/lint passes

### US-002: Iterator validation and orchestrator wiring

**Description:** As a framework developer, I want the iterator node validated and dispatched correctly so that invalid configs are caught early and the orchestrator knows how to handle iterator nodes.

**Implementation Hints:**
- Add `NodeType.ITERATOR` to dispatch dict in `_dispatch_node` (`orchestrator.py:476-486`)
- **Two validator updates required:**
  1. Static self-reference check at `validator.py:200-209` — currently only handles `NodeType.SUBPROCESS`. Add `NodeType.ITERATOR` to this check.
  2. Transitive cycle detection in `validate_subprocess_references()` at `validator.py:214-252` — separate async function called by `Orchestrator.start_run`. Must also follow iterator `process_id` references.
- Add new event types to `EventType` enum in `roots/events/types.py`: `ITERATOR_STARTED`, `ITERATOR_ITEM_COMPLETED`, `ITERATOR_ITEM_FAILED`, `ITERATOR_COMPLETED`, `ITERATOR_FAILED`
- Ensure `Orchestrator.start_run()` calls `validate_subprocess_references()` which now includes iterator references

**Acceptance Criteria:**
- [x] Iterator added to node dispatch dict in orchestrator
- [x] Static self-reference check at `validator.py:200-209` includes `NodeType.ITERATOR`
- [x] Transitive cycle detection at `validator.py:214-252` follows iterator `process_id` references
- [x] Iterator event types added to EventType enum
- [x] `Orchestrator.start_run` validates iterator references transitively
- [x] Tests: dispatch works; cycle detection catches iterator→iterator; event types exist
- [x] Full test suite passes
- [x] Typecheck/lint passes

### US-003: Sequential iteration core handler

**Description:** As a framework consumer, I want sequential iteration that processes items one at a time with validation and default failure behavior.

**Implementation Hints:**
- Create `_handle_iterator` method on ProcessRunner, dispatched from `_dispatch_node`
- **Runtime validation:** If `state[config.items_key]` does not exist, raise `OrchestrationError`. If value is not a list, raise `OrchestrationError`.
- Sequential mode: for each item:
  1. Build child work_item: `{config.item_key: item, **{child_k: state[parent_k] for parent_k, child_k in config.input_mapping.items()}}`
  2. Inject `_subprocess_depth` incremented from parent; check against `config.max_depth`
  3. Create child run with `parent_run_id` and `parent_node_id` set
  4. Execute child via inner tick loop (same as subprocess handler)
  5. Persist result: `save_branch_result(run_id, node.id, f"item-{index}", "completed", result_envelope)`
  6. Emit `ITERATOR_ITEM_COMPLETED`
- **Default failure behavior:** `on_item_failure` defaults to `"stop"` — fail iterator on first child failure
- **Uniform result envelope:** `{"_item_index": int, "_status": "completed"|"failed", "_item_value": Any, "output": dict}`
- Empty list: return empty results list, emit `ITERATOR_COMPLETED`
- Lifecycle events: `ITERATOR_STARTED`, `ITERATOR_ITEM_COMPLETED`, `ITERATOR_COMPLETED`

**Acceptance Criteria:**
- [x] Sequential mode iterates over `items_key` list one at a time
- [x] Missing `items_key` raises `OrchestrationError`; non-list value raises `OrchestrationError`
- [x] `_subprocess_depth` incremented and checked (against `config.max_depth`)
- [x] Each item creates child run with `item_key` and `input_mapping` applied
- [x] Default failure behavior: fail iterator on first child failure (`on_item_failure` defaults to `"stop"`)
- [x] Results use uniform envelope: `{_item_index, _status, _item_value, output}`
- [x] Empty list produces empty result list (no error)
- [x] Lifecycle events: `ITERATOR_STARTED`, `ITERATOR_ITEM_COMPLETED`, `ITERATOR_COMPLETED`
- [x] Tests: sequential iteration; empty list; validation errors; depth enforcement; default failure; events
- [x] Full test suite passes
- [x] Typecheck/lint passes

### US-004: Sequential iteration crash recovery and pause cascading

**Description:** As a framework consumer, I want crash recovery and pause cascading for sequential iteration so that progress is preserved across failures and checkpoints propagate correctly.

**Implementation Hints:**
- **Per-item persistence:** After each item completes, persist result via `save_branch_result(run_id, node.id, f"item-{index}", status, result_envelope)`
- **Fresh vs resume detection:** Call `get_branch_results(run_id, node.id)`. If results exist → resume (skip completed items, continue from first incomplete). If empty → fresh start. This aligns with the fork/join recovery pattern (no separate state flag needed).
- `clear_branch_results` after successful completion only (preserves progress on failure for recovery)
- **Checkpoint/pause cascading:** If child run pauses at a checkpoint, cascade pause to parent using existing `SUBPROCESS_PAUSED` escalation trigger. On resume (checkpoint resolved), iterator resumes the paused child and continues from there.
- Lock renewed periodically during child tick execution

**Acceptance Criteria:**
- [x] Completed items persisted to branch storage (`save_branch_result` per item)
- [x] Crash recovery: use `get_branch_results()` presence to detect resume (NOT a state flag — aligns with fork/join pattern)
- [x] On fresh execution, `get_branch_results` returns empty → start from beginning
- [x] On resume, skip items with completed results, continue from first incomplete
- [x] `clear_branch_results` after successful completion only (preserves progress on failure)
- [x] Child run pause cascades to parent (`SUBPROCESS_PAUSED` escalation)
- [x] Lock renewed periodically during child tick execution
- [x] Tests: crash recovery with partial completion; pause cascade and resume; lock renewal
- [x] Full test suite passes
- [x] Typecheck/lint passes

### US-005: Sequential iteration — failure handling modes

**Description:** As a framework consumer, I want configurable failure behavior beyond the default stop mode for sequential iteration.

**Implementation Hints:**
- `on_item_failure` modes (sequential only in this story):
  - `"continue"`: record failed item in results, emit `ITERATOR_ITEM_FAILED`, continue to next
  - `"stop"`: halt on first failure, emit `ITERATOR_FAILED`, fail the iterator node
  - `"stop_after_n"`: count failures, halt when count reaches `max_failures`
- **Iterator node terminal state:** `"continue"` = iterator completes (even all-failed); `"stop"`/`"stop_after_n"` = iterator fails
- All failure modes persist completed results in branch storage

**Acceptance Criteria:**
- [ ] `"continue"` records failed items and processes all remaining; iterator completes
- [ ] `"stop"` halts on first failure; iterator node fails; completed results preserved
- [ ] `"stop_after_n"` halts after `max_failures`; completed results preserved
- [ ] Failed items use uniform envelope: `{_item_index, _status: "failed", _item_value, output: {_error: str}}`
- [ ] `ITERATOR_ITEM_FAILED` event emitted for each failed item
- [ ] Tests: continue with failures; stop on first; stop_after_n; all-fail with continue
- [ ] Full test suite passes
- [ ] Typecheck/lint passes

### US-006: Parallel iteration core handler

**Description:** As a framework consumer, I want parallel iteration that processes all items concurrently using crash-safe infrastructure.

**Implementation Hints:**
- Parallel mode: start child subprocess runs concurrently
- **Use `asyncio.create_task()` + manual task management** — NOT `asyncio.gather(return_exceptions=True)`. Gather cannot cancel in-flight tasks when `"stop"` mode triggers. Create tasks, track them in a list.
- **Concurrency limiting:** If `config.max_concurrency` is set, use `asyncio.Semaphore(config.max_concurrency)` to cap concurrent tasks.
- Use crash-safe branch storage: persist results as each item completes via `save_branch_result`
- On recovery: call `get_branch_results(run_id, node.id)`. If results exist → resume (only start tasks for incomplete items). If empty → fresh start. Same pattern as sequential.
- Results assembled in original order by `_item_index` regardless of completion order
- `clear_branch_results` after successful completion only (preserves progress on failure)
- Lock renewed periodically during parallel execution (same as fork handler)

**Acceptance Criteria:**
- [ ] Parallel mode starts child runs concurrently via `asyncio.create_task()` (not gather)
- [ ] `max_concurrency` caps concurrent tasks via Semaphore
- [ ] Uses crash-safe branch storage (persist as each completes)
- [ ] Recovery: `get_branch_results()` presence for resume detection (same as sequential)
- [ ] Results preserve input order by `_item_index`
- [ ] `clear_branch_results` after successful completion only
- [ ] Lock renewed periodically during parallel execution
- [ ] Tests: parallel execution; concurrency limit; crash recovery; order preservation
- [ ] Full test suite passes
- [ ] Typecheck/lint passes

### US-007: Parallel iteration failure and constraint handling

**Description:** As a framework consumer, I want failure modes and constraint handling for parallel iteration so that failures are handled correctly when multiple tasks run concurrently.

**Implementation Hints:**
- `"stop"` cancels remaining in-flight tasks on first failure via `task.cancel()`
- `"continue"` lets all tasks finish regardless of failures
- `"stop_after_n"` cancels remaining when failure count hits `max_failures` threshold
- **Checkpoint/pause in parallel:** Document that parallel mode does NOT support child checkpoint pauses. If a child process uses checkpoint nodes, use sequential mode. Rationale: pausing one of N concurrent children while others continue creates an unresolvable state (can't cascade pause to parent while siblings run). If a child pauses in parallel mode, treat as failure with clear error message.
- Emit `ITERATOR_ITEM_FAILED` events for each failed item

**Acceptance Criteria:**
- [ ] `on_item_failure` `"stop"` cancels remaining in-flight tasks
- [ ] `on_item_failure` `"continue"` lets all tasks finish regardless of failures
- [ ] `on_item_failure` `"stop_after_n"` cancels when threshold hit
- [ ] Child checkpoint in parallel mode treated as failure with clear error message (documented constraint — use sequential for checkpoints)
- [ ] `ITERATOR_ITEM_FAILED` events emitted
- [ ] Tests: stop cancellation; continue with failures; stop_after_n; checkpoint-as-failure error
- [ ] Full test suite passes
- [ ] Typecheck/lint passes

## Out of Scope

- Batched iteration (process K items at a time) — max_concurrency provides throttling
- Dynamic process_id per item (all items run the same subprocess)
- Item transformation before child run (use input_mapping; complex transforms belong in an agent)
- Custom merge strategies for results (always ordered list with uniform envelope)
- Retry at the item level (retry is child process node-level config)
- Checkpoint pauses in parallel mode children (documented constraint; use sequential mode)

## Technical Considerations

- **Dependency mapping:** US-001 of crash-safe-parallel (branch storage schema) must complete before US-004 of this spec can start. The dependency is declared in frontmatter.
- `items_key` missing from state and non-list value both raise `OrchestrationError` at runtime
- **Fresh vs resume:** Call `get_branch_results(run_id, node.id)`. If results exist → resume (skip completed items). If empty → fresh start. This aligns with the fork/join recovery pattern (no separate state flag needed).
- `max_depth` on IteratorNodeConfig — same field as SubProcessNodeConfig, checked by the same enforcement mechanism at orchestrator.py:1219
- `max_concurrency` prevents resource exhaustion on large lists. Uses `asyncio.Semaphore`.
- `_subprocess_depth` incremented per child — checked against `config.max_depth`
- Parallel mode uses `asyncio.create_task()` + `task.cancel()` (NOT `asyncio.gather`) to support mid-execution cancellation for stop/stop_after_n
- Checkpoint/pause cascading follows subprocess pattern for sequential mode; explicitly NOT supported in parallel mode (documented constraint)
- Uniform result envelope: `{"_item_index": int, "_status": str, "_item_value": Any, "output": dict}` — same shape for success and failure
- Field named `input_mapping` (not `context_mapping`) to match SubProcessNodeConfig naming convention
- `output_mapping` optional — if empty, full child output stored as-is in envelope; if populated, maps child output keys to result keys
- Child runs have `parent_run_id` and `parent_node_id` set, queryable via `get_child_runs`
- Reuses `ExecutionMode` enum (not new one) — FIRST_PASS rejected by validator

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md)

## Implementation Notes

## Refinement Notes

### Research Conducted
- `SubProcessNodeConfig` at `schema.py:215-220`: uses `input_mapping` field name
- `ExecutionMode` enum at `schema.py:54`: PARALLEL, SEQUENTIAL, FIRST_PASS
- `CONFIG_MAP` at `schema.py:223`: must register IteratorNodeConfig
- `_handle_subprocess` at `orchestrator.py:1196-1325`: inner tick loop, lock refresh, depth tracking, pause cascading
- `_dispatch_node` handler dict at `orchestrator.py:476-486`
- Static self-reference check at `validator.py:200-209`: only handles SUBPROCESS
- Transitive cycle detection at `validator.py:214-252`: separate async function — must also follow iterator refs
- `asyncio.gather(return_exceptions=True)` cannot cancel tasks — must use create_task + cancel for stop mode

### Scope Adjustments
- Split US-001 into schema (US-001) + wiring (US-002) — separate layers
- Split US-002 into core handler (US-003) + crash recovery/pause (US-004) + failure modes (US-005) — each focused
- Split parallel into core handler (US-006) + failure/constraints (US-007) — each under 10 criteria
- Renamed context_mapping → input_mapping (match SubProcessNodeConfig)
- max_depth re-added — enforcement mechanism at orchestrator.py:1219 requires config.max_depth
- output_mapping added (optional) — consumers need reshaping without shim agents
- Fresh vs resume uses get_branch_results() presence (aligns with fork/join, no separate state flag)
- Parallel mode explicitly does NOT support child checkpoint pauses (documented constraint)

### Decisions Made
- Reuse `ExecutionMode` enum — reject FIRST_PASS via validator
- `input_mapping` matches SubProcessNodeConfig naming (not context_mapping)
- Results always ordered list with uniform envelope — no asymmetric shapes
- All items fail + continue mode = iterator completes (downstream decides)
- `max_concurrency` optional — None = unlimited, explicit = Semaphore
- `asyncio.create_task()` for parallel (not gather) — enables cancellation
- Parallel mode: child checkpoint = treated as failure (documented constraint; use sequential for checkpoints)
- Fresh vs resume detection via `get_branch_results()` presence (aligns with fork/join pattern, no separate state flag)
- max_depth re-added — enforcement mechanism at orchestrator.py:1219 requires config.max_depth
- output_mapping added (optional) — consumers need reshaping without shim agents
