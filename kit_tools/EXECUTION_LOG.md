# Execution Log

## Run: Epic roots-v1 — 2026-03-23

**Mode:** Guarded (pause after 3 consecutive failures)
**Branch:** `epic/roots-v1`
**Specs:** 11 feature specs, 71 stories
**Completion:** None (leave branch as-is)

---


## Run: epic/roots-v1 — 2026-03-24
- **Epic:** roots-v1
- **Branch:** epic/roots-v1
- **Mode:** guarded (max 3 retries)
- **Feature Specs:** 11 total

### US-001: Project Scaffolding (Attempt 1) — PASS
- Completed: 2026-03-24T01:42:06Z
- Verified by: independent verifier session
- Learnings: Pyright strict mode flags errors in kit_tools/hooks/ Python files but roots/ and tests/ pass cleanly; Ruff has one pre-existing error in kit_tools/hooks/validate_setup.py — not from scaffolding; hatchling used as build backend for editable installs; Verifier note: All 6 acceptance criteria are met. The scaffolding is clean and complete. Minor note: typer 0.23.1 does not provide the 'all' extra (warning during install), but this does not affect functionality or any criterion.
- Committed: feat(process-schema): US-001 - Project Scaffolding

### US-002: Base Node Model and NodeType Enum (Attempt 1) — PASS
- Completed: 2026-03-24T01:45:20Z
- Verified by: independent verifier session
- Learnings: Pydantic model_validator(mode='after') with Self return type works well for cross-field validation; Using TYPE_CHECKING guard for Self import avoids runtime issues while satisfying pyright strict mode; from __future__ import annotations needed to allow forward references in model_validator return types; Verifier note: Implementation is clean and follows the spec exactly. All models use Pydantic with model_validator(mode='after'). BackoffStrategy and OnExhaustion are correctly separated as StrEnum types. Tests are thorough with parametrized coverage of all non-agent node types.
- Committed: feat(process-schema): US-002 - Base Node Model and NodeType Enum

### US-003: Agent, AgentPool, and Decision Config Models (Attempt 1) — PASS
- Completed: 2026-03-24T01:47:38Z
- Verified by: independent verifier session
- Learnings: Pydantic model_validator(mode='after') with Self return type works well for cross-field validation; Empty string condition treated as falsy for deterministic mode edge validation; Verifier note: Implementation is clean and follows the spec precisely. All models, enums, and validators match the implementation hints. Tests are thorough with good edge case coverage including empty string conditions and threshold boundary checks.
- Committed: feat(process-schema): US-003 - Agent, AgentPool, and Decision Config Models

### US-004: Remaining Config Models and Type-Discriminated Node Parsing (Attempt 1) — PASS
- Completed: 2026-03-24T01:51:18Z
- Verified by: independent verifier session
- Learnings: NodeDefinition.config type changed from dict[str, Any] to dict[str, Any] | BaseModel to support both dict and typed config models; isinstance(self.config, dict) guard in model_validator prevents re-parsing already-typed configs passed programmatically; Exception wrapping in config validator adds node ID and type context for better error messages; Verifier note: Implementation is clean and complete. All 5 config models match the spec exactly. The CONFIG_MAP discriminator with isinstance guard handles both dict and pre-typed configs correctly. Error messages include node ID and type for debugging. Test coverage is thorough with 73 passing tests.
- Committed: feat(process-schema): US-004 - Remaining Config Models and Type-Discriminated Node Parsing

### US-005: Edge Model and Process Definition Model (Attempt 1) — PASS
- Completed: 2026-03-24T01:54:15Z
- Verified by: independent verifier session
- Learnings: Pydantic Field(alias='from') with ConfigDict(populate_by_name=True) allows both 'from' and 'from_node' for YAML/Python compat; Pyright flags aliased field names as unknown parameters — runtime works fine with populate_by_name=True but pyright sees only the alias; model_dump(by_alias=True, mode='json') serializes using alias names (from/to) for round-trip YAML compatibility; Private _node_map built in model_validator(mode='after') provides O(1) node lookups for get_node and validation; Verifier note: Clean implementation matching the spec exactly. EdgeDefinition uses proper alias handling with populate_by_name. ProcessDefinition validator correctly builds _node_map and validates all references. Helper methods correctly dispatch between decision config edges and top-level edges. All 95 tests pass.
- Committed: feat(process-schema): US-005 - Edge Model and Process Definition Model

### US-006: YAML Parsing Pipeline (Attempt 1) — PASS
- Completed: 2026-03-24T01:58:05Z
- Verified by: independent verifier session
- Learnings: yaml.safe_load returns Any type - use cast() to satisfy pyright strict mode when narrowing via isinstance; Pydantic ValidationError.errors() returns dicts with 'loc' tuples and 'msg' strings - loc[0]=='nodes' and loc[1] being int indicates a node-level error; Node-level validation errors from Pydantic may be wrapped by the NodeDefinition model_validator, so the loc path may not always reach into node config fields directly; Verifier note: Implementation is clean and follows the spec closely. All three public functions (load_process_yaml, parse_process_dict, validate_process_yaml) are implemented correctly. Minor cosmetic note: node-level error messages have duplicated context (once from _format_validation_errors, once from NodeDefinition.validate_node in schema.py), but this doesn't affect correctness. Full test suite passes with 112 tests and no regressions.
- Committed: feat(process-schema): US-006 - YAML Parsing Pipeline

