<!-- Template Version: 2.2.0 -->
---
feature: event-subscriptions
status: completed
session_ready: true
depends_on: []
vision_ref: "Framework Consumer (Embedded) persona"
type: epic-child
size: M
epic: embedding-enhancements
epic_seq: 2
epic_final: false
created: 2026-06-01
updated: 2026-06-02
---

# Feature Spec: Event Subscription / Callback Hooks

## Overview

Events are currently emitted fire-and-forget to registered sinks. Sinks are output-only — they can log, write files, or POST webhooks, but there's no way for application code to react to events synchronously (e.g., "when this run completes, update my database" or "wait for this run to finish without polling").

This feature adds a lightweight callback/subscription mechanism that lets application code register handlers for specific event types or specific runs, plus an asyncio-friendly `wait_for` helper.

## Goals

- Enable embedding applications to react to process events without polling
- Provide `wait_for()` for asyncio-native "start a run and wait for it to finish"
- Maintain error isolation — broken callbacks never affect orchestration or other callbacks

## User Stories

### US-001: SubscriptionManager with on/once/off

**Description:** As a framework consumer, I want to register callbacks for specific event types so that my application can react to process lifecycle events.

**Implementation Hints:**
- Create `roots/events/subscriptions.py` with `SubscriptionManager` class
- Internal storage: `dict[str, Subscription]` keyed by subscription_id (uuid4)
- `Subscription` dataclass: id, event_types (list[EventType]), callback (async callable), run_id filter (str | None), once flag (bool). Single EventType wrapped to list internally; `event_types=[]` means match all.
- `on(event_type: EventType | list[EventType], ...)`, `once(event_type: EventType | list[EventType], ...)`, `wait_for(event_type: EventType | list[EventType], ...)` — single EventType is wrapped to `[event_type]` at registration time
- `dispatch(event)` must snapshot subscriptions before iterating (`list(self._subscriptions.values())`) to avoid dict mutation during iteration if a callback calls `off()`
- **Once-subscription removal timing:** Remove the once-subscription from the dict BEFORE invoking its callback (not after). This prevents a double-fire race where two rapid events both snapshot the subscription before either removes it.
- Subscription matching: `not sub.event_types or event.event in sub.event_types` AND `sub.run_id is None or sub.run_id == event.run_id`
- Each callback invocation wrapped in try/except — log errors, continue to next callback
- Use `EventType` from `roots/events/types.py` for type parameter

**Acceptance Criteria:**
- [x] `SubscriptionManager` class in `roots/events/subscriptions.py`
- [x] `on(event_type, callback, run_id=None)` registers persistent callback, returns subscription_id string
- [x] `once(event_type, callback, run_id=None)` registers one-shot callback, auto-removes after first fire
- [x] `off(subscription_id)` removes subscription; returns True if found, False if not
- [x] `dispatch(event)` fires all matching callbacks (type match + optional run_id match)
- [x] Dispatch snapshots subscriptions before iterating (safe against mutation during callbacks)
- [x] Callbacks are async callables accepting `EventEnvelope`; exceptions caught and logged per-callback
- [x] on/once/wait_for accept single EventType or list[EventType] for multi-event matching
- [x] Tests: register/fire/unsubscribe; once auto-removal; run_id filtering; error in callback doesn't affect others
- [x] Full test suite passes
- [x] Typecheck/lint passes

### US-002: EventEmitter integration with error isolation

**Description:** As a framework consumer, I want callbacks to fire reliably after each event without affecting orchestration, even if a callback raises an exception.

