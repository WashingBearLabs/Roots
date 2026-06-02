<!-- Template Version: 2.2.0 -->
---
feature: crash-safe-parallel
status: active
session_ready: true
depends_on: []
vision_ref: "Crash safety success criterion"
type: epic-child
size: L
epic: embedding-enhancements
epic_seq: 4
epic_final: false
created: 2026-06-01
updated: 2026-06-01
---

# Feature Spec: Crash-Safe Parallel Execution

## Overview

Fork/join and parallel agent_pool currently execute branches via `asyncio.gather()` with results held in ProcessRunner instance variables. A crash during parallel execution loses all in-flight branch progress. This is documented as a v1 limitation in BACKLOG.md and GOTCHAS.md.

This feature adds per-branch state checkpointing to storage so that the orchestrator can resume from the last completed branch rather than re-executing all branches from scratch.

## Goals

- Per-branch state persistence so completed branches survive orchestrator crashes
- Transparent crash recovery — restart resumes only incomplete branches
- Apply the same pattern to both fork/join and parallel agent_pool
- Prevent nested fork/join corruption
- Backward compatible — existing YAML processes work without changes

## User Stories

### US-001: Branch state storage schema

**Description:** As a framework developer, I want a storage schema for persisting per-branch results so that crash recovery can identify which branches completed.

**Implementation Hints:**
- Add abstract methods to `StorageBackend` (`roots/storage/base.py`): `save_branch_result(run_id, node_id, branch_id, status, result)`, `get_branch_results(run_id, node_id) -> list[BranchResult]`, `clear_branch_results(run_id, node_id)`
- Create `BranchResult` dataclass in `base.py`: `run_id`, `node_id`, `branch_id`, `status` ("completed" | "failed"), `result_json` (dict for success, str for error), `created_at`
- **All branch storage operations use the fork node's ID** as `node_id` — critical for recovery consistency
- **Branch IDs derived from target node ID** (e.g., `"branch:{target_node_id}"` for fork, `"agent:{agent_name}"` for pool) — NOT positional index. Edge ordering from storage may not be deterministic; target node IDs are stable.
- SQLite: `CREATE TABLE IF NOT EXISTS branch_results (...)` in `initialize()`; PostgreSQL: same
- `save_branch_result` uses UPSERT (INSERT ON CONFLICT UPDATE) for idempotency
- `clear_branch_results` deletes all rows for (run_id, node_id)
- Add `branch_results` to PostgreSQL TRUNCATE in test cleanup (`tests/conftest.py:56-58`)

**Acceptance Criteria:**
- [ ] `StorageBackend` has abstract methods: `save_branch_result`, `get_branch_results`, `clear_branch_results`
- [ ] `BranchResult` dataclass with `run_id`, `node_id`, `branch_id`, `status`, `result_json`, `created_at`
- [ ] SQLite and PostgreSQL implementations with `branch_results` table
- [ ] `save_branch_result` is idempotent (UPSERT semantics)
- [ ] `get_branch_results` returns all branches for `(run_id, node_id)`, ordered by branch_id
- [ ] Tests: save/get/clear round-trip; UPSERT overwrites; clear removes all; test cleanup includes branch_results
- [ ] Full test suite passes
- [ ] Typecheck/lint passes

### US-002: Crash-safe fork — branch persistence

**Description:** As a framework developer, I want the fork handler to persist each branch result as it completes so that completed branches survive a crash.

**Implementation Hints:**
- Modify `_handle_fork` (`orchestrator.py:945-996`) to persist branch results incrementally
- Wrap each `_execute_branch` call in a coroutine that: (1) executes the branch, (2) calls `save_branch_result(run_id, fork_node.id, f"branch:{target_node_id}", "completed", result)`, (3) on exception, calls `save_branch_result(..., "failed", str(error))`
- **Lock management and tick() finally-block interaction:** Use lock RENEWAL during parallel execution (same pattern as subprocess handler at orchestrator.py:1270-1275 which releases and immediately reacquires between ticks). For fork: renew the lock periodically during gather by spawning a background renewal task that calls release_run_lock + acquire_run_lock every stale_timeout_seconds/2. This prevents lock expiry without fully releasing (which allows race conditions with other orchestrators). Cancel the renewal task after gather completes. If renewal ever fails (lock stolen), cancel all branch tasks and raise `OrchestrationError("Lock lost during parallel execution")`.
- Failed branches are persisted with error details and re-executed on recovery (transient errors)

