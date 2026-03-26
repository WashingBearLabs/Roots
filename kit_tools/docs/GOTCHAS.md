<!-- Template Version: 2.0.0 -->
<!-- Seeding:
  explorer_focus: architecture, tech-stack
  required_sections: []
  skip_if: never
-->
# GOTCHAS.md

> **TEMPLATE_INTENT:** Document known issues, quirks, and non-obvious behaviors. Landmines to avoid.

> Last updated: 2026-03-26
> Updated by: Claude

## Overview

Known issues, quirks, tech debt, and non-obvious behaviors in the Roots framework.

---

## Active Gotchas

### 1. Fork/Join Is NOT Crash-Safe

**Location:** `roots/core/orchestrator.py`
**Severity:** High
**Added:** 2026-03-26

**What happens:**
If the process crashes during a fork/join section (parallel branch execution), the run cannot reliably resume from where it left off. Partial branch completions may be lost.

**Why it exists:**
This is a documented v1 limitation. Crash-safe fork/join requires transactional multi-node state updates that are not yet implemented.

**Workaround:**
Design critical workflows to avoid fork/join, or accept that fork/join sections may need to be re-executed from the fork point after a crash.

**Fix planned:** Yes (post-v1)

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
The 8 node types each have different configuration schemas, unified under a single `config` field.

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
