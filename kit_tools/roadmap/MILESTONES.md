# Milestones

> Last updated: 2026-06-01
> Updated by: Claude

Tracks major milestones for the Roots framework.

---

## Current Target: Embedding Enhancements (v1.2)

**Target:** 2026-06 (active)
**Status:** Active — [epic-embedding-enhancements.md](../specs/epic-embedding-enhancements.md)
**Stories:** 18 across 5 feature specs

Production embedding improvements:
- Run Metadata & Tagging (3 stories) — run tagging with rich filtering
- Event Subscriptions (3 stories) — callback hooks and wait_for
- Agent Context Injection (3 stories) — agent orchestration access
- Crash-Safe Parallel Execution (5 stories) — per-branch state checkpointing
- Iterator Node (4 stories) — dynamic for_each iteration

---

## Next: Root Registry (T3.8)

**Target:** TBD (not yet planned)
**Status:** Not Started

The next major milestone. Will enable publishing, discovering, and installing .root packages from a central registry.

---

## Completed

- [x] ~~**Roots v1 Core** — Core orchestrator, agent contracts, storage backends, YAML process definitions, expression evaluation, fork/join, decision nodes~~ (71 stories, completed 2026-03-24)
- [x] ~~**Demo Apps** — Reference applications demonstrating Roots framework capabilities~~ (14 stories, completed 2026-03-25)
- [x] ~~**Root Packaging** — .root packaging format, manifest & export, import & install, defaults & config~~ (16 stories, completed 2026-03-25)
- [x] ~~**Library Refinements** — Vote aggregation, decision history, process versioning~~ (11 stories, completed 2026-05-13)
- [x] ~~**Process Composition** — Subprocess node type, pause/fail cascading, depth limits, API~~ (9 stories, completed 2026-05-21)

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