**Implementation Hints:**
- **Key architecture decision:** `emit()` is currently a sync `def` (not async), called from 38+ sites across orchestrator, checkpoint, escalation, and retry modules. It must stay sync. Schedule subscription dispatch via `asyncio.create_task()` — same pattern as sink dispatch (`emitter.py:74`). Do NOT add `await` inside `emit()`.
- Add `subscriptions: SubscriptionManager | None = None` parameter to `EventEmitter.__init__` (`roots/events/emitter.py:15-30`)
- **Fix the early-return guard:** Current `emit()` at `emitter.py:58` has `if not self._sinks: return` which would skip subscriptions when no sinks are configured. Change to `if not self._sinks and not self._subscriptions: return`. This is critical for embedded consumers using callbacks without output sinks.
- In `emit()` method (`emitter.py:52-76`): after scheduling sink tasks, if subscriptions is set, create a task for `self._dispatch_subscriptions(event)` using the same bounded buffer pattern as sinks
- `_dispatch_subscriptions` is an async method that calls `await self._subscriptions.dispatch(event)` with try/except for error isolation
- For reentrancy: since dispatch now runs as a task (not inline), reentrancy is naturally handled — a callback that emits just schedules another task. No deferred queue needed.
- Existing EventEmitter tests in `tests/test_emitter.py` (NOT `test_events.py`) must all still pass

