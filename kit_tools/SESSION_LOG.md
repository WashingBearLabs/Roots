<!-- Template Version: 2.0.0 -->
<!-- Seeding:
  explorer_focus: none
  required_sections: []
  skip_if: always
  note: SESSION_LOG is populated during sessions, not during initial seeding
-->
# SESSION_LOG.md

> **TEMPLATE_INTENT:** Running history of development sessions. Enables continuity across sessions and team members.

> This file tracks development sessions to maintain context and prevent drift.

---

## Log Format

```
## YYYY-MM-DD — [Brief Title]

**Duration:** ~X hours
**Focus:** [Main topic/feature]

### Accomplished
- [What was done]

### Documentation Updated
- [x] [File updated]
- [ ] [File that should have been updated but wasn't]

### Open Items
- [Items to pick up next session]

### Notes
[Any context for future sessions]

---
```

---

## Session History

<!-- Newest sessions at top -->

## 2026-06-25 — Dotted-Path Resolution + v0.1.1 Release

**Duration:** ~1 session
**Focus:** Close out a stale epic, then ship the Poppy FR4 cross-repo handoff — dotted-path state resolution for iterator/subprocess reads — as `rootsflow` v0.1.1.

### Accomplished
- **Stale-epic cleanup** — `epic-library-refinements` was `status: active` though all 3 child features shipped + archived on 2026-05-13. Set `completed`, ticked all criteria, and fixed the matching `PRODUCT_VISION` drift (T3.1/T3.2/T3.4 — and T3.3 — `Deferred` → `Completed` with feature-spec links). Completed epics stay in `specs/` per convention (only feature specs move to `archive/`).
- **Dotted-path resolution (feature)** — agent output nests under `output_key`, so iterator `items_key`/`input_mapping` and subprocess `input_mapping` couldn't reach nested values (hard-fail / silent-drop). Added `resolve_state_path` + `STATE_PATH_MISSING` (`decision.py`) and a shared `ProcessRunner._resolve_input_mapping` used at all 4 read sites. Dotless keys unchanged (back-compat). Iterator `input_mapping` now **raises** on a missing key (was silent skip), matching subprocess. Dict-walk only — no list indexing.
- **Tests** — +14 (resolver units, dotted `items_key`/`input_mapping` across sequential + parallel iterator and subprocess, back-compat, raise-on-miss). Full suite 1647 passed, 113 skipped; ruff clean; pyright roots/ 0 errors.
- **Released v0.1.1 end-to-end** — merged to `main` (`1b6955f`) + tag `v0.1.1`; built + `twine check`; **published `rootsflow 0.1.1` to PyPI** (verified installable); site release notes + `roots.md` title bumped and pushed live (auto-deploy).
- **Handoff-back** — delivered a self-contained FR4 planning doc (consume `rootsflow>=0.1.1`, rewire `epic-lifecycle.yaml` to dotted keys; carries forward the 3 Poppy-side out-of-scope items: `output_key: branch_name` collision, missing `epic_context`/`repo_url`/`spec_path` producer, `run-` prefix on `run_id`).

