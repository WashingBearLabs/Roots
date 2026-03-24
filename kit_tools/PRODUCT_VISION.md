<!-- Template Version: 2.0.0 -->
<!-- Seeding:
  explorer_focus: none
  required_sections: []
  skip_if: always
  note: Created interactively by create-vision skill, not auto-seeded
-->
# PRODUCT_VISION.md

> Last updated: 2026-03-24
> Updated by: Claude

---

## Product Vision Statement

Roots is an AI-native process orchestration framework that provides the connective tissue between agents, humans, tools, and decisions. It defines processes as directed graphs — authored in YAML, executed by a stateless orchestrator, and observable through structured events. The core insight: defining the process *is* tuning the system. AI capabilities are only as reliable as the process structures they operate within. Roots makes process definition a first-class, living artifact that is version-controlled, diffable, visualizable, and executable — not a document that sits unread in a wiki.

---

## Target Users & Personas

| Persona | Role / Context | Primary Need | Pain Point |
|---------|---------------|--------------|------------|
| Framework Consumer (Embedded) | Developer building AI-powered applications who needs to orchestrate multi-agent workflows within their app | A library that manages process state, agent invocation, and decision routing without imposing an agent framework | Hand-coding orchestration logic that is brittle, hard to visualize, impossible to modify without code changes |
| Platform Operator (Standalone) | Team running a shared orchestration service where agents live in separate services | A standalone server with HTTP API for process management, agent registration, and run monitoring | No standard way to coordinate agents across services; each integration is bespoke |
| Process Author | Technical user who defines and tunes multi-agent workflows | A human-readable, version-controlled format for process definitions with clear validation | Process logic buried in code; changes require engineering effort; no visibility into flow structure |
| Checkpoint Reviewer | Human in the loop who approves, rejects, or redirects at decision points | Clear surfacing of AI recommendations with confidence scores and the ability to override | AI decisions happen opaquely; no structured way to intervene or audit the decision path |

---

## Value Proposition

**For** developers and teams building AI-powered applications with multi-agent workflows
**Who** need reliable, auditable, and modifiable process orchestration
**This product** provides a framework for defining processes as directed graphs with typed nodes, pluggable storage, and a full autonomy spectrum for AI decisions
**Unlike** LangGraph, CrewAI, and other agent frameworks that couple orchestration to their agent runtime
**Our approach** is framework-agnostic at the orchestration level — Roots calls agents, it does not depend on how those agents are built. Process definitions are YAML artifacts, not code.

---

## Success Criteria

| Criterion | Metric | Target |
|-----------|--------|--------|
| Architectural soundness | Walking skeleton executes end-to-end (load YAML, run process, persist state, emit events) | Working before any feature buildout |
| Schema completeness | All 8 node types parse, validate, and execute correctly | 100% coverage in v1 |
| Decision spectrum | All 4 decision modes functional with confidence threshold escalation | Independently testable, all modes passing |
| Crash safety | Orchestrator recovers mid-run after simulated crash (kill + restart) | State fully recovered from storage backend |
| Embedded API usability | Consumer can load process, register agents, start run, get graph in <10 lines | Validated by example processes |
| First external consumer | At least one non-trivial process definition authored by someone other than the framework author | Within 30 days of v1 release |
| Test coverage | Core modules (orchestrator, decision, storage) have comprehensive test suites | >90% line coverage on core/, all edge cases from architecture doc covered |

---

## Feature Areas

### Tier 1 -- Core (MVP)

#### T1.1 -- Process Schema Layer
- **Description:** YAML-based process graph definitions with Pydantic v2 models for all 8 node types (agent, agent_pool, decision, checkpoint, fork, join, emit, end), typed edges with metadata (label, condition, event trigger flag), and a schema validator that produces clear error messages referencing offending fields and node IDs. Agent pool nodes support three v1 execution strategies: `parallel`, `sequential`, and `first_pass`. The authoritative definition of what a Roots process is.
- **Dual-edge design:** Decision nodes define their outbound edges *inside their config block* (each with target, condition, label, description). Non-decision nodes use the top-level `edges` list for unconditional routing. The schema validator must enforce this: decision nodes require config-level edges; top-level edges connecting *from* a decision node are invalid.
- **Feature Spec(s):** [feature-process-schema.md](specs/feature-process-schema.md) (8 stories)
- **Status:** Planned