**Acceptance Criteria:**
- [x] EventEmitter accepts optional `SubscriptionManager` in constructor
- [x] Subscription dispatch scheduled via `asyncio.create_task()` (emit stays sync)
- [x] Early-return guard updated: `if not self._sinks and not self._subscriptions: return` (subscriptions fire even without sinks)
- [x] Exceptions in callbacks are caught and logged (don't break orchestration or other callbacks)
- [x] Callbacks that emit events don't cause recursion (naturally handled by task scheduling)
- [x] Subscription dispatch uses a separate pending dict from sink tasks (not shared bounded buffer) to prevent sink pressure from shedding subscription tasks
- [x] Existing sink behavior unchanged (all current tests in `tests/test_emitter.py` pass)
- [x] Tests: callback fires after event; error isolation; callback emitting event doesn't recurse
- [x] Full test suite passes
- [x] Typecheck/lint passes

### US-003: wait_for() helper on SubscriptionManager

**Description:** As a framework consumer, I want an asyncio-friendly way to wait for a specific event without polling.

**Implementation Hints:**
- Add `wait_for(event_type, run_id=None, timeout=...)` to `SubscriptionManager`
- Creates `asyncio.Future`, registers `once()` with a callback that resolves the future
- Wraps in `asyncio.wait_for(future, timeout=timeout)` for timeout enforcement
- `timeout` is required (no default value) — design decision to prevent indefinite waits
- Cleanup on timeout or cancellation: use `try/finally` (not just except) to call `off()` — `asyncio.CancelledError` bypasses except clauses
- Multiple concurrent `wait_for` calls for different run_ids must work (each gets its own Future)
- **Race condition note:** `wait_for(run_id=run.id)` must be called after `start_run()` returns the run_id. If the event fires between `start_run()` and `wait_for()`, the event is missed. For race-free usage, register with `run_id=None` and filter in the callback, or use `start_and_wait()` (see US-004).
- Roots.close() must cancel all pending wait_for Futures and call off() for each (prevents hanging coroutines on shutdown)

**Acceptance Criteria:**
- [x] `wait_for(event_type, run_id=None, timeout=...)` returns `EventEnvelope` when event fires
- [x] `timeout` is required (no default); raises `asyncio.TimeoutError` on expiry
- [x] Subscription cleaned up on timeout AND on cancellation (try/finally with off())
- [x] Multiple concurrent `wait_for` calls for different runs work correctly
- [x] Pending wait_for Futures cancelled on Roots.close()
- [x] Tests: happy path; timeout; cancellation cleanup; concurrent waits
- [x] Full test suite passes
- [x] Typecheck/lint passes

### US-004: Roots class subscription API

**Description:** As a framework consumer, I want subscription methods on the Roots class so that I can use `roots.on()`, `roots.wait_for()`, and `roots.start_and_wait()` directly.

**Implementation Hints:**
- Store `SubscriptionManager` directly on `Roots` as `self._subscriptions` — pass it to `EventEmitter` constructor
- Wire `on()`, `once()`, `off()`, `wait_for()` onto `Roots` class (`roots/__init__.py`) — delegate to `self._subscriptions`
- Add `start_and_wait(process_id, work_item, event_type=EventType.RUN_COMPLETED, timeout=..., metadata=None)` convenience method on `Roots`:
  1. Register `once()` subscription with `run_id=None` (don't have run_id yet)
  2. Call `start_run()` to get `RunRecord` with the run_id
  3. The once callback checks `event.run_id == run.id` before resolving the Future (filters in callback, not in subscription registration)
  4. Call `execute_run(run.id)` to trigger execution (start_run only creates the run)
  5. Await the Future with timeout
  6. Cleanup: if `start_run()` throws, cancel the subscription in try/except. If timeout expires, clean up in try/finally.
- `start_and_wait` returns tuple of `(RunRecord, EventEnvelope)`
- **Note:** start_and_wait listens for [RUN_COMPLETED, RUN_FAILED] by default (resolves on either terminal state). If the run fails, the returned EventEnvelope has event=RUN_FAILED — caller checks event type to distinguish success from failure.

**Acceptance Criteria:**
- [x] `Roots.on()`, `Roots.once()`, `Roots.off()`, `Roots.wait_for()` delegate to `SubscriptionManager`
- [x] `SubscriptionManager` stored as `self._subscriptions` on Roots (not reached through EventEmitter internals)
- [x] `Roots.start_and_wait(process_id, work_item, timeout=..., event_type=RUN_COMPLETED)` registers subscription, starts run, triggers execution, and waits (race-free)
- [x] `start_and_wait` filters by run_id in callback (registered before run_id is known)
- [x] `start_and_wait` returns `(RunRecord, EventEnvelope)` tuple
- [x] `start_and_wait` cleans up subscription if `start_run()` throws (no dangling subscription)
- [x] Tests: delegation works; start_and_wait race-free; start_and_wait timeout; start_and_wait with failed run
- [x] Full test suite passes
- [x] Typecheck/lint passes

## Out of Scope

- Event replay / rewind (sinks handle persistence)
- Event filtering by metadata values (filter by type and run_id only)
- Durable subscriptions that survive process restart
- Backpressure on callback execution

## Technical Considerations

- `emit()` is sync and must stay sync — 38+ call sites across 4 modules cannot be migrated to async. Subscription dispatch runs as an `asyncio.create_task()` alongside sink tasks.
- EventEnvelope must be exported from roots/__init__.py — consumers need it for callback type annotations.
- Because dispatch runs as a task, callbacks execute asynchronously after `emit()` returns. This means `wait_for()` timing depends on the event loop scheduling the dispatch task. In practice this is fine — the Future resolves on the next event loop tick after the event is emitted.
- Long-running callbacks should `asyncio.create_task()` their own work to avoid blocking the dispatch task
- `start_and_wait()` solves the race condition where the event fires between `start_run()` and `wait_for()` registration — it's the recommended API for the common "start and wait for completion" pattern

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md)

## Implementation Notes

## Refinement Notes

### Research Conducted
- EventEmitter at `emitter.py:15-91` uses OrderedDict for bounded buffer, dispatches to sinks via asyncio.Task
- `emit()` is `def emit` (sync, not async) at `emitter.py:52` — called from 38+ sites without await
- Sinks fire via `_dispatch_to_sink` which catches all exceptions (`emitter.py:32-44`)
- EventEmitter tests are in `tests/test_emitter.py` (not `test_events.py`)
- EventEnvelope has `event` (str), `run_id`, `process_id`, `node_id` fields for matching

### Scope Adjustments
- Split US-003 into wait_for (US-003) and Roots class API (US-004) — distinct concerns, different files
- Added start_and_wait() convenience to US-004 — eliminates documented race condition
- Removed reentrancy deferred queue — asyncio.create_task dispatch naturally prevents recursion

### Decisions Made
- emit() stays sync — dispatch via asyncio.create_task() (same as sinks)
- Callbacks fire after sinks (sinks are the canonical record)
- timeout is required on wait_for (no default) to prevent indefinite waits
- try/finally for cleanup (not just except) — handles CancelledError
- start_and_wait() is the race-free API for the common pattern
- SubscriptionManager stored directly on Roots (not reached through EventEmitter)
- wait_for/on/once accept list[EventType] for multi-event matching (single wrapped to list internally)
- Subscription dispatch uses separate pending dict from sinks (no cross-concern shedding)