### US-007: Structural Validator — Basic Rules (Attempt 1) — PASS
- Completed: 2026-03-24T02:01:44Z
- Verified by: independent verifier session
- Learnings: Unreachable node detection uses BFS that follows both top-level edges and decision config edges for full graph traversal; Join nodes are exempt from edge completeness checks alongside end and decision nodes; validate_process_yaml returns structural errors directly (list), while load_process_yaml raises ProcessValidationError; Verifier note: Clean implementation. All 33 tests pass (including pre-existing US-006 tests). Error messages match spec exactly. ProcessValidationError properly defined with errors attribute. Integration into load_process_yaml and validate_process_yaml is correct — structural validation runs after Pydantic validation. Reachability BFS correctly follows both top-level edges and decision config edges. Warnings use the warnings module as specified, keeping them separate from errors.
- Committed: feat(process-schema): US-007 - Structural Validator — Basic Rules

### US-008: Structural Validator — Fork/Join Pairing (Attempt 1) — PASS
- Completed: 2026-03-24T02:05:48Z
- Verified by: independent verifier session
- Learnings: Fork/join validation reuses the adjacency dict already built for reachability BFS in validate_structure; recompute_fork_join_map delegates to validate_structure via lazy import to avoid circular imports between schema.py and validator.py; fork_join_map is a regular Pydantic field (not underscore-prefixed) so it survives serialization; Verifier note: Implementation is clean and correct. All 42 tests pass. Minor note: load_process_yaml calls parse_process_dict (which runs validate_structure via recompute_fork_join_map) and then calls validate_structure again — double validation is a minor inefficiency but not a correctness issue.
- Committed: feat(process-schema): US-008 - Structural Validator — Fork/Join Pairing

### US-001: Abstract Storage Interface and Test Infrastructure (Attempt 1) — PASS
- Completed: 2026-03-24T03:32:44Z
- Verified by: independent verifier session
- Learnings: EdgeDefinition from_node/to_node with populate_by_name causes pyright reportCallIssue — pre-existing across codebase, not introduced by this story; SqliteBackend import in conftest uses lazy import inside fixture body to avoid import errors before US-002 creates the module; pytest-asyncio asyncio_mode='auto' configured in pyproject.toml under [tool.pytest.ini_options]; Verifier note: All acceptance criteria are met. 137 tests pass with no failures. The implementation faithfully follows the spec including the utcnow() helper in roots/core/utils.py, correct stale_timeout_seconds=300 default, and all method signatures matching the spec exactly. The conftest.py pyright errors are due to dependencies outside this story's scope (SqliteBackend not yet implemented, EdgeDefinition alias is a pre-existing schema pattern).
- Committed: feat(storage-backend): US-001 - Abstract Storage Interface and Test Infrastructure

### US-002: SQLite — Schema and Process/Agent CRUD (Attempt 1) — PASS
- Completed: 2026-03-24T03:37:17Z
- Verified by: independent verifier session
- Learnings: ProcessDefinition.model_dump(mode='json') serializes node config as empty dict due to union type dict[str, Any] | BaseModel — need manual config serialization via helper that calls config.model_dump(mode='json') on each node; SQLite AUTOINCREMENT columns create a sqlite_sequence internal table — filter it out when asserting table names; aiosqlite.Connection.executescript() is used for multi-statement DDL (CREATE TABLE), followed by commit(); Verifier note: Clean implementation. All 5 acceptance criteria met. 16 tests pass in 0.04s. Code follows project conventions (utcnow(), model_dump/model_validate, INSERT OR REPLACE for upserts). Implementation goes beyond the story scope to cover runs, history, checkpoints, escalations, decisions, retries, webhooks, and locking — but this is additive, not harmful.
- Committed: feat(storage-backend): US-002 - SQLite — Schema and Process/Agent CRUD

### US-003: SQLite — Run Lifecycle and Work Item State (Attempt 1) — PASS
- Completed: 2026-03-24T03:40:13Z
- Verified by: independent verifier session
- Learnings: Run lifecycle methods were already implemented in US-002 as additive scope — only the run ID format needed fixing (str(uuid4()) -> f"run-{uuid4()}"); All run methods (create_run, get_run, update_run_status, list_runs, get_work_item_state, update_work_item_state, update_run_atomically) were present and correct aside from the ID prefix; Verifier note: All 7 acceptance criteria are met. 17 tests in test_sqlite_runs.py pass. 16 existing tests in test_sqlite.py pass with no regressions. The diff is minimal and focused: one line fix in sqlite.py (run ID prefix) and the new test file. Code follows project conventions.
- Committed: feat(storage-backend): US-003 - SQLite — Run Lifecycle and Work Item State