#### T1.2 -- Storage Backend
- **Description:** Abstract async storage interface with SQLite and PostgreSQL implementations. Covers process CRUD, run lifecycle, work item state, run history, checkpoint/escalation state, decision history schema, retry state, webhook registry, and run-level optimistic locking (advisory locks for Postgres, locked_by column for SQLite).
- **Feature Spec(s):** [feature-storage-backend.md](specs/feature-storage-backend.md) (9 stories)
- **Status:** Planned

#### T1.3 -- Orchestrator Engine
- **Description:** Stateless-between-ticks execution engine. A **tick** is one atomic unit of work: load run state from storage, execute the current node, write updated state back, yield. Each tick is independently crash-safe. ProcessRunner class owns a single run's execution loop; Orchestrator class manages multiple runners. The orchestrator **delegates** to T1.4 (Decision Engine) for decision node evaluation and to T1.5 (Agent Registry) for agent invocation -- it does not implement decision logic or agent calling directly. Handles node dispatch by type, edge evaluation, run state machine (pending/running/paused/completed/failed/cancelled), and coordinates with storage for crash-safe state persistence. Confidence-threshold escalation in T1.4 produces an escalation record that the orchestrator handles identically to T2.1 escalations (pause run, emit event, surface via API).
- **State accumulation model:** Each agent/agent_pool node declares an `output_key` in its config. When the node completes, the orchestrator writes its output to `work_item_state[output_key]`. State accumulates as nodes execute -- downstream nodes (including decision conditions) read from the full accumulated state dict. Nodes never overwrite each other's output because each writes to its own key. The orchestrator owns this write; nodes produce output, they don't mutate state directly.
- **Feature Spec(s):** [feature-orchestrator-engine.md](specs/feature-orchestrator-engine.md) (8 stories)
- **Status:** Planned

#### T1.4 -- Decision Engine
- **Description:** All four decision modes: deterministic (safe expression evaluator -- no eval()), ai_bounded, ai_checkpoint, ai_autonomous. LiteLLM for AI modes -- model-agnostic, works with any provider (Anthropic, OpenAI, Gemini, Ollama, etc.) via unified interface. Model configurable per node. Confidence threshold escalation as the primary safety net. Structured response validation via Pydantic.
- **Feature Spec(s):** [feature-decision-engine.md](specs/feature-decision-engine.md) (8 stories)
- **Status:** Planned

#### T1.5 -- Agent Registry & Invocation
- **Description:** Registry mapping agent names to invocation strategies. v1 implements two invocation types: **local callable** and **remote HTTP callback**. MCP invocation is deferred to Tier 2 (T2.5) pending gateway interface design. AgentInvoker handles both types transparently. Input/output schema validation before and after invocation. Timeout handling for remote agents. Schema mismatch raises typed errors for orchestrator handling. All agents must be pre-registered (no inline/anonymous agents in v1).
- **Feature Spec(s):** [feature-agent-registry.md](specs/feature-agent-registry.md) (5 stories)
- **Status:** Planned

#### T1.6 -- Event System
- **Description:** Structured JSON event emission at all lifecycle points (20+ event types). Fire-and-forget via asyncio.create_task() with bounded task set (max concurrent pending emissions, configurable, default 100) to prevent unbounded memory growth from slow sinks. Pluggable sinks: HttpSink, FileSink, StdoutSink. Broken sinks never halt execution; slow sinks shed oldest pending events when buffer is full. WebhookDispatcher with HMAC-SHA256 signatures and pattern-based event filtering.
- **Feature Spec(s):** [feature-event-system.md](specs/feature-event-system.md) (8 stories)
- **Status:** Planned

### Tier 2 -- Extended (v1)

#### T2.1 -- Retry & Escalation
- **Description:** Retry policy as first-class node config (max_attempts, backoff strategies: fixed/linear/exponential, exhaustion behavior: fail or route to fallback edge). Persistent retry state survives crashes. Escalation as distinct from checkpoints -- triggered by schema validation failure, low AI confidence, or explicit agent signal. Both resolved via same API.
- **Feature Spec(s):** [feature-retry-escalation.md](specs/feature-retry-escalation.md) (5 stories)
- **Status:** Planned

