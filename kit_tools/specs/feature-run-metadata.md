<!-- Template Version: 2.2.0 -->
---
feature: run-metadata
status: active
session_ready: true
depends_on: []
vision_ref: "Framework Consumer (Embedded) persona"
type: epic-child
size: M
epic: embedding-enhancements
epic_seq: 1
epic_final: false
created: 2026-06-01
updated: 2026-06-01
---

# Feature Spec: Run Metadata and Tagging

## Overview

Runs currently have no way to attach application-specific metadata (e.g., "this run is for epic X, story Y" or "triggered by user Z"). Applications running the same process hundreds of times for different entities need to group, filter, and identify runs beyond just process_id and run_id.

This feature adds an optional metadata dict to runs with filtering operators that work consistently across both SQLite and PostgreSQL backends.

## Goals

- Allow embedding applications to attach metadata to runs at creation time
- Support metadata filtering ($eq, $in, $exists) with consistent cross-backend behavior
- Expose metadata through both the embedded Python API and the REST API

## User Stories

### US-001: Add metadata to storage and Roots class

**Description:** As a framework consumer, I want to attach metadata to runs at creation time so that I can tag runs with application-specific identifiers.

**Implementation Hints:**
- Add `metadata: dict[str, Any] | None = None` as last field on `RunRecord` dataclass (`roots/storage/base.py:24-34`) to avoid dataclass ordering errors
- Add `metadata` parameter to `StorageBackend.create_run` abstract method (`base.py:167-175`) — keyword-only after existing params
- SQLite: add `metadata_json TEXT` column to runs table, use `json.dumps()`/`json.loads()` — same pattern as `work_item_state_json` (`sqlite.py:317-358`)
- PostgreSQL: add `metadata JSONB` column to runs table (`postgres.py:357-396`)
- Both backends need ALTER TABLE migration in `initialize()` — SQLite: catch specific `OperationalError` for "duplicate column name" (not bare except); PostgreSQL: use `ADD COLUMN IF NOT EXISTS` (see `postgres.py:174-185`)
- Thread metadata through `Orchestrator.start_run` (`orchestrator.py:1347-1372`) → `storage.create_run` and `Roots.start_run` (`roots/__init__.py:151-155`)
- **Two create_run call sites in orchestrator:** user-facing at line 1362 (needs metadata), subprocess creation at line 1238 (does NOT pass metadata — child runs get None)
- **Update ALL RunRecord construction sites:** `get_run`, `list_runs`, AND `get_child_runs` (`sqlite.py:439-461`, `postgres.py:488-515`) must include the metadata column in SELECT and pass to RunRecord
- CLI at `roots/cli/main.py:194` calls `Roots.start_run` — metadata parameter defaults to None so CLI is unaffected
- Metadata values restricted to JSON scalar types (str, int, float, bool, None) — validate on write, reject nested dicts/arrays with clear error

**Acceptance Criteria:**
- [x] RunRecord has `metadata: dict[str, Any] | None = None` field
- [x] StorageBackend.create_run accepts optional `metadata` keyword parameter
- [x] SQLite and PostgreSQL backends persist metadata as JSON/JSONB column
- [x] Migration adds nullable metadata column to existing runs tables (idempotent, catches specific exceptions)
- [x] `Roots.start_run(process_id, work_item, metadata=None)` passes metadata through to storage
- [x] `get_run()`, `list_runs()`, and `get_child_runs()` all return metadata in RunRecord
- [x] Metadata values validated on write: only JSON scalar values allowed (str, int, float, bool, None); nested dicts/arrays raise ValueError
- [x] Tests: metadata round-trips through create_run/get_run for both backends; None metadata stores and retrieves correctly; migration is idempotent; scalar validation rejects nested values
- [x] Full test suite passes
- [x] Typecheck/lint passes

### US-002: Metadata filter operator implementation

**Description:** As a framework consumer, I want to filter runs by metadata using equality, set membership, and existence operators so that I can query runs by application-specific criteria.

