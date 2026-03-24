<!-- Template Version: 2.0.0 -->
---
feature: storage-backend
status: active
session_ready: true
depends_on: [process-schema]
vision_ref: "T1.2 — Storage Backend"
type: epic-child
epic: roots-v1
epic_seq: 2
epic_final: false
created: 2026-03-23
updated: 2026-03-23
---

# Feature Spec: Storage Backend

## Overview

The storage backend provides the persistence layer for all Roots state. It defines an abstract async interface and ships two implementations: SQLite (for development/testing/embedded) and PostgreSQL (for production/standalone). The SQLite implementation is built first and serves as the reference for all tests.

## Goals

- Define a complete abstract storage interface covering all entity types
- Implement a fully functional SQLite backend that enables fast in-memory testing
- Implement a PostgreSQL backend with advisory lock-based run locking
- Establish shared test infrastructure (conftest.py) that all subsequent specs use

## User Stories

### US-001: Abstract Storage Interface and Test Infrastructure

**Description:** As a framework developer, I want a complete abstract storage interface and shared test fixtures so that all backends implement the same contract and all tests have a common foundation.

**Implementation Hints:**
- Create `roots/storage/base.py` with `StorageBackend(abc.ABC)`
- All methods are `async` and abstract. Group by entity:
  - **Process:** `save_process(process: ProcessDefinition)`, `get_process(id: str) -> ProcessDefinition | None`, `list_processes() -> list[ProcessDefinition]`, `delete_process(id: str) -> bool`
  - **Agent:** `save_agent(registration: dict)`, `get_agent(name: str) -> dict | None`, `list_agents() -> list[dict]`, `delete_agent(name: str) -> bool`
  - **Run:** `create_run(process_id: str, work_item_state: dict) -> RunRecord`, `get_run(run_id: str) -> RunRecord | None`, `update_run_status(run_id: str, status: str, current_node_id: str | None = None)`, `list_runs(process_id: str | None = None, status: str | None = None) -> list[RunRecord]`
  - **Work Item:** `get_work_item_state(run_id: str) -> dict`, `update_work_item_state(run_id: str, state: dict)`
  - **History:** `append_history_event(run_id: str, event_type: str, node_id: str | None, data: dict)`, `list_history_events(run_id: str) -> list[HistoryEvent]`
  - **Checkpoint:** `create_checkpoint(run_id, node_id, checkpoint_type, prompt, ai_recommendation=None) -> str`, `get_pending_checkpoint(run_id) -> CheckpointRecord | None`, `resolve_checkpoint(checkpoint_id, resolution: dict)`
  - **Escalation:** `create_escalation(run_id, node_id, trigger_type, reason, work_item_snapshot) -> str`, `get_pending_escalation(run_id) -> EscalationRecord | None`, `resolve_escalation(escalation_id, resolution: dict)`
  - **Decision:** `append_decision(run_id, process_id, node_id, mode, input_state, decision, confidence)`, `list_decisions(process_id, node_id) -> list[DecisionRecord]`
  - **Retry:** `get_retry_state(run_id, node_id) -> RetryState | None`, `increment_retry(run_id, node_id, error: str)`, `clear_retry(run_id, node_id)`
  - **Webhook:** `create_webhook(url, events, secret=None) -> WebhookRecord`, `list_webhooks() -> list[WebhookRecord]`, `list_webhooks_by_pattern(event_type: str) -> list[WebhookRecord]`, `delete_webhook(webhook_id: str) -> bool`
  - **Atomic Run Update:** `update_run_atomically(run_id, work_item_state: dict, status: str, current_node_id: str | None)` — updates state, status, and current node in a SINGLE write/transaction. This is critical for crash safety: if the orchestrator crashes between two separate writes, state and position can become inconsistent. The SQLite implementation wraps in a transaction; PostgreSQL uses a single UPDATE statement.
  - **Locking:** `acquire_run_lock(run_id, owner_id, stale_timeout_seconds=300) -> bool`, `release_run_lock(run_id, owner_id)`, `check_run_lock(run_id) -> tuple[str | None, datetime | None]`
  - Note: default stale lock timeout is 300s (5 min), not 60s. Fork/join parallel execution can take minutes; a 60s timeout would cause false stale-lock reclamation and double-execution.
  - **Lifecycle:** `initialize()`, `close()`
