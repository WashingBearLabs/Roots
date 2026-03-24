<!-- Template Version: 2.0.0 -->
<!-- Seeding:
  explorer_focus: none
  required_sections: []
  skip_if: always
  note: AUDIT_FINDINGS is populated by validate-feature skill, not during seeding
-->
# AUDIT_FINDINGS.md

> **TEMPLATE_INTENT:** Persistent record of code quality, security, and intent alignment findings from automated validation. Tracks findings across sessions with status tracking and archival.

> Last updated: 2026-03-23
> Updated by: Claude (validate-feature)

---

## Status Key

| Status | Meaning |
|--------|---------|
| `open` | Finding has not been addressed |
| `resolved` | Finding has been fixed or addressed |
| `dismissed` | Finding reviewed and intentionally not addressed (with reason) |

## Severity Key

| Severity | Meaning |
|----------|---------|
| `critical` | Must address before shipping — security vulnerabilities, data loss risks, broken functionality |
| `warning` | Should address — convention violations, potential bugs, incomplete error handling |
| `info` | Worth noting — minor style issues, suggestions, observations |

---

## Active Findings

<!-- Newest findings at top. Each entry has a unique ID: YYYY-MM-DD-NNN -->
<!-- Findings are added by /kit-tools:validate-feature -->

### 2026-03-23-001
| Field | Value |
|-------|-------|
| **Category** | quality |
| **Severity** | warning |
| **Status** | open |
| **File** | `roots/core/validator.py` |

**Description:** The `validate_structure` function (lines 105-196) contains ~90 lines of logic covering multiple distinct validation concerns (decision edge exclusivity, edge completeness, end node existence, reachability BFS, fork/join pairing, unpaired joins, fallback edge validity). Exceeds the 50-line guideline.

**Recommendation:** Extract each validation concern into its own private helper function (e.g., `_validate_edge_completeness`, `_validate_reachability`), similar to how `_validate_fork_join_pairing` is already extracted.

---

### 2026-03-23-002
| Field | Value |
|-------|-------|
| **Category** | quality |
| **Severity** | warning |
| **Status** | open |
| **File** | `roots/core/validator.py` |

**Description:** In `load_process_yaml`, `parse_process_dict` is called which internally calls `recompute_fork_join_map()` (which calls `validate_structure`), and then `validate_structure` is called again explicitly. Structural validation runs twice for every YAML load.

**Recommendation:** Either remove the `recompute_fork_join_map()` call from `parse_process_dict`, or skip the second `validate_structure` call in `load_process_yaml`.

---

### 2026-03-23-003
| Field | Value |
|-------|-------|
| **Category** | quality |
| **Severity** | warning |
| **Status** | open |
| **File** | `roots/core/schema.py` |

**Description:** `_node_map` is defined as a class-level annotation `_node_map: dict[str, NodeDefinition] = {}`. This creates a mutable class-level default shared across all instances. While overwritten in the `model_validator`, it could cause issues if validation were skipped.

**Recommendation:** Use `Field(default_factory=dict, exclude=True)` or Pydantic's `PrivateAttr` for internal state.

---

### 2026-03-23-004
| Field | Value |
|-------|-------|
| **Category** | quality |
| **Severity** | warning |
| **Status** | open |
| **File** | `roots/core/schema.py` |

**Description:** The `except Exception as exc` clause in `NodeDefinition.validate_node` is overly broad. It catches all exceptions rather than just `ValidationError`, which could mask unexpected errors.

**Recommendation:** Narrow the exception to catch `pydantic.ValidationError` specifically.

---

### 2026-03-23-005
| Field | Value |
|-------|-------|
| **Category** | security |
| **Severity** | warning |
| **Status** | open |
| **File** | `roots/core/validator.py` |

**Description:** `load_process_yaml` and `validate_process_yaml` accept arbitrary file paths with no restrictions. If exposed through the API or CLI with user-supplied paths, this enables path traversal / arbitrary file read.

**Recommendation:** When exposing file-loading functions via CLI or API, restrict paths to a configured allowed directory using `Path.resolve()` and prefix checking.

