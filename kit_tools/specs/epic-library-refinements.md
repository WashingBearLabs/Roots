<!-- Template Version: 2.1.0 -->
---
epic: library-refinements
status: completed
vision_ref: "Tier 3 — Future (Phase 2): T3.1, T3.2, T3.4"
created: 2026-05-13
updated: 2026-06-25
completed: 2026-05-13
---

# Epic: Roots Library Refinements (v1.1)

## Goal

Advance the Roots framework beyond v1 core with three internal library improvements: vote aggregation for agent pool consensus, decision history retrieval for pattern-based AI learning, and process versioning for safe concurrent process evolution. These refinements strengthen Roots' orchestration capabilities without introducing community-facing infrastructure (registry, marketplace) or new node types.

## Decomposition

| Seq | Feature Spec | Status | Dependencies |
|-----|-------------|--------|--------------|
| 1 | [feature-vote-aggregation.md](archive/feature-vote-aggregation.md) | Completed (4 stories) | None |
| 2 | [feature-decision-history.md](archive/feature-decision-history.md) | Completed (4 stories) | None |
| 3 | [feature-process-versioning.md](archive/feature-process-versioning.md) | Completed (3 stories) | None |

## Completion Criteria

- [x] All three feature specs completed and archived
- [x] Vote aggregation works with all agent pool execution modes (parallel, sequential)
- [x] Decision history auto-injects into AI prompts and is queryable via API
- [x] Process versioning pins runs to their creation-time version with full backward compatibility
- [x] All 1,191+ existing tests continue to pass (zero regressions)
- [x] Product vision updated to reflect T3.1, T3.2, T3.4 as completed

## Notes

- Process Composition (T3.3) was explicitly deferred — it needs its own epic due to Large effort and sub-process lifecycle complexity.
- Transform Node (T3.5) was iceboxed — Roots orchestrates, agents compute.
- Root Registry (T3.8) is community-facing and out of scope for this epic.
- Agent Node Error Key (T3.9) was completed 2026-05-13 as a standalone item before this epic was planned.