- Define dataclass/Pydantic models: `RunRecord` (id, process_id, status, current_node_id, work_item_state, created_at, updated_at), `CheckpointRecord` (id, run_id, node_id, checkpoint_type, prompt, ai_recommendation, status, resolution, created_at, resolved_at), `EscalationRecord` (id, run_id, node_id, trigger_type, reason, work_item_snapshot, status, resolution, created_at, resolved_at), `HistoryEvent` (id, run_id, event_type, node_id, data, created_at), `DecisionRecord` (id, run_id, process_id, node_id, mode, input_state, decision, confidence, created_at), `RetryState` (run_id, node_id, attempt_count, last_error), `WebhookRecord` (id, url, events, secret, created_at)
- Create `tests/conftest.py` with:
  - `@pytest.fixture` async fixture `sqlite_storage` that creates `SqliteBackend(":memory:")`, calls `initialize()`, yields it, calls `close()`
  - `@pytest.fixture` for a sample `ProcessDefinition` (simple 2-node linear process)
  - **Placeholder comment** for a `roots_instance` fixture: add `# TODO: roots_instance fixture — created in T1.3 US-007 after Roots class exists` in conftest. The actual fixture (Roots with sqlite_storage + echo agent) is added when T1.3 US-007 is implemented. Do NOT try to import Roots here — it doesn't exist yet.
  - Configure pytest-asyncio mode in `pyproject.toml`: `[tool.pytest.ini_options]` with `asyncio_mode = "auto"`
  - **Datetime convention:** Use `datetime.now(datetime.UTC)` everywhere, NEVER `datetime.utcnow()` (deprecated in Python 3.12+). Produces timezone-aware datetimes that work consistently across SQLite (stored as ISO string) and PostgreSQL (TIMESTAMPTZ). Add a helper `utcnow() -> datetime` in a shared `roots/core/utils.py` that wraps this.

**Acceptance Criteria:**
- [x] `StorageBackend` ABC with all methods defined and typed
- [x] All return type models defined with appropriate fields
- [x] `tests/conftest.py` with sqlite_storage fixture and sample process
- [x] pytest-asyncio configured in pyproject.toml
- [x] Type hints are complete and pyright-compatible

### US-002: SQLite — Schema and Process/Agent CRUD

**Description:** As a framework developer, I want the SQLite backend to create its schema and handle process and agent storage.

**Implementation Hints:**
- Create `roots/storage/sqlite.py` with `SqliteBackend(StorageBackend)`
- Constructor takes `path: str` (use `":memory:"` for tests)
- `initialize()` creates all tables using `aiosqlite`:
  - `processes` (id TEXT PK, name TEXT, version TEXT, description TEXT, definition_json TEXT, created_at TEXT, updated_at TEXT)
  - `agents` (name TEXT PK, type TEXT, config_json TEXT, created_at TEXT)
  - `runs` (id TEXT PK, process_id TEXT, status TEXT, current_node_id TEXT, work_item_state_json TEXT, created_at TEXT, updated_at TEXT, locked_by TEXT, locked_at TEXT)
  - `run_history` (id INTEGER PK AUTOINCREMENT, run_id TEXT, event_type TEXT, node_id TEXT, data_json TEXT, created_at TEXT)
  - `checkpoints` (id TEXT PK, run_id TEXT, node_id TEXT, checkpoint_type TEXT, prompt TEXT, ai_recommendation_json TEXT, status TEXT DEFAULT 'pending', resolution_json TEXT, created_at TEXT, resolved_at TEXT)
  - `escalations` (id TEXT PK, run_id TEXT, node_id TEXT, trigger_type TEXT, reason TEXT, work_item_snapshot_json TEXT, status TEXT DEFAULT 'pending', resolution_json TEXT, created_at TEXT, resolved_at TEXT)
  - `decision_history` (id INTEGER PK AUTOINCREMENT, run_id TEXT, process_id TEXT, node_id TEXT, mode TEXT, input_state_json TEXT, decision_json TEXT, confidence REAL, created_at TEXT)
  - `retry_state` (run_id TEXT, node_id TEXT, attempt_count INTEGER DEFAULT 0, last_error TEXT, PRIMARY KEY (run_id, node_id))
  - `webhooks` (id TEXT PK, url TEXT, events_json TEXT, secret TEXT, created_at TEXT)