---

### 2026-03-23-006
| Field | Value |
|-------|-------|
| **Category** | security |
| **Severity** | warning |
| **Status** | open |
| **File** | `roots/core/validator.py` |

**Description:** No size limit on YAML file reading (`file_path.read_text`). A maliciously large file or one with deeply nested alias expansion could cause DoS.

**Recommendation:** Add a file size check before reading (e.g., reject files larger than 1MB). Consider limiting YAML nesting depth if processing untrusted input.

---

### 2026-03-23-007
| Field | Value |
|-------|-------|
| **Category** | compliance |
| **Severity** | warning |
| **Status** | open |
| **File** | `roots/core/validator.py` |

**Description:** US-006 — `load_process_yaml` does not format Pydantic ValidationErrors into user-friendly messages with node context. The `_format_validation_errors` function exists and is used by `validate_process_yaml`, but `load_process_yaml` lets raw `ValidationError` propagate.

**Recommendation:** Wrap the `parse_process_dict` call in `load_process_yaml` with a try/except for `ValidationError` and re-raise with formatted messages as `ProcessValidationError`.

---

### 2026-03-23-008
| Field | Value |
|-------|-------|
| **Category** | quality |
| **Severity** | info |
| **Status** | open |
| **File** | `tests/test_schema.py` |

**Description:** `from typing import Any` import on line 95 is placed mid-file after the first test class, rather than at the top with other imports.

**Recommendation:** Move to the top of the file alongside other imports.

---

### 2026-03-23-009
| Field | Value |
|-------|-------|
| **Category** | quality |
| **Severity** | info |
| **Status** | open |
| **File** | `tests/test_schema.py` |

**Description:** Helper function `_make_node` uses `id` as a parameter name, shadowing the Python built-in `id()` function. The same helper in `test_validator.py` uses `node_id`.

**Recommendation:** Rename the parameter to `node_id` for consistency.

---

### 2026-03-23-010
| Field | Value |
|-------|-------|
| **Category** | quality |
| **Severity** | info |
| **Status** | open |
| **File** | `roots/core/validator.py` |

**Description:** `_validate_fork_join_pairing` (~70 lines) has 4 levels of nesting from nested BFS inside a loop. Complex but inherent to graph traversal.

**Recommendation:** Consider extracting per-branch BFS traversal into a `_trace_branch_to_join()` helper.

---

### 2026-03-23-011
| Field | Value |
|-------|-------|
| **Category** | quality |
| **Severity** | info |
| **Status** | open |
| **File** | `roots/core/validator.py` |

**Description:** Fork validation error message says "has no outbound edges" but is triggered when `len(branches) < 2`, which includes the case of exactly 1 branch. Inaccurate message text.

**Recommendation:** Adjust message to reflect actual count, e.g., "has {len(branches)} outbound edge(s) — need at least 2 branches".

---

### 2026-03-23-012
| Field | Value |
|-------|-------|
| **Category** | security |
| **Severity** | info |
| **Status** | open |
| **File** | `pyproject.toml` |

**Description:** The `simpleeval` dependency is for evaluating expressions. If condition fields on edges are later evaluated with it, expression injection risks exist.

**Recommendation:** When implementing condition evaluation, configure simpleeval with strict allowlists. Pin to a specific version and track CVEs.

---

### 2026-03-23-013
| Field | Value |
|-------|-------|
| **Category** | security |
| **Severity** | info |
| **Status** | open |
| **File** | `pyproject.toml` |

**Description:** Dependencies lack version pinning (e.g., `pyyaml`, `fastapi`, `litellm`, `httpx` with no version constraints). Increases risk of pulling vulnerable versions.

**Recommendation:** Pin to minimum known-good versions and use a lockfile for reproducible builds.

---

### 2026-03-23-014
| Field | Value |
|-------|-------|
| **Category** | security |
| **Severity** | info |
| **Status** | open |
| **File** | `roots/core/schema.py` |

**Description:** `DecisionNodeConfig.model`, `context_prompt`, and `checkpoint_prompt` fields accept arbitrary strings with no length validation. Excessively large prompts could cause cost/resource issues.