#### T2.2 -- Fork/Join Parallel Execution
- **Description:** Matched-pair fork/join nodes for parallel branch execution. Schema validator enforces pairing. Fork creates N sub-run contexts with state copies, executes via asyncio.gather. Join merges via merge_all (deep merge, last-writer-wins) or collect (list under configurable key). allow_partial flag for partial failure tolerance.
- **Feature Spec(s):** [feature-fork-join.md](specs/feature-fork-join.md) (5 stories)
- **Status:** Planned

#### T2.3 -- HTTP API
- **Description:** FastAPI server with async throughout. Process CRUD, run management (start/pause/resume/cancel/history), checkpoint resolution (approve/reject/redirect), agent registration, webhook management, headless graph data endpoints, and **graph mutation endpoints** (add/update/delete nodes, edges, and node positions -- the write contract for any visual editor). Application factory pattern for embedded/standalone/hybrid modes.
- **Feature Spec(s):** [feature-http-api.md](specs/feature-http-api.md) (9 stories)
- **Status:** Planned

#### T2.4 -- CLI
- **Description:** Typer-based CLI (`roots`) for process validation, run management, agent registration, and server startup. Commands: serve, validate, run, status, agents. Useful for CI/CD integration and scripting.
- **Feature Spec(s):** [feature-cli.md](specs/feature-cli.md) (5 stories)
- **Status:** Planned

#### T2.5 -- MCP Agent Invocation
- **Description:** MCP tool invocation as a third agent type. Requires designing the MCP gateway interface: how MCP servers are discovered/configured, how tool calls map to agent input/output contracts, and how MCP server lifecycle is managed. Deferred from T1.5 because the interface design is unresolved.
- **Feature Spec(s):** [feature-mcp-invocation.md](specs/feature-mcp-invocation.md) (5 stories)
- **Status:** Planned

### Tier 3 -- Future (Phase 2)

#### T3.1 -- Decision History Retrieval
- **Description:** Query layer for retrieving past decisions as AI context. The storage schema is designed in v1; the retrieval logic and context injection into AI decision prompts is Phase 2. Enables pattern-based learning: "this type of decision at this node historically resolves this way."
- **Feature Spec(s):** --
- **Status:** Deferred

#### T3.2 -- Process Versioning & Migration
- **Description:** Version management for process graphs with graceful handling of in-flight runs when definitions change. Migration strategies for runs started on v1 of a process when v2 is deployed.
- **Feature Spec(s):** --
- **Status:** Deferred

#### T3.3 -- Process Composition
- **Description:** Sub-process references as node types -- a node whose execution is itself a full process graph. Enables hierarchical process decomposition.
- **Feature Spec(s):** --
- **Status:** Deferred

#### T3.4 -- Vote Aggregation Strategy
- **Description:** Weighted voting for agent_pool nodes -- multiple agents vote on a result, majority wins. Requires design for quorum semantics, tie resolution, and weight configuration.
- **Feature Spec(s):** --
- **Status:** Deferred

#### T3.5 -- Transform Node Type
- **Description:** Declarative state mutation node without invoking a full agent. Lightweight alternative to local agent nodes for deterministic transformations.
- **Feature Spec(s):** --
- **Status:** Deferred

#### T3.6 -- Visual Process Editor
- **Description:** Consumer-built visual editor consuming the headless graph API. Roots is UI-agnostic — any framework works. The reference implementation (built by consumers like Acorn) renders node/edge JSON and translates user gestures into mutation API calls.
- **Feature Spec(s):** --
- **Status:** Deferred