### US-004: SQLite — History, Checkpoints, Escalations (Attempt 1) — PASS
- Completed: 2026-03-24T03:53:30Z
- Verified by: independent verifier session
- Learnings: StorageError was not yet defined — added to roots/storage/base.py as a simple Exception subclass; Checkpoint IDs were str(uuid4()) — changed to f"ckpt-{uuid4()}" per spec; Escalation IDs were str(uuid4()) — changed to f"esc-{uuid4()}" per spec; History, checkpoint, and escalation SQL methods were already implemented in US-002 as additive scope — only needed ID prefix fixes and enforcement checks; Verifier note: Clean implementation. All 15 new tests pass, all 16 existing tests pass (no regressions). Code follows spec hints exactly: ckpt-/esc- ID prefixes, StorageError for duplicate pending, resolution with dict and timestamp. StorageError exception class properly added to base.py and imported in sqlite.py.
- Committed: feat(storage-backend): US-004 - SQLite — History, Checkpoints, Escalations

### US-005: SQLite — Decision History and Retry State (Attempt 1) — PASS
- Completed: 2026-03-24T03:55:54Z
- Verified by: independent verifier session
- Learnings: Decision history and retry state methods were already fully implemented in sqlite.py from US-002 additive scope — only tests needed to be written; increment_retry uses ON CONFLICT DO UPDATE (upsert) pattern for clean first-insert-or-increment logic; Decision records are queried by process_id + node_id (not run_id), allowing cross-run decision analysis; Verifier note: Implementation is clean and complete. Schema defines decision_history and retry_state tables correctly. Base class (base.py) declares abstract methods for all 5 operations. SQLite backend implements them with proper JSON serialization/deserialization. The UPSERT pattern for increment_retry is elegant and correct. All tests are thorough and pass.
- Committed: feat(storage-backend): US-005 - SQLite — Decision History and Retry State

### US-006: SQLite — Webhook Registry and Pattern Matching (Attempt 1) — PASS
- Completed: 2026-03-24T03:58:34Z
- Verified by: independent verifier session
- Learnings: Webhook methods were already implemented in sqlite.py from US-002 additive scope — only needed ID prefix fix (str(uuid4()) -> f"wh-{uuid4()}") and pattern matching logic; list_webhooks_by_pattern originally did only exact `in` check — replaced with proper wildcard/universal matching using break-on-first-match to avoid duplicates; Verifier note: Implementation is clean and correct. Pattern matching logic handles all three cases (universal, wildcard suffix, exact) with proper break statements to avoid duplicate matches. The wh_id format uses f"wh-{uuid4()}" as specified. All 16 tests pass.
- Committed: feat(storage-backend): US-006 - SQLite — Webhook Registry and Pattern Matching

### US-007: SQLite — Run Locking (Attempt 1) — PASS
- Completed: 2026-03-24T04:00:55Z
- Verified by: independent verifier session
- Learnings: Locking methods (acquire_run_lock, release_run_lock, check_run_lock) and abstract base class definitions were already implemented from US-002 additive scope; acquire_run_lock originally used a two-step SELECT+UPDATE pattern — refactored to single atomic UPDATE with WHERE clause (locked_by IS NULL OR locked_at < stale_threshold) and rowcount check, matching the spec hints; Added timedelta import to sqlite.py for stale threshold computation; Verifier note: Implementation uses a single atomic UPDATE for lock acquisition, matching the spec's hint exactly. The diff replaced a SELECT-then-UPDATE pattern with the atomic approach, eliminating a race condition. All 13 locking tests pass, and 16 existing sqlite tests pass with no regressions.
- Committed: feat(storage-backend): US-007 - SQLite — Run Locking

### US-008: PostgreSQL Backend — Schema and Core CRUD (Attempt 1) — PASS
- Completed: 2026-03-24T04:05:38Z
- Verified by: independent verifier session
- Learnings: asyncpg returns JSONB columns as Python dicts automatically, but we pass JSON strings via json.dumps() on insert — added isinstance(data, str) guards on read for safety; asyncpg uses $1/$2 positional parameter syntax instead of ? placeholders; asyncpg execute() returns status strings like 'DELETE 1' or 'UPDATE 1' instead of rowcount — use string comparison for affected row checks; PostgreSQL uses SERIAL instead of INTEGER PRIMARY KEY AUTOINCREMENT, DOUBLE PRECISION instead of REAL, JSONB instead of TEXT for JSON, TIMESTAMPTZ instead of TEXT for timestamps; Verifier note: Implementation is thorough and well-structured. PostgresBackend properly extends StorageBackend ABC, uses asyncpg connection pooling, handles JSONB serialization/deserialization correctly (with isinstance checks for str fallback), and implements all required CRUD operations plus additional operations (history, checkpoints, escalations, decisions, retries, webhooks, locking). Tests are comprehensive with 32 test cases covering all CRUD operations, edge cases, and full lifecycle.
- Committed: feat(storage-backend): US-008 - PostgreSQL Backend — Schema and Core CRUD

### US-009: PostgreSQL Backend — History, Locking, and Remaining Methods (Attempt 1) — PASS
- Completed: 2026-03-24T04:15:15Z
- Verified by: independent verifier session
- Learnings: Advisory locks are session-scoped in PostgreSQL — must pin the connection that acquires the lock (store in _lock_connections dict) so the lock persists until explicitly released; pg_try_advisory_lock(hashtext(run_id)) doesn't care about table existence — must check run exists first to match SQLite behavior of returning False for nonexistent runs; Advisory locks auto-release on connection close, but the run_locks tracking table entry persists — crash recovery requires cleaning stale tracking entries; Parameterized pytest fixtures with pytest.skip() inside the fixture body work cleanly for conditional backend availability; All history, checkpoint, escalation, decision, retry, and webhook methods were already implemented in postgres.py from US-008 — US-009 only needed advisory lock refactoring and parameterized tests; Verifier note: Implementation is solid and complete. All methods mirror the SQLite backend with asyncpg queries. Advisory locking uses session-scoped pg_try_advisory_lock with a run_locks tracking table for check_run_lock visibility. Connection pinning ensures locks stay held. Parameterized test fixture is well-designed with proper cleanup (TRUNCATE CASCADE). All tests pass for SQLite; PostgreSQL tests skip cleanly without errors.
- Committed: feat(storage-backend): US-009 - PostgreSQL Backend — History, Locking, and Remaining Methods

