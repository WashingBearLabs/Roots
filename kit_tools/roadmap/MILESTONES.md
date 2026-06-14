# Milestones

> Last updated: 2026-06-14
> Updated by: Claude

Tracks major milestones for the Roots framework.

---

## Current Target: None active — first public release shipped 🎉

**v0.1.0 is public** (2026-06-14): published to PyPI as `rootsflow`, repo made
public, with a product page and docs on washingbearlabs.com. The next planned
milestone is **Root Registry (T3.8)**, not yet scoped. Run `/kit-tools:plan-epic`
to start the next one.

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
- [x] ~~**Process Composition** — Subprocess node type, pause/fail cascading, depth limits, API~~ (9 stories, completed 2026-05-21; merged to `main` 2026-06-13)
- [x] ~~**Embedding Enhancements (v1.2)** — Run metadata & tagging, event subscriptions, agent context injection, crash-safe parallel execution, iterator node~~ (18 stories, completed 2026-06)
- [x] ~~**Public Release (v0.1.0)** — Repo made public + published to PyPI as `rootsflow`; LICENSE/README/CONTRIBUTING/SECURITY, branch protection, CI (GitHub Actions), security hardening (SSRF resolution, archive size caps, optional API auth), and a product page + docs on washingbearlabs.com~~ (completed 2026-06-14)

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
