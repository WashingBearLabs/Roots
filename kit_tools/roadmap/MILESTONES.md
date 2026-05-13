# Milestones

> Last updated: 2026-05-13
> Updated by: Claude

Tracks major milestones for the Roots framework.

---

## Current Target: Library Refinements (v1.1)

**Target:** 2026-05 (in progress)
**Status:** Active — [epic-library-refinements.md](../specs/epic-library-refinements.md)
**Stories:** 9 across 3 feature specs

Internal library improvements:
- Vote Aggregation (T3.4) — agent pool consensus mechanisms (4 stories)
- Decision History Retrieval (T3.1) — query + auto-inject into AI prompts (4 stories)
- Process Versioning (T3.2) — version history + run pinning (3 stories)

---

## Next: Process Composition (v1.2)

**Target:** TBD (after library refinements)
**Status:** Planned — [epic-process-composition.md](../specs/epic-process-composition.md)
**Stories:** 8 across 2 feature specs

Hierarchical process decomposition:
- Subprocess Schema (T3.3) — node type, storage, validation (3 stories)
- Subprocess Execution (T3.3) — orchestrator lifecycle, pause/fail cascading, API (5 stories)

---

## Future: Root Registry (T3.8)

**Target:** TBD (not yet planned)
**Status:** Not Started

The next major milestone after library refinements. Will enable publishing, discovering, and installing .root packages from a central registry.

---

## Completed

- [x] ~~**Roots v1 Core** — Core orchestrator, agent contracts, storage backends, YAML process definitions, expression evaluation, fork/join, decision nodes~~ (71 stories, completed 2026-03-24)
- [x] ~~**Demo Apps** — Reference applications demonstrating Roots framework capabilities~~ (14 stories, completed 2026-03-25)
- [x] ~~**Root Packaging** — .root packaging format, manifest & export, import & install, defaults & config~~ (16 stories, completed 2026-03-25)

---

## Milestone Details

### Roots v1 Core (COMPLETE)
**Completed:** 2026-03-24 | **Stories:** 71

Core framework delivering:
- YAML-based process definitions with validation
- Stateless-between-ticks orchestrator
- Agent contracts decoupling process from implementation
- SQLite and PostgreSQL storage backends
- Custom LLM shim for AI decision nodes
- simpleeval-based safe expression evaluation
- Fork/join parallel execution (not crash-safe in v1)

### Demo Apps (COMPLETE)
**Completed:** 2026-03-25 | **Stories:** 14

Reference implementations demonstrating:
- End-to-end process orchestration
- Agent contract implementation patterns
- Storage backend usage
- LLM integration via custom shim

### Root Packaging (COMPLETE)
**Completed:** 2026-03-25 | **Stories:** 16

Packaging ecosystem delivering:
- Root Manifest & Export (6 stories, 43 acceptance criteria)
- Root Import & Install (5 stories, 37 acceptance criteria)
- Root Defaults & Config (5 stories, 31 acceptance criteria)
- Total: 111 acceptance criteria, all passing
