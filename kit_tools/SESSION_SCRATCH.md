# SESSION_SCRATCH.md

> Auto-generated. Append notes as you work. Processed on session close.

## Active Feature

**Working on:** Library Refinements Epic
**Feature Spec:** epic-library-refinements.md (3 child specs)

---

## Notes

[session] Captured Poppy integration feedback: py.typed already done, error_key added to backlog
- Files: kit_tools/roadmap/BACKLOG.md

[session] Implemented T3.9 — agent node error_key detection
- Files: roots/core/schema.py (AgentNodeConfig), roots/core/orchestrator.py (tick Step 7b), tests/test_error_key.py (9 tests)
- Decision: error_key checks agent output dict (not top-level state), uses truthiness check so None/empty/""/0/False don't trigger
- Decision: failure behavior = fail the run (matching retry-exhaustion pattern), output still stored in state before failing

[session] Planned epic: library-refinements (9 stories, 3 specs)
- Files: kit_tools/specs/epic-library-refinements.md, feature-vote-aggregation.md, feature-decision-history.md, feature-process-versioning.md
- Decision: Deferred Process Composition (T3.3) to its own epic — too large
- Decision: Iceboxed Transform Node (T3.5) — Roots orchestrates, agents compute
- Decision: Vote aggregation uses new roots/core/aggregation.py module
- Decision: Decision history injection passes pre-fetched history to DecisionEngine (engine stays storage-independent)
- Decision: Process versioning uses separate process_versions table (not PK change) for backward compat

[session] Validated epic: library-refinements — all 3 specs passed 4-reviewer validation with fixes
- Specs grew from 9 to 12 stories after splits (vote-agg: 4, decision-history: 4, process-versioning: 3)
- Key fixes: abstention denominator, tie-breaking clarity, DecisionRecord field paths, transaction atomicity, duplicate version handling, schema migration

[session] Planned epic: process-composition (8 stories, 2 specs)
- Files: kit_tools/specs/epic-process-composition.md, feature-subprocess-schema.md, feature-subprocess-execution.md
- Decision: Explicit input_mapping/output_mapping — no full state copy across process boundaries
- Decision: Parent pauses when child pauses; parent fails when child fails
- Decision: Configurable max_depth (default 5) per subprocess node
- Decision: Depth tracked via state metadata — survives serialization/crash recovery
- Decision: Child_run_id stored in parent state at _subprocess_run_{node.id} for pause/resume
- Decision: Inner tick loop (synchronous from parent) for v1 — simpler than async polling

[session] Added backlog item: In-Repo Documentation (D1, P1) — agent-friendly docs in docs/ at repo root