**Implementation Hints:**
- Operators for v1: `$eq` (exact match), `$in` (value in list), `$exists` (key presence — defined as "key is present in dict", even if value is null)
- Range operators ($gt/$gte/$lt/$lte/$ne) deferred — cross-backend type coercion for numeric values is unresolved (SQLite json_extract returns TEXT; CAST semantics diverge). Add when a concrete use case requires them.
- Define operator types as a dict structure: `{"key": {"$op": value}}` — shorthand `{"key": value}` means `$eq`
- **Metadata key validation:** Keys must match `^[a-zA-Z_][a-zA-Z0-9_]*$` (alphanumeric + underscore, no special chars). Validate on write AND in filter parsing. This prevents SQL injection via key names interpolated into `json_extract(metadata_json, '$.key')` or `metadata->>'key'` expressions.
- SQLite: use `json_type(metadata_json, '$.key') IS NOT NULL` for `$exists` (distinguishes missing key from JSON null value — `json_type` returns `'null'` for explicit null vs SQL NULL for missing key). For `$eq`, note that `json_extract` returns TEXT — numeric `$eq` must CAST both sides for correct comparison (e.g., `CAST(json_extract(...) AS NUMERIC) = CAST(? AS NUMERIC)` when filter value is int/float)
- PostgreSQL: `metadata->>'key'` for text extraction; `$exists` maps to `metadata ? 'key'` (key presence regardless of value)
- Add `metadata_filter: dict[str, Any] | None = None` to `StorageBackend.list_runs` abstract, `SqliteBackend.list_runs` (`sqlite.py:401-437`), and `PostgresBackend.list_runs` (`postgres.py:445-486`) — update all three in lockstep
- PostgreSQL clause builder uses incrementing `$N` parameters (`postgres.py:455-464`) — metadata filter clauses must continue the parameter index
- Cross-backend tests in `tests/test_storage.py` using the parameterized `storage` fixture (see `conftest.py:36-61`)

**Acceptance Criteria:**
- [x] Operators supported: `$eq`, `$in`, `$exists`
- [x] Shorthand: bare values treated as `$eq` (e.g., `{"epic_id": "abc"}` equals `{"epic_id": {"$eq": "abc"}}`)
- [x] `$exists` defined as key presence (true even when value is JSON null); SQLite uses `json_type()` for correct null handling
- [x] Metadata keys validated: `^[a-zA-Z_][a-zA-Z0-9_]*$` — invalid keys rejected on write and in filter parsing (prevents SQL injection)
- [x] Numeric `$eq` uses CAST on SQLite for correct cross-backend comparison (e.g., `{"count": 5}` matches identically on both backends)
- [x] StorageBackend.list_runs accepts optional `metadata_filter` parameter (abstract + both implementations updated in lockstep)
- [x] Tests: each operator on both backends; shorthand syntax; numeric `$eq` cross-backend
- [x] Full test suite passes
- [x] Typecheck/lint passes

### US-003: Metadata filter validation and edge cases

**Description:** As a framework consumer, I want metadata filtering to handle invalid inputs and edge cases gracefully so that I get clear errors and consistent behavior across backends.

**Implementation Hints:**
- Unknown operators raise `ValueError` with the operator name
- `$in` value must be a list — validate and raise `ValueError` if not
- Runs with NULL metadata should be excluded from positive filter matches (WHERE clause must handle NULL metadata column)
- `$eq` with null value should use `json_type()` on SQLite (same approach as `$exists`) for correct cross-backend behavior — `json_extract` returns SQL NULL for both missing keys and explicit null values
- Extract metadata key validation into a shared utility function used by both the write path (US-001) and the filter path (US-002) — single function, no drift
- Cross-backend consistency test in `test_storage.py` using the parameterized `storage` fixture (see `conftest.py:36-61`)

**Acceptance Criteria:**
- [x] Unknown operators raise `ValueError` with operator name
- [x] `$in` with non-list value raises `ValueError`
- [x] Runs with NULL metadata excluded from positive filter matches
- [x] `$eq` with null value uses `json_type()` (same as `$exists`) for correct cross-backend behavior
- [x] Shared key validation utility used by both write path and filter path (single function, no drift)
- [x] Cross-backend consistency test in `test_storage.py` using parameterized fixture
- [x] Tests: unknown operator error; `$in` validation; NULL exclusion; `$eq` null; cross-backend parity
- [x] Full test suite passes
- [x] Typecheck/lint passes

### US-004: REST API metadata integration

**Description:** As an API consumer, I want to create runs with metadata and filter runs by metadata via the REST API so that I can manage runs programmatically.

