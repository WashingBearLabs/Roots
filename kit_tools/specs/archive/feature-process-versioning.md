<!-- Template Version: 2.2.0 -->
---
feature: process-versioning
status: active
session_ready: true
depends_on: []
vision_ref: "T3.2 — Process Versioning & Migration"
type: epic-child
size: M
epic: library-refinements
epic_seq: 3
epic_final: true
created: 2026-05-13
updated: 2026-05-13
---

# Feature Spec: Process Versioning

## Overview

Currently, saving a process definition overwrites the previous version — there is no history and no way to have multiple versions coexist. Worse, runs reference processes by ID only and fetch the definition at each tick, meaning a process update mid-run silently changes what the run executes. This feature adds version history storage and pins runs to the process version that was current when they were created, ensuring runs execute deterministically regardless of subsequent process updates.

## Goals

- Store process version history so previous definitions are preserved, not overwritten
- Pin runs to the process version at creation time — updates to the process don't affect in-flight runs
- Expose version management via API for listing and retrieving specific versions
- Full backward compatibility — existing processes, runs, and API behavior unchanged for default cases

## User Stories

### US-001: Add process version history storage

**Description:** As a framework consumer, I want process definitions to be versioned so that previous versions are preserved when I update a process.

**Implementation Hints:**
- SQLite schema at `roots/storage/sqlite.py:29-121` — add process_versions table alongside existing processes table
- PostgreSQL schema at `roots/storage/postgres.py:29-150` — mirror the SQLite changes
- StorageBackend abstract class at `roots/storage/base.py` — add new abstract methods
- Keep the existing `processes` table as-is (id as PK, always holds latest) — add a separate `process_versions` table for history
- save_process currently does INSERT OR REPLACE (sqlite.py:160-177) — add an INSERT into process_versions in the same transaction
- Follow existing pattern: abstract method in base.py, implement in sqlite.py and postgres.py

**Acceptance Criteria:**
- [x] New process_versions table in both SQLite and PostgreSQL (columns: id TEXT, version TEXT, definition_json TEXT, created_at TEXT/TIMESTAMPTZ, PRIMARY KEY (id, version))
- [x] save_process wraps both the processes upsert and process_versions insert in a single explicit transaction (crash between writes must not lose version history)
- [x] Duplicate (id, version) saves use INSERT OR REPLACE on process_versions (last write wins, matches processes table upsert behavior)
- [x] get_process_version(id, version) abstract method on StorageBackend; implemented in both backends
- [x] list_process_versions(id) abstract method returns all versions ordered by created_at DESC; implemented in both backends
- [x] delete_process also deletes all version history rows for that process ID
- [x] Existing save_process/get_process behavior unchanged (backward compatible)
- [x] Tests written/updated for new functionality
- [x] Full test suite passes
- [x] Typecheck/lint passes

### US-002: Pin runs to process version at creation

**Description:** As a platform operator, I want runs to execute against the process version that existed when they were created so that updating a process definition doesn't break in-flight runs.

**Implementation Hints:**
- RunRecord dataclass at `roots/storage/base.py:46-54` — add process_version field
- runs table schema in sqlite.py and postgres.py — add process_version column (nullable for backward compat)
- create_run at `roots/storage/sqlite.py:240-269` — fetch process, store its version
- Orchestrator tick at `roots/core/orchestrator.py:143-146` (Step 3: Load process definition) — use get_process_version when run has a pinned version
- Orchestrator.start_run at `roots/core/orchestrator.py:1075-1094` — process is already fetched here, version available

**Acceptance Criteria:**
- [x] process_version TEXT column added to runs table in both backends (nullable for existing runs); initialize() adds column via ALTER TABLE IF NOT EXISTS for databases created before this feature
- [x] RunRecord dataclass includes process_version: str | None field
- [x] create_run stores ProcessDefinition.version as the run's process_version (create_run abstract signature updated to accept process version)
- [x] Orchestrator tick loads process via get_process_version(process_id, run.process_version) when pinned version exists
- [x] Falls back to get_process(process_id) for runs with process_version=None (backward compat with pre-existing runs)
- [x] If a pinned version is not found in process_versions (deleted), orchestrator falls back to get_process and logs a warning
- [x] Test: create run, update process definition, tick run — run uses original definition, not updated one
- [x] Tests written/updated for new functionality
- [x] Full test suite passes
- [x] Typecheck/lint passes

### US-003: Process version management API

**Description:** As a platform operator, I want to list and retrieve specific process versions via the REST API so that I can audit process evolution and inspect what version a run executed against.