#### T3.7 -- Root Packaging & Distribution
- **Description:** Portable, versionable, shareable process packages. A "Root" (capital R) is a `.root` archive containing a process definition, agent contracts (input/output schemas without implementations), optional default agent implementations, configuration overrides/templates, and metadata. Enables process-as-a-package: open-source security playbooks, compliance workflows, and operational procedures as installable, executable artifacts. `roots pack` exports, `roots inspect` previews, `roots install` loads with contract validation. Agent contracts decouple process definitions from implementations — the same Root works on any system that satisfies the contracts.
- **Feature Spec(s):** [epic-root-packaging.md](specs/epic-root-packaging.md) — 3 feature specs, 16 stories:
  - [feature-root-manifest.md](specs/feature-root-manifest.md) (6 stories) — Manifest schema, contract extraction, archive format, pack/inspect CLI
  - [feature-root-install.md](specs/feature-root-install.md) (5 stories) — Install, contract validation, config overrides, package tracking
  - [feature-root-defaults.md](specs/feature-root-defaults.md) (5 stories) — Default agents, scaffolding, config templates, e2e test
- **Status:** Planned

#### T3.8 -- Root Registry & Marketplace
- **Description:** A registry service for discovering, publishing, and installing Root packages. `roots publish` uploads to the registry. `roots search` finds packages by tag/keyword. `roots install <name>` pulls from the registry instead of a local file. Versioned package resolution, dependency tracking, and community ratings. Think npm for process orchestration.
- **Feature Spec(s):** --
- **Status:** Deferred (depends on T3.7)

---

## Build Order

### Dependency Graph

| Feature | Depends On | Notes |
|---------|-----------|-------|
| T1.1 -- Process Schema | -- | Foundation. Everything parses against these models. |
| T1.2 -- Storage Backend | T1.1 | Storage persists schema-defined entities. SQLite impl enables all testing. |
| T1.3 -- Orchestrator Engine | T1.1, T1.2, T1.4*, T1.5* | Loads schemas, reads/writes state. *Depends on interfaces of T1.4 and T1.5, not full implementation -- develop interface-first. |
| T1.4 -- Decision Engine | T1.1, T1.2 | Expression evaluator + AI modes. Writes decision history to storage. Independently testable. |
| T1.5 -- Agent Registry | T1.1 | Agent types defined in schema layer. Consumed by orchestrator. |
| T1.6 -- Event System | T1.1 | Event types defined in schema. Fire-and-forget, no upstream dependencies. |
| T2.1 -- Retry & Escalation | T1.2, T1.3 | Retry state in storage, escalation pauses runs via orchestrator. |
| T2.2 -- Fork/Join | T1.2, T1.3 | Orchestrator extension. Needs storage for sub-run state persistence. |
| T2.3 -- HTTP API | T1.1-T1.6, T2.1-T2.2 | Thin layer over all core components. |
| T2.4 -- CLI | T2.3 | Wraps API and embedded mode. |
| T2.5 -- MCP Invocation | T1.5 | Extends agent registry with MCP invocation type. |

### Suggested Build Sequence

1. **Phase 1: Schema + Storage** -- T1.1 and T1.2. Defines all data models and persistence. Everything else builds on these. SQLite in-memory enables fast test iteration from day one. Note: fork/join node types are defined in schema (T1.1) but their execution logic lives in T2.2. The schema validator will accept fork/join nodes; the orchestrator will raise "not yet implemented" until T2.2 is complete.
2. **Phase 2: Core Engine** -- T1.4, T1.5 interfaces first (abstract base + type stubs), then T1.3 orchestrator against those interfaces, then T1.4 and T1.5 full implementations, then T1.6 event system (independent). The orchestrator can be tested with mock decision/agent implementations before the real ones are built.
3. **Phase 3: Advanced Execution** -- T2.1 (retry/escalation) and T2.2 (fork/join). These extend the orchestrator with production-critical behaviors.
4. **Phase 4: API Surface** -- T2.3 (HTTP API), T2.4 (CLI), T2.5 (MCP invocation). Thin wrappers over the core engine, built last because the core must be solid first.

---

## Walking Skeleton

**Slice:** Load a two-node linear YAML process, register a local agent, start a run, execute both nodes with state persistence to SQLite, emit events to StdoutSink, and retrieve the run graph with execution state merged in.

