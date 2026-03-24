<!-- Template Version: 2.0.0 -->
---
feature: fork-join
status: active
session_ready: true
depends_on: [storage-backend, orchestrator-engine]
vision_ref: "T2.2 — Fork/Join Parallel Execution"
type: epic-child
epic: roots-v1
epic_seq: 8
epic_final: false
created: 2026-03-23
updated: 2026-03-23
---

# Feature Spec: Fork/Join Parallel Execution

## Overview

Fork/join enables parallel branch execution within a process graph. A fork node splits execution into N parallel branches, each with a copy of the work item state. A join node waits for all branches and merges their outputs. Two merge strategies are supported: merge_all (deep merge) and collect (list under a key). The allow_partial flag enables tolerance for branch failures.

## Goals

- Implement fork node that creates parallel branch execution contexts
- Implement join node with two merge strategies
- Handle partial failure gracefully when configured

## User Stories

### US-001: Fork Node — Branch Creation

**Description:** As a framework developer, I want fork nodes to create parallel branch contexts so that independent work can execute concurrently.

**Implementation Hints:**
- Replace the `NotImplementedError` in ProcessRunner's fork handler
- When orchestrator hits a fork node:
  - Get all outbound edges from the fork node (from top-level edges list)
  - Each edge target is a branch entry point
  - For each branch: create a branch context with `copy.deepcopy(work_item_state)` — true deep copy so mutations in one branch never affect another
  - Branch context structure: `{"branch_id": f"branch-{i}", "entry_node_id": edge.to_node, "state": deep_copied_state}`
  - Store branch contexts in a local list (NOT in work item state — branches are ephemeral execution state)
  - Look up the matching join node via `process._fork_join_map[fork_node.id]` (computed during schema validation in T1.1 US-008) — pass this join_node_id to each branch executor so they know where to stop
- Emit `roots.node.completed` for the fork node after branches are set up

**Acceptance Criteria:**
- [x] Fork node identifies all outbound edges as branches
- [x] Each branch gets a deep copy of work item state
- [x] Branch metadata is tracked (branch_id, entry node)
- [x] Fork node with 0 outbound edges raises OrchestrationError
- [x] Tests verify branch creation for 2 and 3+ branches

### US-002: Parallel Branch Execution

**Description:** As a framework developer, I want branches to execute concurrently so that parallel work completes as fast as the slowest branch.

**Implementation Hints:**
- After fork creates branch contexts, execute all branches via `asyncio.gather`:
  - Create a `async _execute_branch(branch_context, join_node_id) -> dict` method on ProcessRunner
  - This method runs a mini execution loop: starting from `entry_node_id`, execute nodes sequentially against the branch's state copy, advancing via edge evaluation, until `current_node_id == join_node_id` (stop — don't execute the join itself)
  - Reuse the same node handlers (`_handle_agent`, `_handle_decision`, etc.) — they work on any state dict
  - Return the branch's final accumulated state dict
  - `asyncio.gather(*[_execute_branch(ctx, join_id) for ctx in branches], return_exceptions=True)` to collect results
- Each branch produces its final state dict. Exceptions are captured (not raised) by `return_exceptions=True`.
- Track timing per branch for events
- **Branch context in events:** All events emitted during branch execution must include `branch_id` in the event metadata dict. Without this, an event consumer cannot distinguish which branch a `roots.node.entered` event belongs to. Pass `branch_id` into `_execute_branch` and thread it through to the event emitter as `metadata={"branch_id": branch_id}`.

**Acceptance Criteria:**
- [x] All branches execute concurrently via asyncio.gather
- [x] Each branch maintains its own independent state
- [x] Branch execution stops at the join node
- [x] Branch results (state dicts) are collected
- [x] Branch exceptions are captured (not raised immediately)
- [x] Events emitted during branches include branch_id in metadata
- [x] Tests verify concurrent execution with timing

### US-003: Join Node — merge_all Strategy

**Description:** As a framework developer, I want the merge_all join strategy so that branch outputs are deep-merged into a single state dict.