**Recommendation:** Add `max_length` constraints on prompt fields and validate model against an allowlist.

---

### 2026-03-23-015
| Field | Value |
|-------|-------|
| **Category** | compliance |
| **Severity** | info |
| **Status** | open |
| **File** | `roots/core/validator.py` |

**Description:** US-006 — `parse_process_dict` raises raw Pydantic `ValidationError` on schema failures instead of formatted errors. Only `validate_process_yaml` formats them.

**Recommendation:** Catch `ValidationError` in `parse_process_dict` and re-raise as `ProcessValidationError` with formatted messages, or document that raw errors are intentional for API consumers.

---

### 2026-03-23-016
| Field | Value |
|-------|-------|
| **Category** | tests |
| **Severity** | info |
| **Status** | open |
| **File** | `tests/` |

**Description:** All 137 tests pass. 4 `UserWarning` emissions during structural validation tests (unreachable nodes in test fixtures) — expected behavior, not failures.

**Recommendation:** No action required.

---

### 2026-03-23-017
| Field | Value |
|-------|-------|
| **Category** | quality |
| **Severity** | warning |
| **Status** | open |
| **File** | `roots/storage/postgres.py`, `roots/storage/sqlite.py` |

**Description:** `_serialize_process` function is duplicated identically in both backends. `list_webhooks_by_pattern` pattern-matching logic is also duplicated — it is backend-agnostic Python filtering.

**Recommendation:** Extract `_serialize_process` into a shared location (e.g., `roots/storage/base.py`). Move `list_webhooks_by_pattern` to the base class as a concrete method that delegates to `self.list_webhooks()`.

---

### 2026-03-23-018
| Field | Value |
|-------|-------|
| **Category** | quality |
| **Severity** | warning |
| **Status** | open |
| **File** | `roots/storage/postgres.py` |

**Description:** The PostgreSQL locking data model diverges from SQLite. SQLite stores lock state inline on the `runs` table (`locked_by`, `locked_at` columns) while PostgreSQL uses a separate `run_locks` table plus advisory locks. The `stale_timeout_seconds` parameter is accepted but never used in the PostgreSQL backend. Advisory locks use `hashtext()` (32-bit) which has collision risk at scale.

**Recommendation:** (1) Implement stale lock detection using `locked_at` in `run_locks` or document why advisory locks make it unnecessary. (2) Consider using two-key advisory lock (`pg_try_advisory_lock(bigint, bigint)`) or 64-bit hash to reduce collision probability.

---

### 2026-03-23-019
| Field | Value |
|-------|-------|
| **Category** | quality |
| **Severity** | warning |
| **Status** | open |
| **File** | `roots/storage/postgres.py` |

**Description:** The `close()` method (lines 158-164) releases lock connections back to the pool without first calling `pg_advisory_unlock`. If `close()` is called while locks are held, advisory locks may persist until the underlying connections are destroyed.

**Recommendation:** Call `pg_advisory_unlock` on each connection in `_lock_connections` before releasing them to the pool in `close()`.

---

### 2026-03-23-020
| Field | Value |
|-------|-------|
| **Category** | quality |
| **Severity** | info |
| **Status** | open |
| **File** | `roots/storage/sqlite.py` |

**Description:** `save_process` (line 158) and `save_agent` (line 202) use `INSERT OR REPLACE`, which deletes and re-inserts the row — resetting `created_at` on every upsert. The PostgreSQL backend correctly uses `ON CONFLICT DO UPDATE`.

**Recommendation:** Switch to `INSERT ... ON CONFLICT DO UPDATE` to preserve the original `created_at`.

---

### 2026-03-23-021
| Field | Value |
|-------|-------|
| **Category** | quality |
| **Severity** | info |
| **Status** | open |
| **File** | `roots/storage/base.py` |

**Description:** Abstract methods `get_process` and `delete_process` use `id` as a parameter name, shadowing the Python built-in `id()` function.

**Recommendation:** Rename to `process_id` for clarity.

---