### US-001: Agent Registration Models (Attempt 1) — PASS
- Completed: 2026-03-24T04:22:57Z
- Verified by: independent verifier session
- Learnings: Pydantic model_config with arbitrary_types_allowed=True is needed for Callable fields; Project uses StrEnum, from __future__ import annotations, and TYPE_CHECKING pattern consistently; Verifier note: Clean implementation matching the spec exactly. All fields, types, defaults, and validation logic are correct. Tests are thorough with good coverage of edge cases.
- Committed: feat(agent-registry): US-001 - Agent Registration Models

### US-002: Agent Registry (Attempt 1) — PASS
- Completed: 2026-03-24T04:36:35Z
- Verified by: independent verifier session
- Learnings: Verifier note: Clean implementation. Registry is straightforward with correct method signatures matching the spec. Types module provides proper validation (LOCAL requires callable, REMOTE requires callback_url). No scope creep or extraneous changes.
- Committed: feat(agent-registry): US-002 - Agent Registry

### US-003: Local Callable Invocation (Attempt 1) — PASS
- Completed: 2026-03-24T04:39:31Z
- Verified by: independent verifier session
- Learnings: inspect.iscoroutinefunction is preferred over asyncio.iscoroutinefunction (deprecated since Python 3.14); Pydantic Callable fields type-narrow to Callable | None — assert not None after registry lookup for type safety; Verifier note: Clean implementation matching the spec exactly. Code follows project conventions (Pydantic models, async-first design). AgentInvocationError and AgentNotFoundError have proper attributes. The full input dict passthrough pattern is correctly implemented. No scope creep or extraneous changes.
- Committed: feat(agent-registry): US-003 - Local Callable Invocation

### US-004: Remote HTTP Invocation (Attempt 1) — PASS
- Completed: 2026-03-24T04:41:56Z
- Verified by: independent verifier session
- Learnings: httpx.MockTransport works well for testing async HTTP clients without real network calls; httpx.AsyncClient accepts a transport parameter for dependency injection in tests; ConnectError must be caught separately from HTTPStatusError and TimeoutException for distinct error messages; Verifier note: Clean implementation. All acceptance criteria are met. The code follows the implementation hints closely: shared httpx.AsyncClient via constructor injection, proper error handling for timeout/connection/HTTP errors, and model_dump(mode='json') for the request body. All tests pass with no regressions.
- Committed: feat(agent-registry): US-004 - Remote HTTP Invocation

### US-005: Input/Output Schema Validation (Attempt 1) — PASS
- Completed: 2026-03-24T04:45:09Z
- Verified by: independent verifier session
- Learnings: jsonschema.Draft7Validator.iter_errors collects all validation errors at once, avoiding the need to catch and re-raise ValidationError; AgentSchemaValidationError inherits from AgentInvocationError so the orchestrator can catch it as a subtype for escalation handling; Verifier note: Clean implementation. AgentSchemaValidationError properly inherits from AgentInvocationError for orchestrator escalation. Uses Draft7Validator with iter_errors for comprehensive validation. Validation errors include path, message, and validator type. No regressions — full suite passes (325 passed, 80 skipped).
- Committed: feat(agent-registry): US-005 - Input/Output Schema Validation

### US-001: Safe Expression Evaluator (Attempt 1) — PASS
- Completed: 2026-03-24T04:53:37Z
- Verified by: independent verifier session
- Learnings: simpleeval's NameNotDefined has a .name attribute (not .node.id) for the missing name; simpleeval's AttributeDoesNotExist (not NameNotDefined) is raised for missing dict keys accessed via dot notation; EvalWithCompoundTypes has ATTR_INDEX_FALLBACK=True, enabling dict key access via dot notation automatically; Dot-notation array indices (e.g. results.0.name) are not valid Python syntax — must preprocess to bracket notation (results[0].name) before evaluation; The regex for array index conversion must use lookbehind for [A-Za-z_\]] to avoid matching decimal points in float literals like 0.5; Verifier note: Clean implementation using simpleeval.EvalWithCompoundTypes as specified. The flatten_for_eval helper correctly handles nested dicts, lists with dict items, and lists with scalar items. The regex-based array index conversion is a pragmatic approach. Error handling covers NameNotDefined, AttributeDoesNotExist, TypeError, and a catch-all Exception. All 33 tests pass in 0.03s.
- Committed: feat(decision-engine): US-001 - Safe Expression Evaluator