- Implement process CRUD: serialize `ProcessDefinition` via `model.model_dump(mode="json")`, deserialize via `ProcessDefinition.model_validate(json.loads(row))`. Wrap in `json.dumps`/`json.loads` for the TEXT column.
- Implement agent CRUD: store as JSON dict
- Use `datetime.now(datetime.UTC).isoformat()` for all timestamps

**Acceptance Criteria:**
- [x] `SqliteBackend(":memory:")` creates all tables on `initialize()`
- [x] Process CRUD works: save, get by ID, list all, delete
- [x] Agent CRUD works: save, get by name, list all, delete
- [x] Process round-trip preserves all fields (serialize→store→load→compare)
- [x] Tests use the `sqlite_storage` fixture from conftest

### US-003: SQLite — Run Lifecycle and Work Item State

**Description:** As a framework developer, I want run lifecycle and work item state operations so that the orchestrator can create and manage process runs.

**Implementation Hints:**
- `create_run(process_id, initial_work_item_state) -> RunRecord`: generate ID as `f"run-{uuid4()}"`, status=`"pending"`, store initial state as JSON
- `get_run(run_id)`: return RunRecord or None
- `update_run_status(run_id, status, current_node_id=None)`: UPDATE status, current_node_id (if provided), updated_at
- `update_run_atomically(run_id, work_item_state, status, current_node_id)`: single UPDATE that sets all three fields + updated_at in one statement (or within a transaction for SQLite). This is the crash-safe write path used by the orchestrator's tick loop.
- `list_runs(process_id=None, status=None)`: build WHERE clause from optional filters
- `get_work_item_state(run_id)`: SELECT work_item_state_json, parse and return dict
- `update_work_item_state(run_id, state)`: UPDATE work_item_state_json with `json.dumps(state)`, set updated_at

**Acceptance Criteria:**
- [x] Creating a run returns RunRecord with pending status and generated ID
- [x] Run status updates are persisted and retrievable
- [x] Work item state can be read and updated independently
- [x] List runs filters correctly by process_id and status (both, either, neither)
- [x] Non-existent run_id returns None (not exception)
- [x] `update_run_atomically` updates state+status+node in a single operation
- [x] Tests cover full lifecycle: create → update to running → atomic update → complete

### US-004: SQLite — History, Checkpoints, Escalations

**Description:** As a framework developer, I want history, checkpoint, and escalation storage so that run execution is fully auditable and human-in-the-loop flows work.

**Implementation Hints:**
- `append_history_event(run_id, event_type, node_id, data)`: INSERT into run_history
- `list_history_events(run_id)`: SELECT ordered by created_at ASC
- `create_checkpoint(...)`: generate ID as `f"ckpt-{uuid4()}"`, INSERT with status='pending'. Return the ID.
- `get_pending_checkpoint(run_id)`: SELECT WHERE run_id=? AND status='pending' LIMIT 1. This returns the first (and should be only) pending checkpoint.
- `resolve_checkpoint(checkpoint_id, resolution)`: UPDATE status='resolved', resolution_json=dumps(resolution), resolved_at=now
- Escalation methods mirror checkpoint methods with `f"esc-{uuid4()}"` IDs
- **Enforcement:** `create_checkpoint` and `create_escalation` should check that no pending checkpoint/escalation already exists for this run. If one exists, raise `StorageError("Run {run_id} already has a pending checkpoint/escalation")`.

