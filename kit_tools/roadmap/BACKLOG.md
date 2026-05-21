# BACKLOG.md

> Last updated: 2026-05-21
> Updated by: Claude

Future work items and ideas for the Roots framework.

---

## Prioritized Items

| Priority | Item | Type | Ref | Status |
|----------|------|------|-----|--------|
| P0 | Root Registry & Marketplace | Feature | T3.8 | Not Started |
| P1 | ~~Decision History Retrieval~~ | Feature | T3.1 | **Done** (2026-05-13) |
| P1 | ~~Process Versioning~~ | Feature | T3.2 | **Done** (2026-05-13) |
| P2 | ~~Process Composition~~ | Feature | T3.3 | **Done** (2026-05-21) |
| P2 | ~~Vote Aggregation~~ | Feature | T3.4 | **Done** (2026-05-13) |
| P2 | ~~Transform Node~~ | Feature | T3.5 | Iceboxed |
| P2 | ~~Agent Node Error Key~~ | Feature | T3.9 | **Done** (2026-05-13) |
| P1 | In-Repo Documentation | Docs | D1 | Not Started |

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
- [Vote Aggregation](../specs/feature-vote-aggregation.md) — Agent pool consensus (T3.4, 4 stories)
- [Decision History](../specs/feature-decision-history.md) — Query + auto-inject (T3.1, 4 stories)
- [Process Versioning](../specs/feature-process-versioning.md) — Version pinning (T3.2, 3 stories)

## Process Composition (Epic)
- [Epic Overview](../specs/epic-process-composition.md)
- [Subprocess Schema](../specs/feature-subprocess-schema.md) — Node type, storage, validation (T3.3, 3 stories)
- [Subprocess Execution](../specs/feature-subprocess-execution.md) — Orchestrator lifecycle, pause/fail cascading, API (T3.3, 5 stories)

---

## Technical Debt

### Fork/Join Crash Safety
**Impact:** Medium
**Description:** Fork/join is not crash-safe in v1 (see `arch/DECISIONS.md`). A crash during fork execution loses in-flight branch progress and requires re-execution from the fork node. Needs state checkpointing per branch to enable resumption.

### .gitignore for __pycache__
**Impact:** Low
**Description:** Ensure `__pycache__/` directories are properly gitignored across the project. Minor housekeeping item.

### Pyright Warning Cleanup
**Impact:** Low
**Description:** Pyright shows errors on some third-party imports (currently downgraded to warnings in config). Clean up type stubs or add explicit `# type: ignore` annotations where appropriate to reduce noise.

---

## Icebox

- **Transform Node (T3.5)** — Data transformation between steps. Iceboxed: Roots orchestrates, agents compute. Revisit if .root portability demands it.