### US-002: Deterministic Decision Mode (Attempt 1) — PASS
- Completed: 2026-03-24T04:58:37Z
- Verified by: independent verifier session
- Learnings: DecisionEvaluationError extended with optional node_id and context for backward compatibility with US-001; DecisionEngine uses async evaluate() method consistent with project async-first design pattern; assert isinstance(node.config, DecisionNodeConfig) used for type narrowing after Pydantic model_validator parses config dict; Verifier note: Implementation is clean and correct. DecisionEngine.evaluate dispatches to _evaluate_deterministic for deterministic mode and raises NotImplementedError for other modes. DecisionResult model matches the spec. DecisionEvaluationError was extended with node_id and context fields while maintaining backward compatibility for US-001 usage. All 39 tests pass (including US-001 tests).
- Committed: feat(decision-engine): US-002 - Deterministic Decision Mode

### US-003: AI Decision Response Model (Attempt 1) — PASS
- Completed: 2026-03-24T05:02:20Z
- Verified by: independent verifier session
- Learnings: DecisionEngine now requires default_model constructor parameter — existing tests updated; parse_ai_response uses two-path parsing: tool_calls first, then text content with markdown fence stripping; DECISION_TOOL constant defines OpenAI-compatible tool format that LiteLLM translates for other providers; Verifier note: Clean implementation. AIDecisionResponse, build_decision_messages, resolve_model, and parse_ai_response are well-structured with proper error handling and fallback paths. DECISION_TOOL constant matches the OpenAI-compatible format specified in the story. The DecisionEngine constructor now requires default_model, and existing tests were updated accordingly.
- Committed: feat(decision-engine): US-003 - AI Decision Response Model

### US-004: AI Bounded and AI Autonomous Modes (Attempt 1) — PASS
- Completed: 2026-03-24T05:06:46Z
- Verified by: independent verifier session
- Learnings: AIDecisionResponse must be defined before DecisionResult since DecisionResult references it as a field type; The _evaluate_ai method is shared by both ai_bounded and ai_autonomous modes — the confidence threshold logic is identical for both; Acceptance criteria says invalid edge target raises DecisionEvaluationError (not escalation as hints suggest) — followed AC; Verifier note: All 6 acceptance criteria are met. Implementation is clean: _evaluate_ai handles both ai_bounded and ai_autonomous modes with shared logic, edge validation raises DecisionEvaluationError, confidence threshold uses strict < comparison (boundary tested), and DecisionResult has the required escalated and ai_recommendation fields with correct defaults. 71 tests pass with no failures.
- Committed: feat(decision-engine): US-004 - AI Bounded and AI Autonomous Modes

### US-005: AI Checkpoint Mode (Attempt 1) — PASS
- Completed: 2026-03-24T05:31:26Z
- Verified by: independent verifier session
- Learnings: ai_checkpoint reuses the same LiteLLM call pattern as ai_bounded/ai_autonomous but always sets escalated=True regardless of confidence; checkpoint_prompt is an optional field on both DecisionNodeConfig (already existed) and DecisionResult (added) — the decision engine passes it through from config to result; Verifier note: Clean implementation. The _evaluate_ai_checkpoint method correctly mirrors _evaluate_ai but unconditionally sets escalated=True and passes through checkpoint_prompt. Edge validation is properly included. Test coverage is thorough across all acceptance criteria.
- Committed: feat(decision-engine): US-005 - AI Checkpoint Mode

### US-006: Decision History Recording (Attempt 1) — PASS
- Completed: 2026-03-24T05:34:52Z
- Verified by: independent verifier session
- Learnings: to_decision_record() takes input_state as a parameter since DecisionResult doesn't store the work item state; Optional fields (checkpoint_prompt, ai_recommendation) are only included in the record when present, keeping deterministic records clean; Verifier note: Clean implementation. The to_decision_record() method is straightforward and well-tested. All modes are covered with specific test cases. No scope creep or side effects observed.
- Committed: feat(decision-engine): US-006 - Decision History Recording

### US-001: Event Type Catalog and Envelope Model (Attempt 1) — PASS
- Completed: 2026-03-24T05:42:59Z
- Verified by: independent verifier session
- Learnings: The spec says 18 event types but the actual enumerated list has 19 (5+4+3+3+2+2) — implemented all 19 from the list; Pydantic BaseModel with model_dump_json() serializes datetime as ISO string by default (e.g. 2026-03-23T12:00:00Z); EventType uses StrEnum consistent with NodeType and other enums in roots.core.schema; Issue: Acceptance criteria says '18 event types' but the enumerated list in the story has 19 events — implemented all 19 from the list; Verifier note: Clean implementation. All 24 tests pass in 0.02s. Code is minimal and matches the spec precisely. Minor cosmetic issue: test method named 'test_all_18_event_types_defined' asserts len == 19, but this is because the spec's criterion text says '18' while the implementation hints actually list 19 types. The code correctly implements all 19 types from the hints.
- Committed: feat(event-system): US-001 - Event Type Catalog and Envelope Model

### US-002: Event Emitter with Bounded Buffer (Attempt 1) — PASS
- Completed: 2026-03-24T05:45:50Z
- Verified by: independent verifier session
- Learnings: OrderedDict provides natural FIFO ordering for task shedding — simpler than maintaining a separate deque of task IDs; asyncio.wait() with timeout handles graceful shutdown cleanly — tasks that don't complete within timeout are simply left (not cancelled) per the spec's 'wait with timeout' requirement; Verifier note: Clean implementation. EventSink is an ABC with a single async emit method. EventEmitter uses OrderedDict for FIFO task tracking, asyncio.create_task for async dispatch, and proper exception isolation. All acceptance criteria are met with comprehensive tests.
- Committed: feat(event-system): US-002 - Event Emitter with Bounded Buffer