**Acceptance Criteria:**
- [ ] Fork handler persists each branch result to storage (using fork node ID and target-node-derived branch ID)
- [ ] Lock renewed periodically during parallel execution via background task (prevents expiry without full release)
- [ ] Background renewal task cancelled after gather completes; if renewal ever fails (lock stolen), cancel all branch tasks and raise OrchestrationError
- [ ] Failed branches persisted with error details
- [ ] Existing fork/join tests continue to pass
- [ ] Tests: normal persistence; lock renewal during gather; lock-stolen cancels branches
- [ ] Full test suite passes
- [ ] Typecheck/lint passes

### US-003: Crash-safe fork — recovery on re-entry

**Description:** As a framework developer, I want the fork and join handlers to recover from crashes by loading completed branches from storage.

**Implementation Hints:**
- **Fork re-entry:** Before starting branches, check `get_branch_results(run_id, fork_node.id)`. If results exist, filter out branches with `status="completed"`. **Failed branches are re-executed** (may have been transient). Merge fresh results with recovered results keyed by branch_id, assembled in original branch order.
- **Join handler recovery:** If `self._fork_branch_results` is None (crash happened after fork, current_node_id is join), load from storage. Resolve fork node ID: `next(fid for fid, jid in process.fork_join_map.items() if jid == node.id)`. Normalize `BranchResult` objects to dicts before merge strategy processing.
- **Cleanup on success only:** `clear_branch_results(run_id, fork_node_id)` after SUCCESSFUL join completion only (not on failure). Failed attempts leave branch_results in storage so the next recovery attempt can skip already-completed branches. This is the entire point of checkpointing.
- Merge strategies (`MERGE_ALL`, `COLLECT`) must work identically with recovered data

**Acceptance Criteria:**
- [ ] Fork handler detects completed branches via `get_branch_results` and skips them
- [ ] Failed branches are re-executed on recovery (not skipped)
- [ ] Join handler loads from storage when `_fork_branch_results` is None (crash at join node)
- [ ] Join resolves fork node ID via `fork_join_map` inverse for storage lookup
- [ ] `clear_branch_results` called after successful join only (not on failure — preserves progress for next recovery attempt)
- [ ] Merge strategies produce correct results with recovered data
- [ ] Crash-recovery integration test: simulate crash mid-fork, verify partial recovery and correct join
- [ ] Full test suite passes
- [ ] Typecheck/lint passes

### US-004: Crash-safe parallel agent_pool — persistence

**Description:** As a framework developer, I want parallel agent_pool execution to persist each agent's result so that completed agents aren't re-invoked after a crash.

**Implementation Hints:**
- Modify `_pool_parallel` (`orchestrator.py:661-697`) to use the same branch storage pattern as fork
- Branch IDs: `"agent:{agent_name}"` (stable, not positional)
- Wrap each agent invocation to persist result on completion/failure via `save_branch_result`
- **Result normalization:** `BranchResult.result_json` must store the full agent output dict including `escalate` and `escalation_reason` fields so they survive round-trip. On recovery, reconstruct `AgentOutput`-compatible dicts from `BranchResult.result_json`.
- Lock management: same renewal pattern as fork — spawn background renewal task during gather that calls release_run_lock + acquire_run_lock every stale_timeout_seconds/2. Cancel after gather; if renewal fails (lock stolen), cancel all agent tasks and raise OrchestrationError.
- `clear_branch_results` after SUCCESSFUL pool completion only (not on failure). Failed attempts leave branch_results in storage so the next recovery attempt can skip already-completed agents. This is the entire point of checkpointing.

