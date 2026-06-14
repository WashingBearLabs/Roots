<!-- Template Version: 2.2.0 -->
---
feature: subprocess-schema
status: active
session_ready: true
depends_on: []
vision_ref: "T3.3 — Process Composition"
type: epic-child
size: M
epic: process-composition
epic_seq: 1
epic_final: false
created: 2026-05-13
updated: 2026-05-13
---

# Feature Spec: Subprocess Node Schema & Storage

## Overview

This spec adds the foundational types and storage support for process composition: a new SUBPROCESS node type with its config model, parent/child run tracking in storage, subprocess-specific event types, and validation to prevent circular process references. No execution logic — that lives in the companion spec (feature-subprocess-execution.md).

## Goals

- Define the SUBPROCESS node type and SubProcessNodeConfig with input/output mappings
- Track parent/child run relationships in storage for observability
- Validate subprocess references to prevent circular process references and enforce depth limits
- Add subprocess-specific event types and escalation trigger

## User Stories

### US-001: Add SUBPROCESS node type and config

**Description:** As a process author, I want to define subprocess nodes in my YAML process definitions so that I can compose processes from reusable building blocks.

**Implementation Hints:**
- NodeType enum at `roots/core/schema.py:15-23` — add SUBPROCESS = "subprocess"
- CONFIG_MAP at `roots/core/schema.py:159-168` — add SubProcessNodeConfig mapping
- Follow existing config patterns: AgentNodeConfig (schema.py:80-83) for simple config, DecisionNodeConfig (schema.py:100-122) for config with validation
- NodeDefinition model_validator at schema.py:178-198 — ensure SUBPROCESS nodes are excluded from the retry-config guard (retry is not supported on subprocess nodes; add SUBPROCESS to the excluded types list)
- output_key stores the mapped output dict; output_mapping selects which child state keys to include. The result stored at state[output_key] is `{parent_key: child_state[child_key] for child_key, parent_key in output_mapping.items()}`

**Acceptance Criteria:**
- [x] SUBPROCESS added to NodeType enum
- [x] SubProcessNodeConfig Pydantic model with: process_id (str), input_mapping (dict[str, str], maps parent state keys → child input keys), output_mapping (dict[str, str], maps child state keys → parent output keys), output_key (str), max_depth (int, default 5, Field with ge=1, le=20)
- [x] SubProcessNodeConfig added to CONFIG_MAP; NodeDefinition validator accepts it
- [x] NodeDefinition validator rejects retry config on SUBPROCESS nodes (same as checkpoint/fork/join)
- [x] YAML process definitions with subprocess nodes parse and validate correctly
- [x] Tests written/updated for new functionality
- [x] Full test suite passes
- [x] Typecheck/lint passes

### US-002: Add subprocess event types and escalation trigger

**Description:** As a framework developer, I want subprocess-specific event types and escalation triggers so that the execution spec has the types it needs.

**Implementation Hints:**
- EscalationTrigger enum at `roots/core/escalation.py:13-16` — add SUBPROCESS_PAUSED
- EventType enum at `roots/events/types.py` — add SUBPROCESS_STARTED, SUBPROCESS_COMPLETED, SUBPROCESS_FAILED
- These types have no consumers until the execution spec ships — this is intentional; types are defined first
- A stub handler (`_handle_subprocess` that raises `OrchestrationError("Subprocess execution not yet implemented")`) should be added to the dispatch table at `roots/core/orchestrator.py:369-388` so that subprocess nodes produce a clear error instead of "No handler for node type 'subprocess'"

**Acceptance Criteria:**
- [x] EscalationTrigger.SUBPROCESS_PAUSED added to escalation trigger enum
- [x] EventType entries added: SUBPROCESS_STARTED, SUBPROCESS_COMPLETED, SUBPROCESS_FAILED
- [x] Stub handler added to orchestrator dispatch table that raises OrchestrationError with clear "not yet implemented" message
- [x] Tests written/updated for new functionality
- [x] Full test suite passes
- [x] Typecheck/lint passes

### US-003: Add parent/child run relationship to storage

**Description:** As a platform operator, I want to see which runs spawned child runs so that I can trace execution through composed processes.

**Implementation Hints:**
- RunRecord dataclass at `roots/storage/base.py:23-31` — add parent_run_id and parent_node_id fields
- runs table schema in `roots/storage/sqlite.py:47-57` and `roots/storage/postgres.py` — add columns
- This is Roots' first ALTER TABLE migration. Pattern for SQLite initialize(): try `ALTER TABLE runs ADD COLUMN parent_run_id TEXT` wrapped in try/except (OperationalError if column exists). Same for parent_node_id. PostgreSQL: `ALTER TABLE runs ADD COLUMN IF NOT EXISTS parent_run_id TEXT`
- StorageBackend abstract class at `roots/storage/base.py` — add get_child_runs abstract method and update create_run signature
- create_run abstract signature change: add optional keyword-only params `parent_run_id: str | None = None, parent_node_id: str | None = None`. This changes the abstract interface — both backends and all callers (orchestrator.py start_run) must be updated
- Add index on parent_run_id in both backends for efficient child run queries

