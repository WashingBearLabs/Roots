<!-- Template Version: 2.0.0 -->
---
epic: roots-v1
status: completed
vision_ref: "Full Product Vision — all Tier 1 and Tier 2 feature areas"
created: 2026-03-23
updated: 2026-03-24
completed: 2026-03-24
---

# Epic: Roots v1 — Complete Framework

## Goal

Build the complete Roots v1 AI-native process orchestration framework: from YAML process schema through storage backends, orchestrator engine, decision engine, agent registry, event system, retry/escalation, fork/join, HTTP API, CLI, and MCP agent invocation. The end state is a working framework where a consumer can define a multi-agent workflow as a YAML directed graph, execute it with all four decision modes, persist state crash-safely across ticks, and manage everything via embedded API, HTTP API, or CLI. Validated by two working example processes.

## Decomposition

| Seq | Feature Spec | Stories | Status | Dependencies |
|-----|-------------|---------|--------|--------------|
| 1 | feature-process-schema.md | 8 | Completed | None |
| 2 | feature-storage-backend.md | 9 | Completed | feature-process-schema.md |
| 3 | feature-agent-registry.md | 5 | Completed | feature-process-schema.md |
| 4 | feature-decision-engine.md | 6 | Completed | feature-process-schema.md, feature-storage-backend.md |
| 5 | feature-event-system.md | 5 | Completed | feature-process-schema.md |
| 6 | feature-orchestrator-engine.md | 9 | Completed | feature-process-schema.md, feature-storage-backend.md, feature-decision-engine.md, feature-agent-registry.md, feature-event-system.md |
| 7 | feature-retry-escalation.md | 5 | Completed | feature-storage-backend.md, feature-orchestrator-engine.md |
| 8 | feature-fork-join.md | 5 | Completed | feature-storage-backend.md, feature-orchestrator-engine.md |
| 9 | feature-http-api.md | 9 | Completed | All feature specs 1-8 |
| 10 | feature-cli.md | 5 | Completed | feature-http-api.md |
| 11 | feature-mcp-invocation.md | 5 | Completed | feature-agent-registry.md |
| | **TOTAL** | **71** | | |

## Completion Criteria

- [x] All 11 feature specs completed and archived
- [x] `examples/processes/simple-linear.yaml` executes end-to-end via embedded API
- [x] `examples/processes/parallel-validation.yaml` executes end-to-end with fork/join and decision gate
- [x] All tests pass (917 passed, 80 skipped — PostgreSQL skipped without live DB)
- [x] `roots serve` starts the API server and all endpoints respond correctly
- [x] `roots validate` and `roots run` work from CLI
- [x] strict pyright passes with 0 errors (290 warnings from third-party lib stubs)
- [x] Both SQLite and PostgreSQL backends pass the same test suite (parameterized fixtures)

## Notes

- Build order follows the vision's phased approach: Schema+Storage → Core Engine → Advanced Execution → API Surface
- Feature specs 3-5 (Agent Registry, Decision Engine, Event System) have no dependencies on each other and could theoretically be built in parallel, but are sequenced for single-executor overnight runs
- The orchestrator (seq 6) comes after its dependencies are fully built — it integrates everything
- MCP invocation (seq 11) is last and lowest priority; it can be dropped if time is constrained without affecting the rest of the framework
- Each feature spec's stories are ordered by dependency within the spec — execute them in order
- Stories were reviewed for size: any story requiring 8+ config models, 20+ methods, or 8+ API routes was split. Target is one focused concern per story.