**Implementation Hints:**
- Process routes at `roots/api/routers/processes.py` — add version sub-routes
- Follow existing pattern: ProcessSummary/ProcessDetail response models in the router
- RunRecord API responses already serialize via Pydantic — adding process_version field to the response model is straightforward
- Register new routes in existing processes router (no new router needed)

**Acceptance Criteria:**
- [x] GET /processes/{id}/versions returns all versions (version string + created_at); version sub-routes added to existing processes router
- [x] GET /processes/{id}/versions/{version} returns full process definition for that version (version passed as path param, URL-encoded if needed)
- [x] Returns 404 when process ID or version not found
- [x] Run API responses include process_version field (ProcessRunResponse or equivalent updated)
- [x] Tests written/updated for new functionality
- [x] Full test suite passes
- [x] Typecheck/lint passes

## Out of Scope

- Live migration of in-flight runs to a new process version (runs are pinned, period)
- Automatic version incrementing (version string is user-controlled, like today)
- Version diffing or comparison tools
- Rollback (deleting the latest version to "revert" — users can just save the old definition again)
- Garbage collection of old versions
- Version constraints or semver enforcement (version is a free-form string)

## Technical Considerations

- **Separate table approach:** A new `process_versions` table stores history while the existing `processes` table continues to hold the latest version. This avoids changing the PK of `processes` (which would cascade to every query) and keeps all existing code working.
- **Transaction atomicity:** save_process must wrap both the processes upsert and process_versions insert in a single transaction. The existing code commits after each statement — this feature must break that pattern to ensure crash safety.
- **Duplicate version handling:** INSERT OR REPLACE on process_versions — if user saves same (id, version) twice, last write wins. This matches the processes table upsert behavior and prevents constraint errors during development iteration.
- **Schema migration for runs table:** The `runs` table uses CREATE TABLE IF NOT EXISTS, which won't add new columns. The `initialize()` method must ALTER TABLE to add `process_version` for existing databases. SQLite supports ALTER TABLE ADD COLUMN; use a try/except or column-exists check for idempotency.
- **Nullable process_version on runs:** Existing runs in the database won't have a process_version. The orchestrator falls back to get_process() (latest) for these, maintaining backward compatibility. Note: for pre-existing runs where the process has since changed, this means they'll use the latest definition — this is the existing behavior and is documented as acceptable.
- **Deleted version fallback:** If a run's pinned version was deleted (via delete_process cascade), the orchestrator falls back to get_process() and logs a warning. This is a graceful degradation, not a crash.
- **No foreign key constraints:** The existing schema doesn't use FK constraints between runs and processes. Maintaining this pattern — version pinning is advisory, not enforced at the DB level.
- **Storage overhead:** Each save_process call now inserts a version history row. For processes updated frequently, this could grow. Garbage collection is explicitly out of scope for v1 but noted as a future concern.

## Design Considerations

N/A — no UI components.

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md)

## Implementation Notes

<!-- Populated during implementation -->

## Refinement Notes

### Research Conducted
- processes table uses id TEXT as PK with INSERT OR REPLACE — only one version per id currently
- RunRecord has no version reference — runs fetch process definition fresh at each tick via get_process(run.process_id)
- save_process in both backends is a simple upsert; no transaction wrapping needed beyond what aiosqlite/asyncpg provide
- PostgreSQL uses ON CONFLICT (id) DO UPDATE — same upsert pattern as SQLite
- No FK constraints exist between runs.process_id and processes.id

### Scope Adjustments
- Originally considered changing processes PK to (id, version) — rejected, too invasive
- Excluded live migration — "pin at creation" is the user's stated preference and much simpler
- Excluded version diffing — useful but separate concern
- Considered surrogate autoincrement PK for process_versions — rejected, (id, version) string PK is simpler and INSERT OR REPLACE handles duplicates cleanly

### Decisions Made
- Separate process_versions table preserves all existing code paths — zero changes to get_process, list_processes
- delete_process cascades to delete version history rows (no orphaned versions)
- Duplicate (id, version) saves use INSERT OR REPLACE (last write wins) — prevents constraint errors during dev iteration
- save_process wraps both writes in an explicit transaction — breaks the existing single-commit pattern for crash safety
- initialize() uses ALTER TABLE ADD COLUMN for `process_version` on runs — handles databases created before this feature
- Nullable process_version on RunRecord maintains backward compat with existing database rows
- Orchestrator fallback to latest when process_version is None or pinned version deleted — graceful degradation with warning log
- create_run abstract signature updated to accept version — this is a breaking change to the StorageBackend interface but all implementations are internal

## Open Questions

None — all design questions resolved during planning and validation review.