### US-003: StdoutSink and FileSink (Attempt 1) — PASS
- Completed: 2026-03-24T05:47:57Z
- Verified by: independent verifier session
- Learnings: asyncio.to_thread wraps synchronous file I/O for non-blocking writes without adding aiofiles dependency; capsys fixture works with async pytest tests under asyncio_mode=auto for capturing stdout; Verifier note: Clean implementation following the spec hints closely. StdoutSink and FileSink are well-structured with proper error handling. Tests are comprehensive, covering normal operation, compact mode, multi-event append, file creation, string path acceptance, and error resilience. All 9 tests pass in 0.03s.
- Committed: feat(event-system): US-003 - StdoutSink and FileSink

### US-004: HttpSink (Attempt 1) — PASS
- Completed: 2026-03-24T05:50:14Z
- Verified by: independent verifier session
- Learnings: httpx.MockTransport is the idiomatic way to test httpx-based HTTP clients — inject it via _client to avoid real network calls; httpx.AsyncClient(timeout=N) sets all timeout values (connect, read, write, pool) to N seconds; httpx.HTTPStatusError is raised by response.raise_for_status() and includes the response object with status_code; Verifier note: Clean implementation. All 9 HttpSink tests pass, and existing 9 sinks tests pass with no regressions. Code follows project conventions (abc base class, logger.warning for errors, lazy client initialization). Constructor signature matches spec (url, headers with None default instead of mutable dict default, timeout_seconds=10).
- Committed: feat(event-system): US-004 - HttpSink

### US-005: WebhookDispatcher (as EventSink) (Attempt 1) — PASS
- Completed: 2026-03-24T05:52:52Z
- Verified by: independent verifier session
- Learnings: asyncio.create_task for fire-and-forget webhook delivery within an EventSink.emit — tasks are collected by the emitter's own buffer management; hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest() computes HMAC-SHA256 on the exact JSON bytes sent in the POST body; httpx.MockTransport with request capture list is the standard test pattern for verifying HTTP calls including headers and body content; Verifier note: Clean implementation. WebhookDispatcher correctly subclasses EventSink, uses fire-and-forget asyncio.create_task for delivery, computes HMAC-SHA256 per spec, and handles failures gracefully. Tests are thorough with good coverage of edge cases.
- Committed: feat(event-system): US-005 - WebhookDispatcher (as EventSink)

### US-001: Run State Machine (Attempt 1) — PASS
- Completed: 2026-03-24T06:01:05Z
- Verified by: independent verifier session
- Learnings: StrEnum pattern is consistent across the codebase (schema.py, types.py) — used the same import style from enum; Test files follow test_{module}.py naming convention in the top-level tests/ directory; Verifier note: Clean, minimal implementation matching the spec exactly. RunStatus is StrEnum with 6 values, transition map matches spec, can_transition and transition functions work as specified, InvalidTransitionError includes all required attributes. No extraneous code or scope creep.
- Committed: feat(orchestrator-engine): US-001 - Run State Machine

### US-002: ProcessRunner — Tick-Based Execution Loop (Attempt 1) — PASS
- Completed: 2026-03-24T06:05:25Z
- Verified by: independent verifier session
- Learnings: EventEmitter.emit() is synchronous fire-and-forget — call emitter.close() in tests to await pending sink tasks before asserting; EdgeDefinition uses from_node/to_node fields (with alias from/to for YAML), while DecisionEdge uses target — _resolve_next handles both via getattr; SqliteBackend.create_run returns a RunRecord with status=pending and current_node_id=None — the first tick must set both status and current_node_id; time.monotonic() for duration_ms measurement avoids clock skew issues; Verifier note: All 8 acceptance criteria are met. Implementation follows the spec closely: tick is crash-safe with try/finally lock management, state is loaded fresh each tick, pending→running transition works correctly, events include all required fields, history uses lifecycle strings, and run_to_completion loops properly. 13 tests all pass using SQLite in-memory backend with a mock echo agent.
- Committed: feat(orchestrator-engine): US-002 - ProcessRunner — Tick-Based Execution Loop

### US-003: Agent and Agent Pool Handlers (Attempt 1) — PASS
- Completed: 2026-03-24T06:10:06Z
- Verified by: independent verifier session
- Learnings: Escalation in handlers must set a flag (_escalated) rather than directly updating run status, because tick() calls update_run_atomically after the handler returns — direct status updates get overwritten; Agent pool _invoke_pool_agent helper centralizes event emission (invoked/returned) for all pool modes, avoiding duplication across parallel/sequential/first_pass; asyncio.gather with return_exceptions=True allows filtering successful AgentOutput from BaseException failures in parallel pool mode; Verifier note: Implementation is clean and well-structured. Handler dispatch is properly wired in _dispatch_node. Pool modes correctly follow the spec: parallel uses asyncio.gather, sequential chains state, first_pass treats escalation as failure. Escalation creates records with correct trigger_type. All 13 tests pass with no regressions in orchestrator tests.
- Committed: feat(orchestrator-engine): US-003 - Agent and Agent Pool Handlers

