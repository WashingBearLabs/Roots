<!-- Template Version: 2.1.0 -->
---
epic: embedding-enhancements
status: completed
vision_ref: "Framework Consumer (Embedded) persona; Crash safety success criterion"
created: 2026-06-01
updated: 2026-06-02
completed: 2026-06-02
---

# Epic: Embedding Enhancements

## Goal

Enable production embedding of Roots in host applications by adding crash-safe parallel execution, agent context injection, event-driven integration, dynamic iteration, and run metadata. These enhancements unlock patterns like dynamic fan-out (process N items where N is runtime-determined), agent-initiated subprocess execution, and event-driven coordination — all as generic framework features usable by any Roots consumer.

## Context

Roots is being used as the process orchestration engine for applications that manage complex multi-agent workflows. The core framework is complete (121 stories shipped), but several gaps block production embedding:

1. Fork/join loses in-flight branch progress on crash (documented v1 limitation)
2. Agents can't interact with the orchestration system (no context API)
3. No event-driven integration (must poll for run completion)
4. No native way to iterate over a runtime-determined list
5. No way to tag or filter runs by application-specific metadata

Enhancement 5 (LLM callable passthrough) from the original spec is already implemented and excluded from this epic.

## Decomposition

| Seq | Feature Spec | Status | Stories | Size | Dependencies |
|-----|-------------|--------|---------|------|--------------|
| 1 | [feature-run-metadata.md](archive/feature-run-metadata.md) | Planned | 4 | M | None |
| 2 | [feature-event-subscriptions.md](archive/feature-event-subscriptions.md) | Planned | 4 | M | None |
| 3 | [feature-agent-context.md](archive/feature-agent-context.md) | Planned | 4 | M | event-subscriptions, run-metadata |
| 4 | [feature-crash-safe-parallel.md](archive/feature-crash-safe-parallel.md) | Planned | 6 | L | None |
| 5 | [feature-iterator-node.md](archive/feature-iterator-node.md) | Planned | 7 | L | crash-safe-parallel, agent-context |

## Completion Criteria

- [ ] All 5 feature specs completed and archived
- [ ] All existing tests continue to pass (currently 1,441+)
- [ ] Fork/join and parallel agent_pool survive simulated crash recovery
- [ ] Agent with context can start and wait for child runs
- [ ] Iterator node processes N items where N comes from runtime state
- [ ] Run metadata supports rich filtering across both storage backends
- [ ] docs/INTEGRATION_GUIDE.md updated with new capabilities

## Notes

- Original enhancement spec included 6 items; Enhancement 5 (LLM callable passthrough) is already implemented
- Suggested execution order: quick wins first (metadata, events), then foundation (context, crash-safe), then capstone (iterator)
- Audit findings 2026-05-19-002 (lock race window), 2026-05-19-003 (child run cap), 2026-05-19-007 (version pinning for child runs) are relevant to the parallel execution and subprocess work
- The iterator node ships with both sequential and parallel modes together (parallel depends on crash-safe-parallel spec completing first)
