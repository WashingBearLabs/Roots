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

[$(date +%H:%M)] Ran validate-implementation for feature-decision-history (autonomous, epic mode)
- Result: 5 warnings, 5 info, 0 critical. Tests: 1275 pass, 0 fail.
- Compliance: clean — all US-001 through US-004 criteria met.
- Quality warnings: DecisionHistoryResponse inline (not in models.py); limit unbounded; mode untyped (should be DecisionMode enum).
- Security warnings: unbounded limit (DoS); prompt injection via unsanitized history reasoning in build_decision_messages.
- Findings logged as 2026-05-13-013 through -023 in AUDIT_FINDINGS.md. No autopause (no criticals). Did NOT invoke complete-implementation (epic flag).

[session] Implemented subprocess-execution US-001 — happy path handler
- Files: roots/core/orchestrator.py (_handle_subprocess replaced stub), tests/test_orchestrator_class.py (8 new tests)
- Decision: Lock refresh = release_run_lock + acquire_run_lock between child ticks (acquire_run_lock only refreshes stale locks, so release first is required)
- Decision: Handler returns raw result dict; tick loop stores it at output_key automatically

[session] Implemented US-001: Add process version history storage (feature-process-versioning)
- Files: roots/storage/base.py (ProcessVersionRecord + 2 abstract methods), roots/storage/sqlite.py, roots/storage/postgres.py, tests/test_process_versioning.py (13 tests)
- Decision: aiosqlite async with conn context manager re-opens connection (breaks) — used two execute() + commit() for implicit transaction instead
- Decision: list_process_versions returns lightweight ProcessVersionRecord (id/version/created_at); full definition via get_process_version

[session] Implemented US-002: Pin runs to process version at creation
- Files: roots/storage/base.py (RunRecord.process_version field + updated create_run sig), roots/storage/sqlite.py, roots/storage/postgres.py, roots/core/orchestrator.py (start_run passes version; tick Step 3 uses get_process_version with fallback), tests/test_process_versioning.py (6 new pinning tests)
- Decision: SQLite ALTER TABLE uses try/except (no IF NOT EXISTS support); Postgres uses IF NOT EXISTS
- Decision: process_version: str | None = None placed as last field in RunRecord dataclass to avoid ordering error

[session] Implemented US-003: Process version management API
- Files: roots/api/models.py (ProcessVersionSummary + process_version in RunResponse), roots/api/routers/processes.py (2 new routes), roots/api/routers/runs.py (_run_to_response includes process_version), tests/test_process_routes.py (6 new tests), tests/test_run_routes.py (1 new test)
- Decision: list versions route checks get_process() first to return 404 for unknown process IDs (not empty list)
- Decision: get version route delegates 404 detection entirely to get_process_version() returning None

[15:42] validate-implementation autonomous run for feature-subprocess-schema
- Result: 1 critical (test_handlers.py missing SUBPROCESS in dispatch dict) — fixed inline, full suite green (1332 passed)
- Remaining: 2 warnings + 6 info — logged as 2026-05-15-001..009 in AUDIT_FINDINGS.md
- Compliance: clean across all 4 user stories (US-001..US-004)
- Decision: epic_mode=true — did not invoke complete-implementation