**Acceptance Criteria:**
- [x] History events appended and retrievable in chronological order
- [x] Checkpoints: create (pending) → get_pending → resolve lifecycle works
- [x] Escalations: create (pending) → get_pending → resolve lifecycle works
- [x] Creating a second pending checkpoint/escalation raises StorageError
- [x] Resolution stores decision dict and timestamp
- [x] Tests cover both checkpoint and escalation lifecycles

### US-005: SQLite — Decision History and Retry State

**Description:** As a framework developer, I want decision history and retry state storage so that decisions are recorded and retries survive crashes.

**Implementation Hints:**
- `append_decision(run_id, process_id, node_id, mode, input_state, decision, confidence)`: INSERT into decision_history with all fields serialized
- `list_decisions(process_id, node_id)`: SELECT WHERE process_id=? AND node_id=? ORDER BY created_at. Returns list of DecisionRecord.
- `get_retry_state(run_id, node_id)`: SELECT from retry_state WHERE run_id=? AND node_id=?. Return RetryState or None.
- `increment_retry(run_id, node_id, error)`: UPSERT — `INSERT OR REPLACE INTO retry_state (run_id, node_id, attempt_count, last_error) VALUES (?, ?, COALESCE((SELECT attempt_count + 1 FROM retry_state WHERE run_id=? AND node_id=?), 1), ?)`. Or simpler: check if exists, INSERT or UPDATE.
- `clear_retry(run_id, node_id)`: DELETE FROM retry_state WHERE run_id=? AND node_id=?

**Acceptance Criteria:**
- [x] Decision records appended and queryable by process_id + node_id
- [x] Retry state: first increment creates row with attempt_count=1
- [x] Retry state: subsequent increments increase attempt_count
- [x] Retry state: clear removes the row
- [x] get_retry_state returns None when no state exists
- [x] Tests cover decision append/query and full retry lifecycle

### US-006: SQLite — Webhook Registry and Pattern Matching

**Description:** As a framework developer, I want webhook storage with event pattern matching so that the webhook dispatcher can find matching subscriptions.

**Implementation Hints:**
- `create_webhook(url, events, secret=None)`: generate ID as `f"wh-{uuid4()}"`, store events as JSON array
- `list_webhooks()`: return all webhooks
- `list_webhooks_by_pattern(event_type)`: load all webhooks, filter in Python. Matching algorithm: a webhook's event pattern matches if:
  - Exact match: `"roots.run.completed"` matches `"roots.run.completed"`
  - Wildcard suffix: `"roots.run.*"` matches any event starting with `"roots.run."` — split on `*`, check if event_type starts with the prefix
  - Universal: `"*"` matches everything
- `delete_webhook(webhook_id)`: DELETE, return True if deleted, False if not found

**Acceptance Criteria:**
- [ ] Webhook CRUD works (create, list, delete)
- [ ] Exact pattern matching works
- [ ] Wildcard suffix matching works (`roots.run.*` matches `roots.run.completed`)
- [ ] Universal wildcard `*` matches all events
- [ ] Pattern matching is case-sensitive
- [ ] Tests cover all matching scenarios

### US-007: SQLite — Run Locking

**Description:** As a framework developer, I want run-level locking so that concurrent orchestrator instances don't double-execute the same run.

**Implementation Hints:**
- `acquire_run_lock(run_id, owner_id, stale_timeout_seconds=300) -> bool`:
  - Compute stale threshold: `(datetime.now(datetime.UTC) - timedelta(seconds=stale_timeout_seconds)).isoformat()`
  - SQL: `UPDATE runs SET locked_by=?, locked_at=? WHERE id=? AND (locked_by IS NULL OR locked_at < ?)`
  - Check `cursor.rowcount` — if 1, lock acquired (return True); if 0, lock held by another (return False)
- `release_run_lock(run_id, owner_id)`: `UPDATE runs SET locked_by=NULL, locked_at=NULL WHERE id=? AND locked_by=?` — only releases if caller owns it
- `check_run_lock(run_id)`: SELECT locked_by, locked_at FROM runs WHERE id=?

