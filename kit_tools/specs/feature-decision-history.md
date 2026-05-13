<!-- Template Version: 2.2.0 -->
---
feature: decision-history
status: active
session_ready: true
depends_on: []
vision_ref: "T3.1 — Decision History Retrieval"
type: epic-child
size: M
epic: library-refinements
epic_seq: 2
epic_final: false
created: 2026-05-13
updated: 2026-05-13
---

# Feature Spec: Decision History Retrieval

## Overview

Roots stores every decision made by AI decision nodes (selected edge, confidence, reasoning, input state) but currently only supports a basic query: all decisions for a given process+node pair. There is no way to filter by run, limit results, or — most importantly — feed historical decisions back into AI prompts for pattern-based learning. This feature adds flexible querying and optional auto-injection of decision history into AI decision prompts, enabling processes that learn from their own execution patterns.

## Goals

- Extend decision history queries with run-scoped filtering, limit, and mode filters
- Auto-inject recent decisions into AI decision prompts via a per-node `history_depth` config
- Expose decision history via a REST API endpoint for external consumers

## User Stories

### US-001: Extend decision history query capabilities

**Description:** As a framework consumer, I want to query decision history with filters so that I can retrieve relevant subsets of past decisions for analysis or context.

**Implementation Hints:**
- StorageBackend.list_decisions at `roots/storage/base.py:232-247` — update abstract signature first, then both implementations
- SQLite implementation at `roots/storage/sqlite.py:566-589` — simple SELECT, no ORDER BY currently
- PostgreSQL implementation mirrors SQLite — both need the same changes
- Add optional keyword-only params with defaults that preserve existing behavior
- Add ORDER BY created_at DESC — most recent decisions are most useful for history injection
- Check if an index exists on (process_id, node_id, created_at) in both backends; add one if missing for ORDER BY + LIMIT performance

**Acceptance Criteria:**
- [x] StorageBackend.list_decisions abstract signature updated with optional keyword-only params: run_id (str | None), limit (int | None), mode (str | None)
- [x] list_decisions filters by run_id when provided (scopes to single run)
- [x] list_decisions applies LIMIT when provided (default None = no limit)
- [x] list_decisions filters by mode when provided (e.g., only ai_bounded decisions)
- [x] Results ordered by created_at DESC (most recent first)
- [x] Both SQLite and PostgreSQL backends implement the extended interface
- [x] Tests written/updated for new functionality
- [x] Full test suite passes
- [x] Typecheck/lint passes

### US-002: Add history_depth config and orchestrator fetch

**Description:** As a process author, I want to configure `history_depth` on a decision node so that the orchestrator fetches recent decisions before evaluating.

**Implementation Hints:**
- DecisionNodeConfig at `roots/core/schema.py:100-122` — add history_depth: int | None = None
- Orchestrator calls decision engine at `roots/core/orchestrator.py:660-673` — fetch history here before calling evaluate
- The history parameter type passed to DecisionEngine should be `list[dict[str, Any]]` (plain dicts, not DecisionRecord) to keep the engine independent of storage types
- Map DecisionRecord → dict with keys: selected_edge, confidence, reasoning, mode — done in the orchestrator, not the engine

**Acceptance Criteria:**
- [x] history_depth: int | None = None added to DecisionNodeConfig (Field with ge=1 when not None)
- [x] Orchestrator fetches last N decisions via list_decisions(process_id, node_id, limit=history_depth) when history_depth is set
- [x] Orchestrator maps DecisionRecord to plain dicts with keys: selected_edge (from decision["selected_edge"]), confidence, reasoning (from decision.get("reasoning") or decision.get("ai_recommendation", {}).get("reasoning")), mode
- [x] Deterministic decisions (which have no reasoning field) are included with reasoning=None
- [x] History passed to DecisionEngine.evaluate as history: list[dict[str, Any]] | None = None
- [x] No fetch when history_depth is None (backward compatible, no storage call)
- [x] Tests written/updated for new functionality
- [x] Full test suite passes
- [x] Typecheck/lint passes

### US-003: Render decision history in AI prompts

**Description:** As a framework developer, I want historical decisions formatted into AI decision prompts so that the AI can learn from past patterns at this node.