**Acceptance Criteria:**
- [x] parent_run_id (str | None) and parent_node_id (str | None) added to RunRecord dataclass
- [x] parent_run_id and parent_node_id TEXT columns added to runs table in both backends (nullable); index on parent_run_id
- [x] initialize() adds columns via ALTER TABLE for existing databases (SQLite: try/except OperationalError; PostgreSQL: ADD COLUMN IF NOT EXISTS)
- [x] create_run abstract signature updated with optional keyword-only parent_run_id and parent_node_id; both backends and existing callers updated
- [x] get_child_runs(parent_run_id) abstract method returns list of child RunRecords; implemented in both backends
- [x] Test: initialize() on a database without parent_run_id column successfully adds it (migration test)
- [x] Tests written/updated for new functionality
- [x] Full test suite passes
- [x] Typecheck/lint passes

### US-004: Validate subprocess references

**Description:** As a framework consumer, I want subprocess references validated so that circular process references and invalid configs are caught before execution.

**Implementation Hints:**
- Validator at `roots/core/validator.py:105-196` — validate_structure() performs graph validation
- Self-reference check is static (compare node's process_id against current ProcessDefinition.id) — add to validate_structure()
- Circular reference detection requires loading referenced processes from storage — implement as async function `validate_subprocess_references(process, storage)` in validator.py
- Wire into Orchestrator.start_run() at `roots/core/orchestrator.py:1075-1094` — call validate_subprocess_references after loading the process, before creating the run. This makes it mandatory, not opt-in.
- Test circular refs at depth 2 (A→B→A) and depth 3 (A→B→C→A) to verify transitive detection

**Acceptance Criteria:**
- [x] Static validation in validate_structure(): subprocess node with process_id matching current process.id produces validation error
- [x] Async function validate_subprocess_references(process, storage) detects circular references transitively (tests: A→B→A and A→B→C→A)
- [x] Circular reference validation returns clear error messages naming the cycle path
- [x] Validation handles missing referenced processes gracefully (error message, not crash)
- [x] validate_subprocess_references called from Orchestrator.start_run() before creating the run (mandatory, not opt-in)
- [x] Tests written/updated for new functionality
- [x] Full test suite passes
- [x] Typecheck/lint passes

## Out of Scope

- Subprocess execution logic (companion spec)
- Dynamic process_id resolution from state
- Subprocess node retry configuration
- Version pinning on subprocess references

## Technical Considerations

- The SUBPROCESS node type is the 9th node type. CONFIG_MAP in schema.py needs an entry. The dispatch table gets a stub handler in this spec; the real handler ships in the companion execution spec.
- **output_key vs output_mapping:** output_mapping selects which child state keys to extract. output_key is where the mapped result dict is stored in parent state. The result at `state[output_key]` is `{parent_key: child_state[child_key] for child_key, parent_key in output_mapping.items()}`.
- **input_mapping:** `dict[str, str]` where keys are parent state keys and values are child input keys. Example: `{"customer_data": "input"}` maps `parent_state["customer_data"]` → `child_state["input"]`.
- **Depth tracking:** The current nesting depth is tracked in the child run's work_item_state at a reserved key `_subprocess_depth`. The execution spec's handler increments this when creating child runs. The innermost subprocess node's max_depth applies (each node controls its own children's depth limit).
- Circular reference detection requires storage access (loading referenced processes). It's wired into Orchestrator.start_run() as a mandatory async validation step — not opt-in.
- **This is Roots' first ALTER TABLE migration.** No prior pattern exists. SQLite uses try/except on OperationalError (column-exists); PostgreSQL uses ADD COLUMN IF NOT EXISTS. Both are idempotent.
- **create_run signature change:** Adding optional keyword-only params (parent_run_id, parent_node_id) changes the abstract StorageBackend interface. Both backends and all callers (Orchestrator.start_run) must be updated.
- Retry config on SUBPROCESS nodes is rejected by the NodeDefinition validator — retry semantics for subprocess execution are deferred.

## Design Considerations

N/A — no UI components.

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md)

## Implementation Notes

<!-- Populated during implementation -->

## Refinement Notes

### Research Conducted
- NodeType dispatch at orchestrator.py:369-388 — dict-based handler lookup, trivial to extend
- CONFIG_MAP at schema.py:159-168 — maps NodeType → config model, automatic validation
- RunRecord at base.py:23-31 — simple dataclass, nullable fields straightforward
- validate_structure at validator.py:105-196 — BFS reachability, no cycle detection currently
- EscalationTrigger at escalation.py:13-16 — 3 values, adding 4th is trivial

### Scope Adjustments
- Split US-001 into US-001 (node type + config) and US-002 (event types + escalation trigger + stub handler) after validation review flagged 9-criterion span across 3 modules
- Async circular reference validation separated from sync schema validation

### Decisions Made
- input_mapping/output_mapping as dict[str, str] — simple key-to-key mapping, no expressions
- max_depth on SubProcessNodeConfig (not a global setting) — per-node control; innermost node's limit applies
- Depth tracked via `_subprocess_depth` key in child run's work_item_state — survives serialization/crash
- Circular reference detection is async (needs storage) — wired into Orchestrator.start_run() as mandatory step
- parent_node_id tracked alongside parent_run_id — enables knowing which subprocess node spawned a child
- Stub handler in dispatch table raises OrchestrationError — clear error during interim before execution spec ships
- Retry config rejected on SUBPROCESS nodes — retry semantics deferred
- CREATE TABLE migration: first ALTER TABLE in Roots; inline SQL pattern, not referencing T3.2
- create_run signature change fully documented — both backends and all callers must update

## Open Questions

None — all design questions resolved during planning and validation review.
