# SESSION_SCRATCH.md

> Auto-generated. Append notes as you work. Processed on session close.

## Active Feature

**Working on:** Embedding Enhancements Epic (planning)
**Feature Spec:** epic-embedding-enhancements.md (5 child specs)

---

## Notes

[21:15] Recovered orphaned scratchpad from crashed session, logged to SESSION_LOG.md

[21:20] Created docs/INTEGRATION_GUIDE.md — agent-facing guide for using Roots library
- Files: docs/INTEGRATION_GUIDE.md (new)
- Covers: Roots class, agents, YAML schema, 9 node types, decisions, events, CLI, packaging, patterns
- Picks up work from crashed session that died during file write

[21:45] Planned epic: embedding-enhancements (18 stories, 5 specs)
- Files: kit_tools/specs/epic-embedding-enhancements.md, feature-run-metadata.md, feature-event-subscriptions.md, feature-agent-context.md, feature-crash-safe-parallel.md, feature-iterator-node.md
- Updated: kit_tools/roadmap/BACKLOG.md, kit_tools/roadmap/MILESTONES.md
- Decision: Enhancement 5 (LLM callable passthrough) already implemented — excluded from epic
- Decision: Rich metadata operators ($eq, $ne, $gt, $gte, $lt, $lte, $in, $exists) across both backends
- Decision: Agent context opt-in via needs_context=True at registration, injected as _roots_context key
- Decision: Iterator ships both sequential + parallel modes together (parallel depends on crash-safe-parallel)
- Decision: wait_for requires timeout (no default) to prevent indefinite waits
- Decision: Metadata immutable after run creation

[22:30] Applied batch validation fixes across all 5 specs (25 reviewer findings addressed)
- feature-run-metadata: dropped range operators, restricted to scalars, added error handling, fixed hints
- feature-event-subscriptions: resolved emit() sync issue (create_task), split US-003→US-003+US-004, added start_and_wait, fixed test file name
- feature-agent-context: fixed invoker target (Orchestrator's, not Roots'), unified depth with _subprocess_depth, added run-metadata dependency, committed to contextvars
- feature-crash-safe-parallel: added join recovery path, fixed node_id (fork not join), clear in try/finally, branch_id keying, fixed nesting guard flag, fixed error class name
- feature-iterator-node: added checkpoint cascade, reused ExecutionMode enum, added events, uniform result envelope, max_concurrency, items_key error handling, fixed validator references

[23:30] Applied final round fixes across all 5 specs (round 3 validation findings)
- Total stories: 25 across 5 specs (up from 18 in initial planning)
- run-metadata: 3→4 (split US-002 filter into operators + validation/edge cases)
- event-subscriptions: 4 (added multi-event type support, separate buffer, close() cleanup)
- agent-context: 3→4 (split US-002 wiring from injection; InvocationContext; execute_run returns RunRecord)
- crash-safe-parallel: 6 (lock renewal pattern, clear-on-success-only, escalation via StorageError catch)
- iterator-node: 5→7 (split US-003, US-005; re-added max_depth; output_mapping; get_branch_results for resume detection)
- 3 full validation rounds completed (75 total reviewer invocations)

