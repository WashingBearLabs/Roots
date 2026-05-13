<!-- Template Version: 2.1.0 -->
---
epic: process-composition
status: active
vision_ref: "T3.3 — Process Composition"
created: 2026-05-13
updated: 2026-05-13
---

# Epic: Process Composition

## Goal

Enable hierarchical process decomposition by adding a SUBPROCESS node type whose execution is an entire child process run. This unlocks modular workflow design — complex processes composed from simpler, reusable building blocks with clean state boundaries, pause cascading, and depth-limited nesting.

## Decomposition

| Seq | Feature Spec | Status | Dependencies |
|-----|-------------|--------|--------------|
| 1 | [feature-subprocess-schema.md](feature-subprocess-schema.md) | Active (4 stories) | None |
| 2 | [feature-subprocess-execution.md](feature-subprocess-execution.md) | Active (5 stories) | subprocess-schema |

## Completion Criteria

- [ ] Both feature specs completed and archived
- [ ] Subprocess nodes execute child processes end-to-end with input/output mapping
- [ ] Child pause correctly cascades to parent (and parent resume re-enters child)
- [ ] Child failure correctly propagates to parent
- [ ] Circular process references detected at validation time
- [ ] Depth limit enforced at runtime (default 5)
- [ ] Parent/child run relationships visible via API
- [ ] All existing tests continue to pass (zero regressions)
- [ ] Product vision updated to reflect T3.3 as completed

## Notes

- State passing uses explicit input_mapping/output_mapping — no full state copy. Clean boundaries between parent and child.
- Dynamic subprocess selection (process_id from state) deferred — process_id is static in config for v1.
- Version pinning on subprocess references deferred — uses latest version (or pinned if T3.2 is implemented).
- Subprocess cancellation propagation (parent cancel → child cancel) deferred — complex lifecycle concern.
- Retry on subprocess nodes deferred — semantics unclear (retry child from scratch? resume?).