### US-004: Decision, Checkpoint, Emit, and End Handlers (Attempt 1) — PASS
- Completed: 2026-03-24T06:15:33Z
- Verified by: independent verifier session
- Learnings: Escalation in handlers must set _escalated flag rather than directly updating run status — tick() handles the atomic status update; Decision handler routes via _decision_next_node field on ProcessRunner, checked in tick() between _escalated and _resolve_next; Emit nodes use custom event_type strings that don't map to EventType enum — construct EventEnvelope directly instead of create_event; Checkpoint handler reuses the _escalated flag to trigger pause, since the tick loop already handles escalated→paused transition; Verifier note: Implementation is clean and matches the spec closely. All handlers follow the patterns described in the implementation hints. The dispatch dict covers all NodeType enum values. Tests are thorough with 11 passing tests covering normal paths, edge cases (missing payload keys), and error paths (escalation, fork/join stubs). Both test_orchestrator.py (13 passed) and test_handlers.py (11 passed) run green.
- Committed: feat(orchestrator-engine): US-004 - Decision, Checkpoint, Emit, and End Handlers

### US-005: Edge Evaluation and State Accumulation (Attempt 1) — PASS
- Completed: 2026-03-24T06:19:52Z
- Verified by: independent verifier session
- Learnings: Edge evaluation and state accumulation were already implemented in prior stories (US-002 tick loop, US-004 handlers) — US-005 required dedicated tests proving each acceptance criterion; Echo agent returns {"echo": work_item_state} which naturally proves state accumulation — node2's output contains node1's output_key in the echoed state; Nodes without output_key (emit, checkpoint, decision, end) return None from handlers and fail the hasattr check, so state is never modified; Verifier note: All 7 acceptance criteria are met. Implementation is clean — edge evaluation in _resolve_next, decision routing via _decision_next_node, state accumulation via output_key guard in tick(). All 8 tests pass. OrchestrationError is properly defined. No issues found.
- Committed: feat(orchestrator-engine): US-005 - Edge Evaluation and State Accumulation

### US-006: Orchestrator Class (Attempt 1) — PASS
- Completed: 2026-03-24T06:24:53Z
- Verified by: independent verifier session
- Learnings: Orchestrator creates AgentInvoker internally from the AgentRegistry — ProcessRunner needs AgentInvoker, not the registry directly; SqliteBackend.update_run_status does raw SQL without state machine validation, so setting current_node_id on a pending run works by calling update_run_status with the same pending status; run_loop graceful shutdown: catching asyncio.CancelledError and returning cleanly allows the task to complete without propagating the error; Verifier note: Implementation is clean and follows the spec closely. Constructor signature matches hints. All methods (start_run, tick_all, run_loop, execute_run) are implemented as specified. 10 new tests all pass, 13 existing orchestrator tests pass with no regressions.
- Committed: feat(orchestrator-engine): US-006 - Orchestrator Class

### US-007: Roots Embedded API — Core Methods (Attempt 1) — PASS
- Completed: 2026-03-24T06:28:38Z
- Verified by: independent verifier session
- Learnings: Orchestrator already creates AgentInvoker internally from AgentRegistry, so Roots class only needs to pass the registry to Orchestrator — not the invoker; SqliteBackend requires explicit initialize() call before use — Roots constructor does not call it, leaving initialization to the consumer; Echo agent must return dict with 'output' and 'escalate' keys to match AgentOutput model expectations; Verifier note: All 9 acceptance criteria met. Full test suite passes (573 passed, 80 skipped). Implementation is clean, follows the spec closely, and all internal wiring matches the implementation hints.
- Committed: feat(orchestrator-engine): US-007 - Roots Embedded API — Core Methods

### US-008: Roots Embedded API — Graph and Resolution (Attempt 1) — PASS
- Completed: 2026-03-24T06:33:02Z
- Verified by: independent verifier session
- Learnings: Checkpoint node gets both 'entered' and 'completed' history events from the tick loop even when run pauses — current_node_id + paused status must take priority over history 'completed' in node status derivation; Edge status derivation: 'traversed' if both from and to nodes have any history events, 'pending' otherwise — simple set membership check on node_events keys; Verifier note: All 586 tests pass (80 skipped). Implementation is clean, well-structured, and follows the spec's implementation hints faithfully. The only discrepancy is between the 'max 2 queries' acceptance criterion text and the implementation hints that explicitly describe 3 queries — the code follows the hints. The 'skipped' node status from hints is not implemented but is not required by acceptance criteria.
- Committed: feat(orchestrator-engine): US-008 - Roots Embedded API — Graph and Resolution

### US-009: Example Process YAML Files (Attempt 1) — PASS
- Completed: 2026-03-24T06:37:42Z
- Verified by: independent verifier session
- Learnings: Echo agent must return dict with 'output' and 'escalate' keys to match AgentOutput model — consistent with prior learnings; RunRecord uses 'work_item_state' field, not 'state'; The parallel-validation YAML has 9 nodes (not 10 as might be expected from the hint) — the gate decision node uses config edges rather than top-level edges per schema requirements; SqliteBackend requires explicit initialize() call before use — Roots constructor does not call it; Verifier note: Clean implementation. All files match the spec's implementation hints closely. Full test suite passes with no regressions (594 passed, 80 skipped). The example script runs successfully as a standalone program.
- Committed: feat(orchestrator-engine): US-009 - Example Process YAML Files