**Implementation Hints:**
- Add `metadata: dict[str, Any] | None = None` to `RunCreateRequest` (`roots/api/models.py:54-58`)
- Add `metadata: dict[str, Any] | None = None` to `RunResponse` (`models.py:61-71`)
- Update `_run_to_response` helper (`roots/api/routers/runs.py:21-33`) to include `metadata=run.metadata`
- Add `metadata_filter: str | None = None` query parameter to `list_runs` endpoint (`runs.py:65-75`) — JSON-encoded string
- Validate metadata_filter at API boundary: wrap `json.loads()` in try/except `json.JSONDecodeError`, return HTTP 422 with clear message (consistent with error pattern at `roots/api/routers/checkpoints.py:76-78`); validate parsed result is a dict with recognized operators before passing to storage
- Pass parsed filter to `roots.storage.list_runs(metadata_filter=...)`
- Pass metadata from `RunCreateRequest` through `roots.start_run(body.process_id, body.work_item, metadata=body.metadata)`
- Add custom Pydantic validator on `RunCreateRequest.metadata` to reject nested dicts/arrays at the API boundary (return 422, not 500 from storage layer)

**Acceptance Criteria:**
- [ ] `RunCreateRequest` accepts optional `metadata` dict
- [ ] `RunResponse` includes `metadata` field
- [ ] `GET /runs` accepts `metadata_filter` query parameter (JSON-encoded string)
- [ ] `GET /runs/{id}` returns metadata in response
- [ ] Invalid JSON in `metadata_filter` returns HTTP 422 with descriptive error
- [ ] Nested dict/array values in metadata rejected at API boundary with HTTP 422 (not propagated to storage)
- [ ] `POST /runs` passes metadata through to `Roots.start_run`
- [ ] Tests: POST with metadata; POST without metadata; GET list with filter; GET single run; invalid JSON returns 422
- [ ] Full test suite passes
- [ ] Typecheck/lint passes

## Out of Scope

- Metadata updates after run creation (immutable for v1)
- Full-text search on metadata values
- Metadata indexing or performance optimization (acceptable for current scale)
- Pagination on list_runs (separate concern)
- Range operators ($gt/$gte/$lt/$lte/$ne) — deferred until cross-backend numeric type coercion is resolved and a concrete use case exists
- Nested metadata values (dicts/arrays as values) — scalar only for v1
- Child run metadata inheritance (child runs get whatever metadata the caller passes, if any)

## Technical Considerations

- Metadata values restricted to JSON scalars (str, int, float, bool, None) to avoid cross-backend type divergence. Validated on write — both at the API boundary and in the storage layer (defense in depth).
- Metadata keys restricted to alphanumeric + underscore pattern — prevents SQL injection in `json_extract`/JSONB operator expressions where keys are interpolated into queries.
- `$exists` is defined as "key is present in the metadata dict" — returns true even when value is JSON null. SQLite implementation must check JSON text rather than json_extract IS NOT NULL (since json_extract returns SQL NULL for both missing keys and explicit null values).
- Both backends must produce identical results for the same metadata_filter input — verified via cross-backend test in `test_storage.py` using the parameterized storage fixture
- The `$in` operator value must be a list; validate at storage layer and raise clear error if not
- This is the first JSON-encoded query parameter in the API — establish clean validation pattern at the API boundary

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md)

## Implementation Notes

## Refinement Notes

### Research Conducted
- RunRecord dataclass at `base.py:24-34` has 10 fields, 3 optional — metadata fits naturally as 11th
- `create_run` abstract method at `base.py:167-175` uses keyword-only args after positional
- SQLite `list_runs` at `sqlite.py:401-437` uses dynamic clause builder — metadata filter appends to same pattern
- PostgreSQL `list_runs` at `postgres.py:445-486` uses `$N` parameterized queries with same clause builder
- REST API `RunCreateRequest` at `models.py:54-58` and `RunResponse` at `models.py:61-71` are straightforward Pydantic models
- `get_child_runs` at `sqlite.py:439-461` and `postgres.py:488-515` also constructs RunRecord from explicit column SELECTs — must be updated
- Two `create_run` call sites in orchestrator: user-facing (line 1362) and subprocess (line 1238)
- CLI at `roots/cli/main.py:194` calls `Roots.start_run` — unaffected with None default

### Scope Adjustments
- Range operators ($gt/$gte/$lt/$lte/$ne) deferred from v1 — SQLite json_extract returns TEXT for all values, making numeric comparison parity with PostgreSQL JSONB unreliable without a type coercion strategy. Stated use cases (tagging, grouping) are all equality/existence checks.
- Metadata values restricted to JSON scalars — avoids nested key access complexity and cross-backend divergence

### Decisions Made
- Metadata is immutable after creation (simplifies implementation, can add updates later)
- MongoDB-style operator syntax ($eq, $in, $exists) — widely known, JSON-friendly
- Shorthand bare values treated as $eq for ergonomics
- $exists defined as key presence (PostgreSQL semantics) — consistent with developer expectations
- API validation at boundary (HTTP 422) rather than letting errors propagate to storage layer