### Documentation Updated
- [x] `specs/epic-library-refinements.md`, `PRODUCT_VISION.md` (epic close-out)
- [x] `arch/DECISIONS.md` (2026-06-25 dotted-path decision)
- [x] `docs/GOTCHAS.md` (#7: raise-on-miss, no list indexing, literal-dot caveat)
- [x] `AGENT_README.md` (auth off-limits note de-staled — `ROOTS_API_KEY` shipped)
- [x] `SYNOPSIS.md` (version v0.1.1, test count, dotted-path on composition line)
- [x] `AUDIT_FINDINGS.md` (close-session quality review: clean + 1 info)

### Open Items
- **FR4 (Poppy repo):** consume v0.1.1 + rewire YAML — tracked in the handoff, not this repo.
- `2026-06-25-001` (info, open): literal-dot top-level key names no longer matchable — documented, intended.

### Notes
- No feature spec for this work — it was a cross-repo handoff (`DOTTED_PATH_HANDOFF.md`, since removed; it named an unreleased project and this repo is public).
- v0.1.1 chosen over 0.2.0 (maintainer call): framed as a state-propagation bug fix though the runbook classes new features as minor.

---

## 2026-06-14 — Public Release v0.1.0 🎉

**Duration:** ~1 long session (continued from the 2026-06-13 merge work below)
**Focus:** Take Roots public — review, harden, publish to PyPI, build the website presence, and ship the first public release.

### Accomplished
- **Public-readiness review** — TruffleHog full-history secret scan (0 verified secrets) + security/packaging deep-dive agents. Surfaced: no LICENSE/README, tracked artifacts, PyPI name collision, SSRF DNS gap, zip-bomb, no API auth.
- **Community files** — added LICENSE (MIT), README, CONTRIBUTING, SECURITY.
- **Repo hygiene** — untracked `roots.db`, `.idea/`, `.pyc`; expanded `.gitignore`.
- **Packaging** — distribution renamed `rootsflow` (import stays `roots`); full metadata/classifiers/URLs; dynamic version from `roots/__init__.py`; sdist allowlist (no internal leak); dropped unused `sqlalchemy`; `typer[all]`→`typer`; added `[mcp]` extra.
- **Security fixes** — SSRF hostname resolution (`url_validator`, +21 tests); `.root` archive size caps (+3 tests); optional `ROOTS_API_KEY` auth + non-local serve warning (+6 tests); verified webhook secrets already masked.
- **CI** — GitHub Actions (ruff/pyright/pytest on 3.12 & 3.13); lint cleaned to green; `AUDIT_FINDINGS` reconciled (critical 2026-03-24-082 → mitigated).
- **Branch protection** on `main` (block force-push/deletion).
- **Published `rootsflow 0.1.0` to PyPI** — verified clean-room `pip install` + `import roots` end-to-end. Tagged `v0.1.0`.
- **Website (washingbearlabs.com)** — Roots card on Open Builds (Beta), `roots.md` docs + `roots-release-notes.md` changelog; docs model `category`→`tags`; consolidated docs index (one card per product, Read more + Changelog links). Live + verified.
- **Release tooling** — `kit_tools/BUMP_VERSION.md` two-repo release runbook (`/kit-tools:bump-version` bumps version, runs the green-gate, and syncs the site docs + changelog).
- **Final leak review** — published artifacts + live site clean; untracked transient kit_tools artifacts (held a personal path); scrubbed unreleased-project names (Poppy/Acorn) and private-infra detail from public docs (Option B).

### Documentation Updated
- [x] SYNOPSIS.md (public/PyPI state, install, links, auth), MILESTONES.md (+ Public Release v0.1.0), BACKLOG.md
- [x] DECISIONS.md (release decisions; fork/join entry marked superseded), API_GUIDE.md (auth), ENV_REFERENCE.md (`ROOTS_API_KEY`)
- [x] AUDIT_FINDINGS.md (reconciliation), README/LICENSE/CONTRIBUTING/SECURITY (new)

### Verification
- Version consistent across every surface: `pyproject = roots.__version__ = PyPI = git tag v0.1.0 = site = 0.1.0`.
- Tests 1633 passed, ruff clean, pyright 0 errors. Both repos clean, on `main`, in sync.

### Open Items / Next Steps
- `arch/DATA_MODEL.md` still an unfilled template (backlog D2).
- Future: feature-spec contribution template (backlog C1) for the spec-based contribution model.
- WBL repo tracks `dist/`+`node_modules/` (gitignore cleanup someday — separate repo).
- Personal path remains in one old git-history commit (low severity; not rewriting — branch protection blocks force-push).

## 2026-06-13 — Process-Composition Merge + Doc Sync

**Duration:** ~1 session
**Focus:** Integrate the long-unmerged `epic/process-composition` (subprocess node type) into `main`, then a full `/kit-tools:sync-project` pass to reconcile doc drift.

### Accomplished
- Discovered `epic/process-composition` was fully built but never merged (branch forked early; `main` had advanced 69 commits with the entire embedding-enhancements epic). `main`'s active subprocess specs were stale 0/69 planning copies; the real completed+archived work lived on the branch.
- Merged via integration branch `merge/process-composition`, fast-forwarded `main` (commit d5b0fd5). 21 conflicts resolved — mostly parallel ITERATOR-vs-SUBPROCESS additions; the real work was re-indexing the `runs` table (now carries `metadata_json` + `parent_run_id` + `parent_node_id`) consistently across sqlite/postgres, and merging `validate_subprocess_references` into one BFS covering both iterator and subprocess refs. Closes audit finding 2026-06-01-010 (`get_child_runs` now exists).
- Dropped 5 embedding specs the merge would have resurrected into active `specs/`; subprocess specs auto-archived, epic marked completed.
- **Doc sync:** fixed node-type count (8 → 10), test count (1,173 → 1,716), and corrected the now-stale "fork/join is NOT crash-safe" claim (the crash-safe-parallel feature from embedding-enhancements made it crash-safe via `branch_results` — uncaught drift) across SYNOPSIS, CODE_ARCH, GOTCHAS, TESTING_GUIDE. Updated MILESTONES (embedding-enhancements → completed, no active target). Added `GET /runs/{id}/children` + `metadata_filter` to API_GUIDE. Fixed ~25 broken spec links in BACKLOG, PRODUCT_VISION, and two epic specs (specs moved to `archive/`).

### Validation
- Tests: 1,603 passed, 113 skipped, 0 failed (was 1,551 on main; +52 subprocess). pyright strict: 0 errors.
- All internal `kit_tools/` doc links resolve.

### Documentation Updated
- [x] SYNOPSIS.md, arch/CODE_ARCH.md, docs/GOTCHAS.md, testing/TESTING_GUIDE.md
- [x] roadmap/MILESTONES.md, roadmap/BACKLOG.md, docs/API_GUIDE.md, PRODUCT_VISION.md
- [x] specs/epic-{library-refinements,embedding-enhancements}.md (link fixes)
- [ ] arch/DATA_MODEL.md — still an unfilled template (pre-existing gap; `runs`/`branch_results`/`decisions` schema undocumented)

### Follow-ups
- `main` is ahead of `origin/main` by ~29 commits — NOT pushed (awaiting user).
- arch/DATA_MODEL.md needs authoring (out of scope for this sync).
- Repo hygiene: `.pyc` bytecode is tracked (should be gitignored).

## 2026-06-08 — Clean Cancellation + Embedding Epic Merge

**Duration:** ~1 hour
**Focus:** Standalone enhancement — honor an external run cancellation that lands mid-node (downstream builder support). Plus merging the completed embedding-enhancements epic to `main`.

### Accomplished
- **Clean cancellation guard** in `ProcessRunner.tick()` (commit `57dd754`): added `_externally_terminal()` helper that re-reads the run and reports whether it reached a terminal state (CANCELLED/FAILED/COMPLETED, or deleted). Called immediately before each of the 5 post-node persists; if terminal, the tick returns False (stop) instead of overwriting. Fixes an external mid-node cancel being clobbered by the post-node persist (or tripping the cancelled→running transition guard).
  - Files: `roots/core/orchestrator.py`, `tests/test_orchestrator_class.py` (`TestMidNodeCancel.test_external_cancel_mid_node_sticks`).
  - Full suite: 1551 passed, 106 skipped.
- **Merged `epic/embedding-enhancements` → `main`** (fast-forward, 69 commits) and pushed to origin.
- **Cleanup:** deleted merged local + remote `epic/embedding-enhancements` branch; dropped a stale stash (was pure `.pyc` noise, no source).

### Documentation Updated
- [x] `kit_tools/SESSION_LOG.md` — this entry
- [x] `kit_tools/AUDIT_FINDINGS.md` — 3 info findings from close-session quality check (2026-06-08-044..046)
- [ ] No spec/milestone update — standalone enhancement, not tied to a feature spec

### Open Items
- **Audit (info, advisory):** new cancellation guard has 3 info findings — failure-path guards untested (044), "failed" history event written before guard bails to CANCELLED (045, intentionally left as-is), residual TOCTOU window where a cancel between guard-read and UPDATE raises `StorageError` (046). None blocking.
- **Doc drift (from session start, unresolved):** GOTCHAS.md #1 still says fork/join is NOT crash-safe, but the merged epic shipped crash-safe parallel execution — reconcile. MILESTONES lists Process Composition complete, but `epic-process-composition.md` (0/9) + subprocess specs (0/38, 0/31) are still `status: active` — close out or reconcile.
- Other active branches untouched: `epic/process-composition`, `epic/library-refinements`.

### Notes
User opted to keep doc changes minimal this session ("just log it"). The downstream use case has a zero-Roots-change fallback (cooperative cancel polling on the consumer side); this guard is the nice-to-have for prompt cancellation.

---

## 2026-06-01/02 — Embedding Enhancements Epic (Complete)

**Duration:** ~8 hours (planning + execution + validation)
**Focus:** Epic: embedding-enhancements — 25 stories across 5 feature specs

### Accomplished
- Created docs/INTEGRATION_GUIDE.md (agent-facing library documentation)
- Planned embedding-enhancements epic (5 specs, initially 18 stories → refined to 25)
- Ran 3 rounds of multi-agent spec validation (75 reviewer invocations)
- Launched guarded autonomous execution (Sonnet impl, Opus verify)
- All 25 stories completed: 29 attempts, 86% first-attempt pass rate
- Post-implementation validation: 0 critical, 15 warnings → all addressed
- Integration guide updated with all new capabilities
- PR #3 created and updated: https://github.com/WashingBearLabs/Roots/pull/3

### Features Shipped
1. **Run Metadata** — metadata dict on runs with $eq/$in/$exists filtering
2. **Event Subscriptions** — on/once/off/wait_for/start_and_wait
3. **Agent Context** — AgentContext with execute_run, needs_context opt-in
4. **Crash-Safe Parallel** — per-branch storage, lock renewal, join recovery
5. **Iterator Node** — 10th node type, sequential + parallel, failure handling

### Key Decisions
- Range operators ($gt/$lt) deferred (cross-backend type coercion unresolved)
- emit() stays sync — subscriptions via asyncio.create_task with separate buffer
- Roots owns single AgentInvoker (passed to Orchestrator)
- _subprocess_depth in work_item_state is single depth mechanism
- Branch IDs from target node/agent name (not positional)
- Lock renewal during gather (not full release)
- clear_branch_results on success only
- Iterator parallel uses create_task + cancel (not gather)

### Documentation Updated
- [x] docs/INTEGRATION_GUIDE.md — Full update with 5 new capabilities
- [x] kit_tools/roadmap/MILESTONES.md — Epic marked complete
- [x] kit_tools/roadmap/BACKLOG.md — Epic added, fork/join debt addressed
- [x] kit_tools/specs/epic-embedding-enhancements.md — status: completed
- [x] kit_tools/specs/archive/ — All 5 feature specs archived

### Open Items
- [ ] Merge PR #3 to main
- [ ] Update SYNOPSIS.md test count (1173 → 1550) and node type count (8 → 10)
- [ ] Update CODE_ARCH.md with new modules (context.py, subscriptions.py)
- [ ] Plan next epic (Root Registry T3.8 or downstream integration)

### Stats
- Tests: 1,441 → 1,550 (+109 new)
- Total sessions spawned: 61 (24 impl, 23 verify, 4 validation + supervisory)
- Validation rounds: 3 pre-execution + 1 post-execution
- Audit findings: 43 logged (0 critical remaining)

---

## 2026-05-25 — Recovered Orphaned Scratchpad

**Duration:** Unknown (recovered from orphaned SESSION_SCRATCH.md)
**Focus:** Library Refinements + Process Composition epics

### Accomplished
- Captured downstream integration feedback (py.typed done, error_key backlogged)
- Implemented T3.9 agent node error_key detection (schema, orchestrator, 9 tests)
- Planned library-refinements epic (9 stories, 3 specs)
- Validated library-refinements epic (all 3 specs passed, stories grew from 9 to 12 after splits)
- Planned process-composition epic (8 stories, 2 specs)
- Added backlog item: In-Repo Documentation (D1, P1)
- Validated feature-decision-history (5 warnings, 5 info, 0 critical; all US met)
- Implemented subprocess-execution US-001 (happy path handler)
- Implemented process-versioning US-001 (version history storage, 13 tests)
- Implemented process-versioning US-002 (pin runs to version, 6 tests)
- Implemented process-versioning US-003 (version management API, 6 tests)
- Validated feature-subprocess-schema (1 critical found and fixed, 1332 tests passing)

### Key Decisions
- error_key checks agent output dict, uses truthiness check
- Deferred Process Composition (T3.3) to own epic — too large
- Iceboxed Transform Node (T3.5) — Roots orchestrates, agents compute
- Subprocess: explicit input/output mapping, synchronous inner tick loop for v1
- Process versioning: separate process_versions table (not PK change)
- Subprocess lock refresh: release + acquire between child ticks

### Documentation Updated
- [x] kit_tools/roadmap/BACKLOG.md
- [x] kit_tools/AUDIT_FINDINGS.md (findings 2026-05-13-013..023, 2026-05-15-001..009)
- [x] kit_tools/specs/ (epic + feature specs created and validated)

### Notes
Recovered from orphaned scratchpad — previous session did not close cleanly.

---

## YYYY-MM-DD — Initial Setup

**Duration:** ~X hours
**Focus:** Project scaffolding

### Accomplished
- Created initial project structure
- Set up kit_tools documentation framework
- Configured basic infrastructure

### Documentation Updated
- [x] SYNOPSIS.md — Initial project description
- [x] CODE_ARCH.md — Basic structure documented
- [x] MILESTONES.md — Initial task list

### Open Items
- [ ] Complete infrastructure setup
- [ ] Deploy initial version

### Notes
Starting fresh. See SYNOPSIS.md for project overview.

---

<!-- Add new sessions above this line -->
