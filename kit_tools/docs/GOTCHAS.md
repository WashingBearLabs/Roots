<!-- Template Version: 2.0.0 -->
<!-- Seeding:
  explorer_focus: architecture, tech-stack
  required_sections: []
  skip_if: never
-->
# GOTCHAS.md

> **TEMPLATE_INTENT:** Document known issues, quirks, and non-obvious behaviors. Landmines to avoid.

> Last updated: 2026-06-13
> Updated by: Claude

## Overview

Known issues, quirks, tech debt, and non-obvious behaviors in the Roots framework.

---

## Active Gotchas

### 1. Fork/Join Crash Safety — RESOLVED (was: NOT crash-safe)

**Location:** `roots/core/orchestrator.py`, `roots/storage/*` (`branch_results` table)
**Severity:** ~~High~~ Resolved
**Added:** 2026-03-26 · **Resolved:** 2026-06 (Crash-Safe Parallel Execution feature)

**Original limitation:**
A crash during a fork/join (or parallel agent_pool) section could lose partial branch completions, since branch results were held in in-memory `ProcessRunner` instance variables.

**How it was fixed:**
Each branch now checkpoints its result to the `branch_results` table as it completes (`save_branch_result`). On re-entry after a crash, the handler loads `get_branch_results` and resumes only the branches that had not finished. Covered by `tests/test_fork_join.py::TestCrashSafeForkPersistence` and the crash-recovery suite.

**Residual edge case:** A fork with two outbound edges pointing at the *same* target node maps both branches to the same storage `branch_id`, so one result can overwrite the other on recovery (audit finding 2026-06-01-031, info severity). Give fork edges distinct targets.

---

### 2. EdgeDefinition Alias (from/from_node) Requires by_alias=True

**Location:** `roots/core/schema.py`
**Severity:** High
**Added:** 2026-03-26

**What happens:**
`EdgeDefinition` uses a Pydantic field alias: the Python field name is `from_node` but it serializes to `from` in JSON. If you call `model_dump()` without `by_alias=True`, the output contains `from_node` instead of `from`, which fails validation on deserialization.

**Why it exists:**
`from` is a Python reserved keyword, so the field must be named `from_node` in code.

**Workaround:**
Always use `model_dump(by_alias=True, mode="json")` when serializing any model that contains edges.

**Fix planned:** No — this is by design. The convention must be followed.

---

### 3. NodeDefinition.config Is a Union Type

**Location:** `roots/core/schema.py`
**Severity:** Medium
**Added:** 2026-03-26

**What happens:**
`NodeDefinition.config` can be one of several config types depending on the node type (agent config, decision config, etc.). Accessing type-specific fields without an `isinstance` guard causes pyright errors and potential runtime `AttributeError`.

**Why it exists:**
The 10 node types each have different configuration schemas, unified under a single `config` field.

**Workaround:**
Always use `isinstance` checks before accessing config-specific attributes:
```python
if isinstance(node.config, AgentNodeConfig):
    agent_name = node.config.agent
```

**Fix planned:** No — this is the intended pattern.

---

### 4. simpleeval Strict Mode Limitations

**Location:** `roots/core/decision.py`
**Severity:** Medium
**Added:** 2026-03-26

**What happens:**
The decision engine uses `simpleeval` for expression evaluation in condition-based transitions. Strict mode is enabled, which means: no function calls are allowed in expressions, and resource limits (time, operations) are enforced. Expressions that work in Python will not necessarily work in simpleeval.

**Why it exists:**
Security — process definitions may come from untrusted sources (e.g., imported .root archives). Unrestricted eval would be a code execution vulnerability.

**Workaround:**
Keep decision expressions simple: comparisons, boolean logic, arithmetic, and variable references only. No function calls.

**Fix planned:** No — this is intentional for security.

---

### 5. PostgreSQL Advisory Locks Are Session-Scoped

**Location:** `roots/storage/postgres.py`
**Severity:** Medium
**Added:** 2026-03-26

**What happens:**
PostgreSQL advisory locks used for orchestrator coordination are session-scoped (tied to the database connection). If the connection is returned to a pool and reused by another task, the lock goes with it. This can cause deadlocks or lost mutual exclusion.

**Why it exists:**
PostgreSQL advisory locks are inherently session-scoped; there is no transaction-scoped alternative that survives the async patterns used.

**Workaround:**
Pin connections when holding advisory locks — do not return them to the pool until the lock is released.

**Fix planned:** No — connection pinning is the correct approach.

---

### 6. Pyright Strict Mode Flags Third-Party Libraries

**Location:** Various import sites
**Severity:** Low
**Added:** 2026-03-26

**What happens:**
Running `pyright roots/` in strict mode produces type errors on imports of `simpleeval` and `asyncpg` because these libraries lack complete type stubs.

**Why it exists:**
These are third-party packages that do not ship py.typed markers or complete stub packages.

**Workaround:**
Use `# type: ignore` comments on the specific import lines. This is the accepted project convention.

**Fix planned:** No — upstream type stub availability is out of scope.

---

## Resolved Gotchas

<!-- Move fixed gotchas here for history -->

(None yet)