**Layers touched:**
- **Schema**: YAML parsed into Pydantic models, validated
- **Storage**: SQLite backend persists process definition, run state, work item state, run history
- **Orchestrator**: Loads run from storage, dispatches agent nodes, advances edges, writes state back
- **Agent Registry**: Local callable registered and invoked by orchestrator
- **Events**: StdoutSink receives lifecycle events (run.started, node.entered, node.completed, run.completed)

**Proves:** The full execution loop works end-to-end -- YAML in, stateless tick-based execution, persistent state, observable events. If this works, every other feature is an extension of the same loop.

**Example process deliverables (v1 smoke tests):**
1. `examples/processes/simple-linear.yaml` -- minimal linear process with two sequential agent nodes, executed end-to-end via embedded API with StdoutSink
2. `examples/processes/parallel-validation.yaml` -- fork/join process with three parallel agent nodes and a decision gate, demonstrating the validation pattern

---

## Constraints & Assumptions

### Constraints
- Python 3.12+ only -- leverages modern async patterns and type syntax
- No agent framework dependencies (no LangChain, LangGraph, CrewAI) -- Roots is framework-agnostic
- LiteLLM for AI decision nodes -- model-agnostic, any provider works via LiteLLM model strings (e.g., `"claude-sonnet-4-20250514"`, `"gpt-4o"`, `"ollama/llama3"`). No vendor-specific SDK dependencies.
- No authentication on the HTTP API in v1 -- deferred to post-v1
- No Docker configuration in v1 -- can be added later
- No visual editor in v1 -- headless graph API only
- Solo developer + Claude Code -- all implementation in a single session or short series of sessions
- Expression evaluator for deterministic decisions: use `simpleeval` library or equivalent rather than building a custom parser from scratch. This is a known rabbit hole -- keep it minimal (field access, comparisons, boolean logic, `in` operator)
- Async SQLite via `aiosqlite` wrapper -- test both SQLite and PostgreSQL backends from day one to catch driver divergence early

### Design Principles
- **Orchestration state is never derived from the event stream.** The storage backend is the source of truth. Events are side effects of state transitions. A consumer with no observability pipeline runs fine.
- **No Poppy-specific or consumer-specific code in this repo.** The architecture doc references Poppy/Acorn as illustrative examples only. Roots is a standalone framework.

### Tooling
- Testing: pytest with pytest-asyncio; SQLite in-memory for storage backend tests
- Packaging: pyproject.toml (no setup.py)
- Type checking: strict pyright
- Tests written alongside implementation, not after

### Assumptions
- Consumers will provide their own agents -- Roots orchestrates, it does not build agents
- SQLite is sufficient for development and lightweight embedded use; PostgreSQL for production
- Process definitions are authored by humans, not generated -- YAML is the source of truth
- LiteLLM is available and the consumer has configured API keys for their chosen provider (via environment variables per LiteLLM conventions)
- A single process run is driven by one orchestrator instance at a time (enforced by run locking)

---

## Open Questions

- [x] Should the expression evaluator for deterministic decisions support function calls? **No -- keep it to field access, comparisons, and boolean logic. Use `simpleeval` or equivalent.**
- [x] How should fork/join handle nested forks? **Deferred to Phase 2 (process composition). v1 supports single-level fork/join only.**
- [x] Should the webhook dispatcher retry failed deliveries? **No in v1. Webhook delivery is fire-and-forget. Consumers who need reliable delivery should use an event sink that writes to a durable queue. Can revisit in Phase 2.**
- [x] Should process definitions support inline agent definitions (anonymous agents)? **No. All agents must be pre-registered. Inline definitions blur the boundary between process definition and agent implementation. Pre-registration keeps the schema clean and enables the registry to validate schemas upfront.**
- [x] What is the MCP gateway interface for `mcp`-type agent invocation? **Deferred to T2.5 (Phase 4). v1 ships with local and remote invocation only. MCP design depends on the MCP ecosystem stabilizing further.**
- [x] Should the safe expression evaluator support array indexing (e.g., `output.findings[0].severity`)? **Yes. The flatten helper must walk lists too: `results.0.name` → `state["results"][0]["name"]`. Required by the parallel-validation example YAML which uses collect strategy output.**
- [ ] What is the maximum concurrent event emission buffer size default? Needs benchmarking during T1.6 implementation.