**Acceptance Criteria:**
- [ ] Lock acquired on unlocked run returns True
- [ ] Lock on already-locked run returns False
- [ ] Stale lock (past timeout) is reclaimed
- [ ] Release by owner clears lock
- [ ] Release by non-owner is no-op
- [ ] Tests cover: acquire, contention, staleness, release, non-owner release

### US-008: PostgreSQL Backend — Schema and Core CRUD

**Description:** As a framework developer, I want a PostgreSQL backend with schema creation and core CRUD operations.

**Implementation Hints:**
- Create `roots/storage/postgres.py` with `PostgresBackend(StorageBackend)`
- Constructor takes `dsn: str`
- Use `asyncpg` for the connection pool: `self.pool = await asyncpg.create_pool(dsn)` in `initialize()`
- `close()`: close the pool
- Schema: `CREATE TABLE IF NOT EXISTS` for all tables, using same structure as SQLite but with `JSONB` instead of `TEXT` for JSON columns, and `TIMESTAMPTZ` instead of `TEXT` for timestamps
- Implement: `save_process`, `get_process`, `list_processes`, `delete_process`, `save_agent`, `get_agent`, `list_agents`, `delete_agent`, `create_run`, `get_run`, `update_run_status`, `list_runs`, `get_work_item_state`, `update_work_item_state`
- Use `json.dumps()` for JSONB inserts, `json.loads()` for reads (asyncpg returns JSONB as dicts automatically — verify this)
- Mark tests with `@pytest.mark.skipif(not os.environ.get("ROOTS_POSTGRES_DSN"), reason="PostgreSQL not available")`

**Acceptance Criteria:**
- [ ] Schema creation with CREATE TABLE IF NOT EXISTS
- [ ] Process and agent CRUD matches SQLite behavior
- [ ] Run lifecycle operations match SQLite behavior
- [ ] Work item state operations match SQLite behavior
- [ ] JSONB used for all JSON columns
- [ ] Tests skipped when ROOTS_POSTGRES_DSN not set

### US-009: PostgreSQL Backend — History, Locking, and Remaining Methods

**Description:** As a framework developer, I want the remaining PostgreSQL methods including advisory lock-based run locking.

**Implementation Hints:**
- Implement all remaining methods mirroring SQLite, EXCEPT run locking:
  - History, checkpoint, escalation, decision history, retry state, webhook methods — same logic as SQLite with asyncpg queries
- **Run locking with advisory locks:**
  - `acquire_run_lock(run_id, owner_id, stale_timeout_seconds=300)`: use `SELECT pg_try_advisory_lock(hashtext($1))` where $1 is run_id. Returns True if lock acquired. Advisory locks are session-scoped — auto-released on disconnect (crash safety).
  - `release_run_lock(run_id, owner_id)`: `SELECT pg_advisory_unlock(hashtext($1))`
  - `check_run_lock`: Not directly supported with advisory locks — return (None, None) or implement a tracking table. Simplest: use a `run_locks` tracking table alongside advisory locks.
- Add parameterized test fixture: `@pytest.fixture(params=["sqlite", "postgres"])` that yields the appropriate backend. This way the same test suite runs against both backends.

**Acceptance Criteria:**
- [ ] All history, checkpoint, escalation, decision, retry, webhook methods work
- [ ] Advisory lock acquisition and release work
- [ ] Advisory locks auto-release on connection drop
- [ ] Parameterized tests run same suite against both backends
- [ ] PostgreSQL tests skip cleanly when no database available

## Out of Scope

- Data migration between backend types
- Connection pooling tuning
- Backup or replication
- Multi-tenant isolation

## Technical Considerations

- `aiosqlite` wraps synchronous SQLite in a thread — subtle behavioral differences from true async
- Use `datetime.now(datetime.UTC).isoformat()` consistently for all timestamps (NEVER `utcnow()` — deprecated in 3.12+)
- PostgreSQL JSONB handles serialization differently from SQLite TEXT + json.dumps — test both paths
- The parameterized test fixture is key — write tests once, run against both backends

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
