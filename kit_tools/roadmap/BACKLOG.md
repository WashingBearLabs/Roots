# BACKLOG.md

> Last updated: 2026-05-13
> Updated by: Claude

Future work items and ideas for the Roots framework.

---

## Prioritized Items

| Priority | Item | Type | Ref | Status |
|----------|------|------|-----|--------|
| P0 | Root Registry & Marketplace | Feature | T3.8 | Not Started |
| P1 | Decision History Retrieval | Feature | T3.1 | Not Started |
| P1 | Process Versioning | Feature | T3.2 | Not Started |
| P2 | Process Composition | Feature | T3.3 | Not Started |
| P2 | Vote Aggregation | Feature | T3.4 | Not Started |
| P2 | Transform Node | Feature | T3.5 | Not Started |
| P2 | Agent Node Error Key | Feature | T3.9 | Not Started |

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

### Transform Node (T3.5)
**Priority:** P2
**Effort:** Small
**Description:** A new node type for data transformation between process steps. Applies mapping, filtering, or reshaping operations to process state without requiring an agent invocation.

### Agent Node Error Key (T3.9)
**Priority:** P2
**Effort:** Small
**Description:** Optional `error_key` config on agent nodes. After a handler returns, Roots checks if `state[error_key]` exists — if so, marks the node as failed instead of completed. Currently, handlers that write errors to state (e.g., `repo_error`) without raising exceptions are treated as successful completions, allowing entire workflows to "complete" with every step having failed silently. Discovered during Poppy integration where handlers don't raise by convention.

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

*No items currently in the icebox.*