**Implementation Hints:**
- Replace the `NotImplementedError` in ProcessRunner's join handler
- Implement deep merge utility: `deep_merge(base: dict, override: dict) -> dict`
  - Recursively merge dicts
  - For non-dict values: last writer wins (branch order determines priority)
  - For lists: override replaces (not concatenation)
- For `merge_all`: iterate branches in order, deep-merge each branch's output state into the accumulated result
- Write merged state back to work item state
- Continue execution from the join node's outbound edge

**Acceptance Criteria:**
- [x] Branch outputs are deep-merged in branch order
- [x] Nested dicts are merged recursively
- [x] Non-dict conflicts are resolved by last-writer-wins (branch order)
- [x] Merged state is written back to work item
- [x] Execution continues from join's outbound edge
- [x] Tests verify merge with overlapping keys, nested structures

### US-004: Join Node — collect Strategy

**Description:** As a framework developer, I want the collect join strategy so that branch outputs are gathered into a list for downstream processing.

**Implementation Hints:**
- For `collect` merge strategy:
  - Collect all branch output states into a list
  - Write to `work_item_state[collect_key]` where `collect_key` comes from join node config
  - Each entry in the list is the full branch output state
  - Include branch metadata (branch_id, entry_node) alongside the output
- Format: `work_item_state[collect_key] = [{"branch_id": "branch-0", "state": {...}}, {"branch_id": "branch-1", "state": {...}}, ...]`
- Failed branches (when `allow_partial` is True): include with `{"branch_id": "branch-2", "state": null, "error": "error message"}`

**Acceptance Criteria:**
- [x] Branch outputs collected as list under configured key
- [x] Each list entry includes branch metadata and state
- [x] `collect_key` is used from join node config
- [x] Order matches branch order (deterministic)
- [x] Tests verify collect with heterogeneous branch outputs

### US-005: Partial Failure Handling

**Description:** As a framework developer, I want the allow_partial flag so that fork/join can tolerate branch failures when configured.

**Implementation Hints:**
- When `allow_partial` is False (default):
  - Any branch failure → fail the entire run
  - Emit `roots.run.failed` event with failed branch details
- When `allow_partial` is True:
  - Failed branches are recorded in work item state under `_failed_branches`
  - Successful branches are merged normally (merge_all or collect)
  - If ALL branches fail, fail the run regardless of allow_partial
  - Emit `roots.node.completed` for the join with metadata noting partial completion
  - Failed branch info: `{ branch_id, entry_node, error_message }`
- The `return_exceptions=True` in asyncio.gather makes this possible — check each result for exceptions

**Acceptance Criteria:**
- [x] `allow_partial: false` fails run on any branch failure
- [x] `allow_partial: true` continues with successful branches
- [x] Failed branch info recorded in work item state
- [x] All branches failing fails the run even with allow_partial
- [x] Join metadata indicates partial completion
- [x] Tests cover: all success, partial failure (allowed), partial failure (not allowed), all failure

## Known Limitations (v1)

**Fork/join is NOT crash-safe.** The tick-based crash safety model applies to the orchestrator's main loop, but branch execution within `_execute_branch` runs a mini-loop to completion in memory. If the orchestrator crashes during fork/join:
- All branch progress is lost
- On restart, the orchestrator re-enters the fork node and re-executes all branches from scratch
- For agent nodes with side effects (HTTP calls, database writes), this means potential double-execution

This is a conscious v1 trade-off. Making fork/join crash-safe requires persisting per-branch state to storage at each node, which adds significant complexity. Documented here so consumers know the limitation. Phase 2 can add branch-level persistence if demand warrants it.

## Out of Scope

- Nested fork/join (Phase 2 — process composition)
- Dynamic branch creation (branches are static from the process definition)
- Branch-level crash safety / persistence (see Known Limitations above)
- Cancellation of remaining branches when one fails (all branches run to completion)

## Technical Considerations

- Deep copy of work item state must be a true deep copy (`copy.deepcopy`) — mutations in one branch must not affect others
- `asyncio.gather` with `return_exceptions=True` is essential — without it, first failure cancels all branches
- Branch execution reuses the same node handlers (agents, decisions, etc.) — no special branch-aware logic needed in handlers
- The join node must be the convergence point — the schema validator (T1.1 US-006) already enforces this

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