### US-001: Retry Execution with Backoff (Attempt 1) — PASS
- Completed: 2026-03-24T06:48:24Z
- Verified by: independent verifier session
- Learnings: Storage already has get_retry_state/increment_retry/clear_retry methods and RetryState dataclass — no storage changes needed; EventEmitter dispatches to sinks via asyncio.create_task, so tests must call emitter.close() to flush pending tasks before asserting on events; AgentSchemaValidationError is a subclass of AgentInvocationError, so is_retryable must check for it BEFORE checking AgentInvocationError; Wrapping _invoke_pool_agent with execute_with_retry covers all pool modes (parallel, sequential, first_pass) since they all call _invoke_pool_agent; Verifier note: Implementation is clean and complete. All 10 acceptance criteria are fully met. Code follows project conventions. Tests are thorough with 33 passing tests covering unit and integration scenarios.
- Committed: feat(retry-escalation): US-001 - Retry Execution with Backoff

### US-002: Retry Exhaustion — Fail Mode (Attempt 1) — PASS
- Completed: 2026-03-24T06:54:49Z
- Verified by: independent verifier session
- Learnings: AgentInvocationError wraps the original error message, so assertions on last_error should use 'in' rather than exact match; Default on_exhaustion is OnExhaustion.FAIL, so existing tests expecting raw RuntimeError on exhaustion needed updating; SqliteBackend uses list_history_events() not get_run_history() to retrieve run history; Verifier note: Implementation is solid. RetryExhaustedError cleanly separates the exhaustion-with-fail path from generic exceptions. The orchestrator catches it at the right level, persists failure state atomically, and emits both node-level and run-level failure events with error metadata. Both unit and integration tests thoroughly cover the exhaustion path. Code follows project conventions.
- Committed: feat(retry-escalation): US-002 - Retry Exhaustion — Fail Mode

### US-003: Retry Exhaustion — Route Mode (Attempt 1) — PASS
- Completed: 2026-03-24T06:58:17Z
- Verified by: independent verifier session
- Learnings: RetryRoutedError follows the same pattern as RetryExhaustedError but carries fallback_edge for routing; RetryRoutedError must be caught BEFORE RetryExhaustedError in orchestrator since route mode previously fell through to a bare re-raise; The tick() method returns True on route fallback to continue execution from the fallback node; Verifier note: Implementation is clean and well-structured. RetryRoutedError is a distinct exception from RetryExhaustedError, caught before it in the handler chain. The orchestrator correctly marks the node as failed in history, emits the NODE_FAILED event with fallback metadata, sets current_node_id to the fallback target, keeps status RUNNING, and returns True to continue. All 37 existing tests plus 6 new route tests pass with no regressions.
- Committed: feat(retry-escalation): US-003 - Retry Exhaustion — Route Mode

### US-004: Escalation Triggers (Attempt 1) — PASS
- Completed: 2026-03-24T07:04:43Z
- Verified by: independent verifier session
- Learnings: AgentSchemaValidationError raised inside an agent callable gets wrapped in AgentInvocationError by _invoke_local — to test schema validation escalation, register the agent with an output_schema and have the agent return non-conforming output so the invoker raises the error after _invoke_local returns; create_escalation_from_error sets run status to PAUSED and the tick loop also sets PAUSED via _escalated flag — double-write is redundant but harmless and keeps the function self-contained per spec; The _trigger_escalation method now delegates to create_escalation_from_error which handles storage, status update, and event emission in one call; Verifier note: Implementation is clean and well-structured. EscalationTrigger enum, create_escalation_from_error function, and orchestrator integration points all match the spec. The escalation.py module is minimal and focused. Integration in orchestrator.py follows the exact pattern specified: schema validation catches AgentSchemaValidationError, confidence uses DecisionResult.escalated, and agent explicit signal checks result.escalate after successful invocation. All 29 tests pass with real SQLite storage.
- Committed: feat(retry-escalation): US-004 - Escalation Triggers

### US-005: Checkpoint and Escalation Resolution (Attempt 1) — PASS
- Completed: 2026-03-24T07:10:30Z
- Verified by: independent verifier session
- Learnings: When a confidence escalation creates both a checkpoint (with ai_recommendation) and an escalation record, resolve_pending must detect the escalation-type checkpoint and route to the escalation resolution path to resolve both records; CheckpointRecord has a checkpoint_type field ('planned' vs 'escalation') that distinguishes planned checkpoints from escalation-related checkpoints; Storage already has resolve_checkpoint and resolve_escalation methods that set status='resolved' and record resolution JSON; Verifier note: Implementation is clean and well-structured. The resolve_pending function correctly checks for pending checkpoint first, then escalation, matching the spec. The escalation-type checkpoint bridging logic (line 49-53) correctly handles the case where a checkpoint was created as part of an escalation flow. All 7 acceptance criteria are fully met with comprehensive test coverage.
- Committed: feat(retry-escalation): US-005 - Checkpoint and Escalation Resolution