**Implementation Hints:**
- build_decision_messages at `roots/core/decision.py:192-219` — add history parameter
- Place "## Historical Decisions" section after ## Current State (keeps primary reasoning target adjacent to instruction)
- Each entry: "- Edge: {selected_edge}, Confidence: {confidence}" + optional ", Reasoning: {reasoning[:200]}" when reasoning is not None
- DecisionEngine.evaluate at `roots/core/decision.py:295-323` — thread history through to build_decision_messages for AI modes only
- Deterministic mode ignores history (expressions don't use it)

**Acceptance Criteria:**
- [x] build_decision_messages accepts optional history: list[dict[str, Any]] | None parameter
- [x] When history is provided and non-empty, a "## Historical Decisions" section is added after ## Current State
- [x] Each entry shows: selected edge target, confidence score, and reasoning (truncated to 200 chars when present, omitted when None)
- [x] History section omitted when history is None or empty list (backward compatible)
- [x] Deterministic mode evaluate path does not pass history to prompt builder (no effect)
- [x] Tests written/updated for new functionality
- [x] Full test suite passes
- [x] Typecheck/lint passes

### US-004: Decision history API endpoint

**Description:** As a platform operator, I want to query decision history via the REST API so that I can build dashboards and audit trails for process decisions.

**Implementation Hints:**
- Create `roots/api/routers/decisions.py` — new router file following pattern from `roots/api/routers/runs.py`
- Create DecisionHistoryResponse Pydantic model in the router (mirrors DecisionRecord fields but as Pydantic for FastAPI serialization)
- Register router in app factory at `roots/api/app.py` — follow existing router registration pattern
- DecisionRecord dataclass at `roots/storage/base.py:73-83` — map to response model in the route handler

**Acceptance Criteria:**
- [ ] GET /processes/{process_id}/decisions endpoint in new decisions router
- [ ] Accepts optional query params: node_id, run_id, limit, mode
- [ ] DecisionHistoryResponse Pydantic model with all fields (id, run_id, process_id, node_id, mode, decision, confidence, created_at)
- [ ] Router registered in app factory
- [ ] Returns empty list (not 404) when no decisions match filters
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes
- [ ] Typecheck/lint passes

## Out of Scope

- Full-text search on decision reasoning
- Analytics/aggregation queries (average confidence by node, decision distribution)
- Decision diffing or comparison tools
- Pagination (limit is sufficient for v1; cursor-based pagination can be added later)
- Injecting cross-node decision history (history_depth scopes to the current node only)

## Technical Considerations

- The decision history schema already exists from v1 (`decision_history` table with rich metadata). This feature adds querying and consumption, not new storage.
- DecisionEngine is currently independent of StorageBackend. To preserve this, the orchestrator fetches history, maps DecisionRecord → plain dicts, and passes those to the engine. The engine never imports or references storage types.
- The history parameter type is `list[dict[str, Any]]` — not `list[DecisionRecord]`. This keeps the core/storage boundary clean.
- History injection is intentionally cross-run (all runs of a process at a given node). This is the point — pattern learning across executions.
- The mode filter on list_decisions is for API consumers. The injection path does not filter by mode — mixed-mode history is intentional (a node's mode may change over time, and past decisions are still relevant context).
- History injection adds tokens to AI prompts. With history_depth=10 and verbose reasoning, this could be significant. Truncating reasoning to 200 chars and limiting to essential fields keeps prompt size manageable.
- `DecisionRecord.decision` is an opaque dict whose keys vary by mode. AI modes have `decision["ai_recommendation"]["reasoning"]`; deterministic mode has no reasoning. The orchestrator's mapping logic handles both cases.
- The existing list_decisions signature changes (new optional keyword-only params) — all existing callers pass positional args for process_id and node_id, so this is backward compatible.

## Design Considerations

N/A — no UI components.

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md)

## Implementation Notes

<!-- Populated during implementation -->

## Refinement Notes

### Research Conducted
- DecisionRecord stores rich data: mode, input_state snapshot, full decision dict with AI recommendation, confidence, reasoning
- list_decisions currently queries by (process_id, node_id) with no ordering — adding ORDER BY and filters is straightforward
- build_decision_messages constructs a 2-message prompt (system + user) with ## Context, ## Current State, ## Available Edges sections
- Orchestrator already has access to storage and process_id when calling decision engine (orchestrator.py:660-673)

### Scope Adjustments
- Originally considered per-run history injection — scoped to per-node only (more useful for pattern learning)
- Excluded pagination — limit param is sufficient for the auto-inject use case
- Split US-002 into US-002 (config + orchestrator fetch) and US-003 (prompt rendering) after validation review flagged 3-layer span
- Original US-003 (API) became US-004

### Decisions Made
- Orchestrator fetches history, passes to DecisionEngine as `list[dict[str, Any]]` — preserves engine's independence from storage types
- History section placed after ## Current State in prompt (not between Context and Current State) — keeps primary reasoning target adjacent to the instruction
- Reasoning truncated to 200 chars in prompt injection — prevents prompt bloat while preserving signal
- Deterministic decisions included in history with reasoning=None — they're still useful pattern evidence
- Mixed-mode history is intentional — no mode filter on the injection path
- DecisionRecord → dict mapping handles both AI mode (reasoning in ai_recommendation) and deterministic mode (no reasoning)

## Open Questions

None — all design questions resolved during planning and validation review.