**Acceptance Criteria:**
- [ ] Parallel agent_pool persists each agent result to branch storage with stable branch IDs
- [ ] Lock renewed periodically during parallel execution via background task (prevents expiry without full release)
- [ ] `BranchResult.result_json` preserves `escalate` and `escalation_reason` fields
- [ ] `clear_branch_results` called after successful pool completion only (not on failure — preserves progress for next recovery attempt)
- [ ] Tests: normal persistence; result round-trip with escalate flag
- [ ] Full test suite passes
- [ ] Typecheck/lint passes

### US-005: Crash-safe parallel agent_pool — recovery

**Description:** As a framework developer, I want agent_pool crash recovery to correctly restore results, re-invoke missing agents, and handle escalation without duplication.

**Implementation Hints:**
- Before invoking agents, check `get_branch_results(run_id, node.id)` — skip agents with `status="completed"`. **Failed agents are re-executed** (same semantics as fork, not skip).
- Normalize recovered `BranchResult` objects to `AgentOutput`-compatible format before vote aggregation or merge_all
- **Escalation de-duplication:** wrap create_escalation call in try/except StorageError — the storage layer already raises StorageError if a pending escalation exists for the run (sqlite.py:596-599). Catch and skip. Do NOT extend the get_pending_escalation API.
- Vote aggregation (`aggregate_votes`) and merge_all must produce correct results with recovered data

**Acceptance Criteria:**
- [ ] On recovery, completed agents loaded from storage and not re-invoked
- [ ] Failed agents are re-executed (same as fork semantics)
- [ ] Recovered results normalized to `AgentOutput`-compatible format
- [ ] Vote aggregation and merge_all produce correct results with recovered data
- [ ] Escalation de-duplication: catch StorageError from create_escalation (existing duplicate detection) — no double-escalation on recovery
- [ ] Tests: recovery with partial agents; escalation round-trip; vote aggregation with recovered results
- [ ] Full test suite passes
- [ ] Typecheck/lint passes

### US-006: Nested fork/join guard

**Description:** As a framework developer, I want to prevent nested fork/join patterns to avoid corruption from nested parallel regions.