### 2026-03-23-022
| Field | Value |
|-------|-------|
| **Category** | quality |
| **Severity** | info |
| **Status** | open |
| **File** | `roots/storage/postgres.py` |

**Description:** `create_checkpoint` (line 442) and `create_escalation` (line 518) use check-then-act pattern (SELECT then INSERT) that is not atomic. Concurrent callers could create duplicate pending records between the check and insert.

**Recommendation:** Use a database-level constraint or serializable transaction to enforce "only one pending checkpoint/escalation per run" atomically. Less likely in SQLite due to single-writer model.

---

### 2026-03-23-023
| Field | Value |
|-------|-------|
| **Category** | security |
| **Severity** | warning |
| **Status** | open |
| **File** | `roots/storage/postgres.py`, `roots/storage/sqlite.py` |

**Description:** Webhook secrets are stored in plaintext in both backends. `list_webhooks` returns the raw secret to all callers. Secrets used for HMAC signing should not need to be retrieved in original form.

**Recommendation:** Hash webhook secrets before storing. When verifying webhook deliveries, compare HMAC digests. At minimum, exclude the secret field from `list_webhooks` responses.

---

### 2026-03-23-024
| Field | Value |
|-------|-------|
| **Category** | security |
| **Severity** | info |
| **Status** | open |
| **File** | `roots/storage/postgres.py` |

**Description:** No foreign key constraints defined. `runs.process_id` does not reference `processes.id`, etc. No indexes beyond primary keys — queries filtering on `run_id` in history/checkpoint/escalation/decision tables will perform full table scans.

**Recommendation:** Add FOREIGN KEY constraints with appropriate ON DELETE behavior. Add indexes on frequently queried columns: `run_history(run_id)`, `checkpoints(run_id, status)`, `escalations(run_id, status)`, `decision_history(process_id, node_id)`.

---

### 2026-03-23-025
| Field | Value |
|-------|-------|
| **Category** | compliance |
| **Severity** | warning |
| **Status** | open |
| **File** | `roots/core/schema.py`, `roots/core/validator.py` |

**Description:** The storage-backend feature spec lists `depends_on: [process-schema]` but includes the full process schema module (268 lines) and validator module (308 lines) in this branch. These belong to the archived `feature-process-schema.md` spec. This constitutes scope creep from a separate feature.

**Recommendation:** If intentionally consolidated (both features shipped in one epic branch), document the decision. The process-schema is a prerequisite that storage-backend needs, so this is acceptable for an epic branch.

---

### 2026-03-23-026
| Field | Value |
|-------|-------|
| **Category** | compliance |
| **Severity** | info |
| **Status** | open |
| **File** | `roots/cli/main.py`, `pyproject.toml` |

**Description:** CLI scaffolding (roots/cli/main.py) and extra dependencies (fastapi, uvicorn, typer, litellm, httpx, sqlalchemy, simpleeval, jsonschema) not described in the storage-backend spec. SQLAlchemy is listed but unused.

**Recommendation:** Minor scaffolding is acceptable for an epic branch. Consider removing unused dependency `sqlalchemy` if not planned for near-term use.

---

### 2026-03-23-027
| Field | Value |
|-------|-------|
| **Category** | compliance |
| **Severity** | info |
| **Status** | open |
| **File** | `roots/storage/sqlite.py` |

**Description:** US-004 spec says `list_history_events` should order by `created_at ASC`. SQLite implementation orders by `id` (AUTOINCREMENT). Functionally equivalent but deviates from spec wording.

**Recommendation:** No functional impact — ordering by AUTOINCREMENT id is actually more reliable for same-timestamp records. No change needed.

---

### 2026-03-23-028
| Field | Value |
|-------|-------|
| **Category** | testing |
| **Severity** | info |
| **Status** | open |
| **File** | `test suite` |

**Description:** Full test suite: 273 passed, 80 skipped (PostgreSQL tests — no `ROOTS_POSTGRES_DSN`), 4 warnings (expected unreachable-node warnings in validator tests). All tests pass.

**Recommendation:** No action required.

---

## Archived Findings

<!-- Resolved or dismissed findings are moved here with their resolution note -->

*No archived findings.*
