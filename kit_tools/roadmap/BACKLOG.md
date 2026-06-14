# BACKLOG.md

> Last updated: 2026-06-14
> Updated by: Claude

Future work items and ideas for the Roots framework.

---

## Prioritized Items

| Priority | Item | Type | Ref | Status |
|----------|------|------|-----|--------|
| P0 | ~~Embedding Enhancements~~ | Epic | E1 | **Done** (2026-06) |
| P0 | ~~Public Release (v0.1.0)~~ | Release | — | **Done** (2026-06-14) |
| P0 | Root Registry & Marketplace | Feature | T3.8 | Not Started |
| P1 | Contribution model — feature-spec template | Docs | C1 | Not Started — spec-based PRs (contributors write feature specs; maintainer implements) + lighter rules for doc PRs |
| P2 | Fill in DATA_MODEL.md | Docs | D2 | Not Started — runs/branch_results/decisions schema still an unfilled template |
| P1 | ~~Decision History Retrieval~~ | Feature | T3.1 | **Done** (2026-05-13) |
| P1 | ~~Process Versioning~~ | Feature | T3.2 | **Done** (2026-05-13) |
| P2 | ~~Process Composition~~ | Feature | T3.3 | **Done** (2026-05-21) |
| P2 | ~~Vote Aggregation~~ | Feature | T3.4 | **Done** (2026-05-13) |
| P2 | ~~Transform Node~~ | Feature | T3.5 | Iceboxed |
| P2 | ~~Agent Node Error Key~~ | Feature | T3.9 | **Done** (2026-05-13) |
| P1 | In-Repo Documentation | Docs | D1 | Partial (Integration Guide created) |

---

## Future Features

### Root Registry & Marketplace (T3.8)
**Priority:** P0 (next major milestone)
**Effort:** Large
**Description:** Central registry for publishing, discovering, and installing .root packages. Enables community sharing of processes, agent contracts, and configurations. Foundation already laid by .root packaging format.

### Decision History Retrieval (T3.1)
**Priority:** P1
**Effort:** Medium
**Description:** Allow processes to query historical decision outcomes. Enables processes that learn from past executions and adapt behavior based on accumulated decision data.

### Process Versioning (T3.2)
**Priority:** P1
**Effort:** Medium
**Description:** Version management for process definitions. Support running multiple versions of a process simultaneously, migration between versions, and rollback capabilities.

### Process Composition (T3.3)
**Priority:** P2
**Effort:** Large
**Description:** Enable processes to invoke other processes as sub-processes. Supports modular process design where complex workflows are composed from simpler, reusable building blocks.

### Vote Aggregation (T3.4)
**Priority:** P2
**Effort:** Small
**Description:** Built-in support for multi-agent voting and consensus mechanisms in decision nodes. Aggregate responses from multiple agents to make collective decisions.

### ~~Transform Node (T3.5)~~ — ICEBOXED
**Reason:** Scope creep risk — data transformation is a deep rabbit hole that pulls Roots away from its orchestration focus. Adapter agents (3-4 lines of Python) handle this idiomatically. Revisit if .root package portability makes trivial adapter agents a recurring pain point.

### ~~Agent Node Error Key (T3.9)~~ — DONE
**Completed:** 2026-05-13 | Commit `7188b9f`
**Description:** Optional `error_key` on `AgentNodeConfig`. After agent returns, if `output[error_key]` is truthy, node and run are failed. Output is still stored in state for inspection.

### In-Repo Documentation (D1)
**Priority:** P1
**Effort:** Medium
**Description:** Agent-friendly in-repo documentation for the Roots framework. Covers getting started, core concepts (process definitions, node types, orchestrator, agents, storage), YAML reference, API reference, and extension guides. Written so that an AI agent or new developer can ingest it and build on Roots without reading the source. Lives in `docs/` at the repo root (distinct from `kit_tools/docs/` which is internal project docs).

---

## Library Refinements (Epic)
- [Epic Overview](../specs/epic-library-refinements.md)
- [Vote Aggregation](../specs/archive/feature-vote-aggregation.md) — Agent pool consensus (T3.4, 4 stories)
- [Decision History](../specs/archive/feature-decision-history.md) — Query + auto-inject (T3.1, 4 stories)
- [Process Versioning](../specs/archive/feature-process-versioning.md) — Version pinning (T3.2, 3 stories)

## Process Composition (Epic)
- [Epic Overview](../specs/epic-process-composition.md)
- [Subprocess Schema](../specs/archive/feature-subprocess-schema.md) — Node type, storage, validation (T3.3, 3 stories)
- [Subprocess Execution](../specs/archive/feature-subprocess-execution.md) — Orchestrator lifecycle, pause/fail cascading, API (T3.3, 5 stories)

## Embedding Enhancements (Epic)
- [Epic Overview](../specs/epic-embedding-enhancements.md)
- [Run Metadata](../specs/archive/feature-run-metadata.md) — Run tagging and rich filtering (3 stories)
- [Event Subscriptions](../specs/archive/feature-event-subscriptions.md) — Callback hooks and wait_for (3 stories)
- [Agent Context](../specs/archive/feature-agent-context.md) — Agent context injection for orchestration access (3 stories)
- [Crash-Safe Parallel](../specs/archive/feature-crash-safe-parallel.md) — Per-branch state checkpointing (5 stories)
- [Iterator Node](../specs/archive/feature-iterator-node.md) — Dynamic for_each iteration (4 stories)

---

## Technical Debt

### ~~Fork/Join Crash Safety~~ — ADDRESSED
**Impact:** ~~Medium~~ → Addressed by Embedding Enhancements epic (feature-crash-safe-parallel.md)

### .gitignore for __pycache__
**Impact:** Low
**Description:** Ensure `__pycache__/` directories are properly gitignored across the project. Minor housekeeping item.

### Pyright Warning Cleanup
**Impact:** Low
**Description:** Pyright shows errors on some third-party imports (currently downgraded to warnings in config). Clean up type stubs or add explicit `# type: ignore` annotations where appropriate to reduce noise.

---

## Icebox

- **Transform Node (T3.5)** — Data transformation between steps. Iceboxed: Roots orchestrates, agents compute. Revisit if .root portability demands it.
