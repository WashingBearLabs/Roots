<!-- Template Version: 2.0.0 -->
---
feature: retry-escalation
status: active
session_ready: true
depends_on: [storage-backend, orchestrator-engine]
vision_ref: "T2.1 — Retry & Escalation"
type: epic-child
epic: roots-v1
epic_seq: 7
epic_final: false
created: 2026-03-23
updated: 2026-03-23
---

# Feature Spec: Retry & Escalation

## Overview

Retry and escalation extend the orchestrator with production-critical error handling. Retry policies let agent nodes declare their own failure recovery behavior — max attempts, backoff strategies, and what to do on exhaustion. Escalation is the unplanned pause mechanism — triggered when something unexpected happens at runtime (schema validation failure, low AI confidence, explicit agent signal). Both are resolved via the same checkpoint API.

## Goals

- Implement retry policies with three backoff strategies and persistent state that survives crashes
- Implement escalation as a distinct path from planned checkpoints
- Provide a unified resolution flow for both checkpoints and escalations

## User Stories

### US-001: Retry Execution with Backoff

**Description:** As a framework developer, I want the orchestrator to retry failed agent nodes with configurable backoff so that transient failures don't immediately fail runs.

**Implementation Hints:**
- Create `roots/core/retry.py` with retry logic
- **Error classification — retryable vs immediately-escalatable:**
  - `AgentInvocationError` (timeout, connection failure, HTTP 5xx) → **retryable**
  - `AgentSchemaValidationError` → **NOT retryable** (schema won't fix itself). Skip retry, escalate immediately.
  - `AgentNotFoundError` → **NOT retryable**. Fail immediately.
  - Generic `Exception` from callable → **retryable** (transient failures)
  - Define a helper: `is_retryable(error: Exception) -> bool` that checks the exception type
- Function `async execute_with_retry(node, execute_fn, storage, run_id, emitter) -> dict`:
  - Load retry config from node (`node.retry` — may be None, meaning no retry)
  - If no retry config or `max_attempts == 1`: just execute and return (no retry)
  - Load retry state from storage (`storage.get_retry_state(run_id, node.id)`)
  - On failure: check `is_retryable(error)`. If NOT retryable: re-raise immediately (let the escalation handler catch it). If retryable AND current attempt < max_attempts:
    - Persist incremented attempt count to storage BEFORE executing
    - Execute the node function
    - On success: clear retry state, return output
    - On failure: compute backoff delay, `await asyncio.sleep(delay)`, emit `roots.node.retrying` event, recurse/loop
  - Backoff calculation:
    - `fixed`: always `backoff_seconds`
    - `linear`: `backoff_seconds * attempt_number`
    - `exponential`: `backoff_seconds * (2 ** (attempt_number - 1))`
- **Integration granularity:**
  - For `agent` nodes: wrap the single agent invocation in `execute_with_retry`
  - For `agent_pool` parallel mode: wrap EACH individual agent invocation in `execute_with_retry` (not the entire pool). This avoids re-running successful agents when only one fails. Each agent retries independently.
  - For `agent_pool` sequential mode: wrap each step in `execute_with_retry`. If step 2 fails and retries, it retries from step 2, not from step 1.
  - For `agent_pool` first_pass mode: retry is per-agent attempt. If agent A fails and is retried, try A again. If A exhausts retries, move to agent B.

**Acceptance Criteria:**
- [x] Retry state is persisted to storage before each attempt
- [x] Fixed backoff uses constant delay
- [x] Linear backoff increases linearly with attempt number
- [x] Exponential backoff doubles each attempt
- [x] `roots.node.retrying` event emitted on each retry
- [x] Successful execution clears retry state
- [x] Backoff uses `asyncio.sleep` (non-blocking)
- [x] Non-retryable errors (AgentSchemaValidationError, AgentNotFoundError) skip retry and re-raise immediately
- [x] Retry wraps individual agents in pools, not the entire pool
- [x] Tests verify all three backoff strategies, state persistence, and error classification

### US-002: Retry Exhaustion — Fail Mode

**Description:** As a framework developer, I want exhausted retries to fail the run when configured so that unrecoverable failures are surfaced clearly.

**Implementation Hints:**
- When retry attempts are exhausted and `on_exhaustion` is `fail`:
  - Mark the node as failed in run history
  - Set run status to `failed`
  - Emit `roots.node.failed` and `roots.run.failed` events
  - Include last error in the failure event metadata
- The retry state should record the last error message for each attempt

**Acceptance Criteria:**
- [ ] Run transitions to `failed` when retries exhausted with `on_exhaustion: fail`
- [ ] `roots.node.failed` and `roots.run.failed` events are emitted
- [ ] Last error is included in failure metadata
- [ ] Run history records all attempts and the final failure
- [ ] Tests verify full exhaustion path

### US-003: Retry Exhaustion — Route Mode

**Description:** As a framework developer, I want exhausted retries to route to a fallback edge so that process authors can define graceful degradation paths.

**Implementation Hints:**
- When retry attempts are exhausted and `on_exhaustion` is `route`:
  - Mark the node as failed in run history
  - Instead of failing the run, advance to `fallback_edge` target node
  - The `fallback_edge` value is a node ID (validated at schema time in T1.1 US-006)
  - Continue execution from the fallback node
  - Emit `roots.node.failed` event with metadata indicating fallback was taken
- This enables patterns like: "if primary agent fails after 3 tries, route to a simpler fallback agent or a human checkpoint"

**Acceptance Criteria:**
- [ ] Run continues via fallback edge when retries exhaust with `on_exhaustion: route`
- [ ] Run does NOT transition to `failed`
- [ ] Execution continues from the fallback target node
- [ ] Event metadata indicates fallback routing occurred
- [ ] Tests verify fallback routing with subsequent node execution

### US-004: Escalation Triggers

**Description:** As a framework developer, I want escalation to trigger automatically on specific runtime conditions so that unexpected situations are surfaced to humans without process authors needing to predict every failure mode.

**Implementation Hints:**
- Create `roots/core/escalation.py` with escalation logic
- Define `EscalationTrigger` enum: `schema_validation_failure`, `confidence_below_threshold`, `agent_explicit_signal`
- Function `create_escalation_from_error(storage, run_id, node_id, trigger, reason, work_item_state)`:
  - Create escalation record in storage
  - Set run status to `paused`
  - Returns the escalation record
- **Exact integration points in ProcessRunner** (from T1.3):
  - **Schema validation failure:** In `_handle_agent` and `_handle_agent_pool`, wrap the `agent_invoker.invoke()` call in `try/except AgentSchemaValidationError as e:` → call `create_escalation_from_error(storage, run_id, node.id, EscalationTrigger.schema_validation_failure, str(e), work_item_state)`.
  - **AI confidence below threshold:** In `_handle_decision`, when `DecisionResult.escalated == True`, the checkpoint/escalation is already created in US-004 of the orchestrator spec. This trigger creates an escalation record with trigger_type=`confidence_below_threshold`.
  - **Agent explicit signal:** In `_handle_agent` and `_handle_agent_pool`, AFTER successful invocation, check `if output.escalate:` → call `create_escalation_from_error(storage, run_id, node.id, EscalationTrigger.agent_explicit_signal, output.escalation_reason or "Agent requested escalation", work_item_state)`.
- Emit `roots.run.escalated` event with trigger type and reason

**Acceptance Criteria:**
- [ ] Schema validation failure on agent output triggers escalation
- [ ] AI confidence below threshold triggers escalation
- [ ] Agent returning `escalate: true` triggers escalation
- [ ] Run transitions to `paused` on any escalation
- [ ] `roots.run.escalated` event is emitted with trigger type
- [ ] Escalation record includes work item state snapshot
- [ ] Tests cover all three trigger types

### US-005: Checkpoint and Escalation Resolution

**Description:** As a framework developer, I want a unified resolution flow so that both planned checkpoints and escalations can be resolved to continue execution.

**Implementation Hints:**
- Add resolution logic (can live in `roots/core/escalation.py` or a new `roots/core/checkpoint.py`):
- Function `async resolve_pending(storage, run_id, decision, notes=None, redirect_to=None)`:
  - Check for pending checkpoint first, then pending escalation
  - If decision is `approve`: for **planned checkpoints**, the next node is the checkpoint node's first outbound edge target (from top-level edges). For **escalations**, the next node is the AI's recommended edge target (from `ai_recommendation.selected_edge_target` in the checkpoint record) if available; if no recommendation exists (e.g., schema validation escalation), `redirect_to` is required — return error if missing.
  - If decision is `reject`: fail the run
  - If decision is `redirect`: resume run from `redirect_to` node (validate it exists in process)
  - Update the checkpoint/escalation record with resolution
  - Set run status back to `running` (for approve/redirect)
  - Emit `roots.checkpoint.resolved` or `roots.escalation.resolved` event
- The orchestrator's `tick()` will pick up the run on its next cycle since status is `running` again
- For escalation resolution with `redirect`: the human specifies which edge to take (since there may not be a pre-defined next step)

**Acceptance Criteria:**
- [ ] `approve` resumes run from the expected next node
- [ ] `reject` transitions run to `failed`
- [ ] `redirect` resumes run from the specified node
- [ ] Resolution updates the checkpoint/escalation record
- [ ] Appropriate resolved event is emitted
- [ ] Invalid redirect target raises error
- [ ] Tests cover approve, reject, and redirect paths for both checkpoints and escalations

## Out of Scope

- Custom escalation triggers defined by consumers
- Escalation notification channels (email, Slack, etc.)
- Retry backoff jitter (can be added later)
- Retry across orchestrator restarts is handled by persistent state — no explicit crash recovery code needed

## Technical Considerations

- Retry state MUST be persisted before execution, not after — this is what makes retries crash-safe
- `asyncio.sleep` for backoff must not block the event loop
- Escalation and checkpoint resolution share the same API endpoint but different storage tables
- The `redirect` option is particularly important for escalations where the human needs to choose the path

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