**Implementation Hints:**
- Schema-level validation: in `roots/core/validator.py`, during structural validation (where `fork_join_map` is computed), detect if any node in a fork branch is itself a fork node. Use `ProcessValidationError` (at `validator.py:25`)
- Walk each branch from fork to join; if any intermediate node is `NodeType.FORK`, raise `ProcessValidationError`
- **Runtime guard:** Use `self._in_fork_branch: bool = False` on ProcessRunner. Set to True in `_execute_branch`, reset after. In `_handle_fork`, check this flag. Do NOT use `_fork_branch_results is not None` (it's None during execution). Add a code comment explaining why the runtime guard exists alongside the schema guard (defense in depth against programmatic process construction that bypasses schema validation).
- Subprocess nodes inside fork branches ARE allowed (separate run lifecycle)

**Acceptance Criteria:**
- [ ] Schema validator detects nested fork/join and raises `ProcessValidationError`
- [ ] Runtime guard uses `_in_fork_branch` flag with code comment explaining rationale
- [ ] Error message clearly explains that nested fork/join is not supported
- [ ] Subprocess nodes inside fork branches remain allowed
- [ ] Tests: schema validation catches nested fork; runtime guard catches bypass; subprocess in fork branch allowed
- [ ] Full test suite passes
- [ ] Typecheck/lint passes

## Out of Scope

- Distributed parallel execution across multiple processes/machines
- Branch-level retry policies (retry is node-level, applies within branches)
- Partial join timeout (wait N seconds for slow branches, then join with available)
- Nested fork/join support (explicitly guarded against)
- Sequential/first_pass agent_pool crash safety (already crash-safe per-tick)
- Cascading branch_results cleanup on run deletion (no `delete_run` method exists on StorageBackend; branch_results are cleaned up on successful join/pool completion)

## Technical Considerations

- **Branch IDs derived from target node ID** (fork) or agent name (pool) — stable across storage round-trips. Positional IDs (`branch-0`, `branch-1`) are fragile if edge ordering changes.
- **All branch storage operations use the fork node's ID** — the join handler resolves this via `fork_join_map` inverse
- **Lock renewal during parallel execution:** Fork/pool spawn a background asyncio task that calls release_run_lock + acquire_run_lock every stale_timeout_seconds/2 during gather. This keeps the lock alive without fully releasing it (which would allow race conditions with other orchestrators). The renewal task is cancelled after gather completes. tick()'s finally block still releases the lock normally.
- **Lock-steal during parallel execution:** If the background renewal task fails to reacquire, all branch tasks are cancelled and OrchestrationError propagates to the tick-level error handler which marks the run failed. Next orchestrator picks it up cleanly for recovery.
- `clear_branch_results` on success only — failure preserves checkpointed progress so the next recovery attempt can skip already-completed branches (the entire point of checkpointing)
- Failed branches/agents are re-executed on recovery (both fork and pool use same semantics)
- Escalation de-duplication via try/except StorageError on create_escalation (storage layer has existing duplicate detection at sqlite.py:596-599) — not via get_pending_escalation or run.status check
- `BranchResult.result_json` stores full branch state (fork) or agent output with escalate flag (pool) — must round-trip correctly
- Branch results scoped by `(run_id, node_id)` — safe for subprocess runs containing their own forks

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md) — Fork/Join crash safety gotcha (#1)
- Audit Findings: [AUDIT_FINDINGS.md](../AUDIT_FINDINGS.md) — 2026-05-19-002 (lock race window)

## Implementation Notes

## Refinement Notes

### Research Conducted
- Fork handler at `orchestrator.py:945-996`: uses `asyncio.gather(*[self._execute_branch(...)])` with `return_exceptions=True`
- Branch state stored in `self._fork_branch_results` (instance variable, line 108) — pure in-memory
- `_execute_branch` at `orchestrator.py:998-1093`: mini execution loop from entry to join
- Join handler at `orchestrator.py:1095-1194`: classifies results, applies merge strategy
- `_pool_parallel` at `orchestrator.py:661-697`: same gather pattern, deep copies state per agent
- `tick()` finally block at `orchestrator.py:457-460`: always releases lock — fork renewal task keeps lock alive during gather
- Subprocess handler lock renewal pattern at `orchestrator.py:1270-1275`: release + immediate reacquire between ticks
- `ProcessValidationError` at `validator.py:25` (not SchemaValidationError)
- `_fork_branch_results` is None during branch execution (only set after gather returns)
- `create_escalation` in sqlite.py:596-599: raises StorageError if pending escalation already exists for the run (built-in duplicate detection)
- Test cleanup at `conftest.py:56-58`: TRUNCATE statements need branch_results table

### Scope Adjustments
- Split US-002 into persistence (US-002) and recovery (US-003) — each under 10 criteria
- Split US-004 into persistence (US-004) and recovery (US-005) — each under 10 criteria
- US-005 (old) renumbered to US-006 (nested guard)
- Removed cascading delete reference (delete_run doesn't exist)

### Decisions Made
- Branch IDs from target node/agent name (not positional index) — stable across storage round-trips
- Lock renewed periodically during parallel execution (not fully released — prevents race with other orchestrators)
- Lock-stolen during gather cancels all branch tasks and raises OrchestrationError (not silent continuation)
- Failed branches/agents re-executed on recovery (both fork and pool, same semantics)
- Escalation de-duplication via try/except StorageError on create_escalation (not get_pending_escalation or run.status check)
- Runtime nesting guard uses _in_fork_branch flag (not _fork_branch_results check)
- clear_branch_results on success only (failure preserves checkpointed progress)
