<!-- Template Version: 2.0.0 -->
---
feature: event-system
status: active
session_ready: true
depends_on: [process-schema]
vision_ref: "T1.6 — Event System"
type: epic-child
epic: roots-v1
epic_seq: 5
epic_final: false
created: 2026-03-23
updated: 2026-03-23
---

# Feature Spec: Event System

## Overview

The event system provides structured JSON event emission at all process lifecycle points. Events are fire-and-forget — emission never blocks the orchestrator. The system includes pluggable sinks (StdoutSink, FileSink, HttpSink), a bounded emission buffer to protect against slow sinks, and a WebhookDispatcher that POSTs matching events to registered webhook URLs with optional HMAC-SHA256 signatures.

## Goals

- Implement fire-and-forget event emission that never blocks the orchestrator execution loop
- Provide three built-in sinks covering development, file-based, and HTTP delivery
- Implement webhook dispatch with pattern matching and HMAC signature verification

## User Stories

### US-001: Event Type Catalog and Envelope Model

**Description:** As a framework developer, I want a complete event type catalog and envelope model so that all lifecycle events have a consistent structure.

**Implementation Hints:**
- Create `roots/events/types.py` with:
  - `EventType` as a `StrEnum` with all event types:
    - Run: `roots.run.started`, `roots.run.completed`, `roots.run.failed`, `roots.run.paused`, `roots.run.escalated`
    - Node: `roots.node.entered`, `roots.node.completed`, `roots.node.failed`, `roots.node.retrying`
    - Agent: `roots.agent.invoked`, `roots.agent.returned`, `roots.agent.failed`
    - Decision: `roots.decision.evaluated`, `roots.decision.taken`, `roots.decision.escalated`
    - Checkpoint: `roots.checkpoint.reached`, `roots.checkpoint.resolved`
    - Escalation: `roots.escalation.triggered`, `roots.escalation.resolved`
  - `EventEnvelope` Pydantic model: `event` (str), `timestamp` (datetime), `run_id` (str), `process_id` (str), `node_id` (optional str), `node_type` (optional str), `work_item_id` (optional str), `duration_ms` (optional int), `metadata` (dict, default empty)
- Factory function `create_event(event_type, run_id, process_id, **kwargs) -> EventEnvelope` — sets timestamp to now, passes through optional fields

**Acceptance Criteria:**
- [x] All 18 event types defined in enum
- [x] EventEnvelope model validates all fields
- [x] `create_event` factory produces valid envelopes with auto-timestamp
- [x] Envelope serializes to JSON cleanly (datetime as ISO string)
- [x] Tests verify event creation for each lifecycle category

### US-002: Event Emitter with Bounded Buffer

**Description:** As a framework developer, I want an event emitter that dispatches to sinks asynchronously with a bounded buffer so that slow sinks don't consume unbounded memory or block execution.

**Implementation Hints:**
- Create `roots/events/emitter.py` with `EventEmitter` class
- Constructor takes: `sinks: list[EventSink]` (can be empty), `max_pending: int = 100`
- Define `EventSink` protocol/ABC in `roots/events/sinks.py`: single method `async emit(event: EventEnvelope) -> None`
- `EventEmitter.emit(event)`:
  - For each sink, create an `asyncio.Task` via `asyncio.create_task`
  - Track pending tasks in a set
  - If `len(pending_tasks) >= max_pending`, cancel the oldest task and log a warning (shed oldest)
  - Wrap each sink call in try/except — log exceptions, never propagate
  - Clean up completed tasks from the set
- `EventEmitter.close()` — wait for pending tasks to complete (with timeout) for graceful shutdown
- If no sinks configured, `emit` is a no-op

**Acceptance Criteria:**
- [x] Events are dispatched to all sinks asynchronously
- [x] Sink exceptions are caught and logged, never propagated
- [x] Buffer limit is enforced — oldest tasks shed when full
- [x] No sinks = silent no-op
- [x] `close()` drains pending events
- [x] Tests verify fire-and-forget behavior, exception isolation, buffer shedding

### US-003: StdoutSink and FileSink

**Description:** As a framework developer, I want basic sinks for development and file-based logging so that events are observable without external infrastructure.

**Implementation Hints:**
- In `roots/events/sinks.py`:
  - `StdoutSink(EventSink)`: `async emit(event)` — prints `event.model_dump_json(indent=2)` to stdout. Add optional `compact: bool = False` — if True, prints one-line JSON.
  - `FileSink(EventSink)`: `async emit(event)` — appends `event.model_dump_json() + "\n"` to a file. Constructor takes `path: str | Path`. Use `aiofiles` or `asyncio.to_thread` for non-blocking file I/O. Create file if it doesn't exist.
- Both sinks should handle serialization errors gracefully (log and skip)

**Acceptance Criteria:**
- [ ] StdoutSink prints events to stdout
- [ ] StdoutSink compact mode prints single-line JSON
- [ ] FileSink appends events as JSON lines to file
- [ ] FileSink creates file if it doesn't exist
- [ ] Both handle serialization errors without crashing
- [ ] Tests capture stdout output and verify file contents

### US-004: HttpSink

**Description:** As a framework developer, I want an HTTP sink so that events can be forwarded to external observability systems.

**Implementation Hints:**
- In `roots/events/sinks.py`:
  - `HttpSink(EventSink)`: constructor takes `url: str`, `headers: dict[str, str] = {}`, `timeout_seconds: float = 10`
  - `async emit(event)`: POST event JSON to URL with configured headers
  - Use `httpx.AsyncClient` (shared instance, created on first use or in constructor)
  - On HTTP error: log warning with status code, do not raise
  - On timeout/connection error: log warning, do not raise
  - Add `Content-Type: application/json` header automatically

**Acceptance Criteria:**
- [ ] Events are POSTed to configured URL as JSON
- [ ] Custom headers are included in requests
- [ ] Timeout is enforced
- [ ] HTTP errors are logged, not raised
- [ ] Connection failures are logged, not raised
- [ ] Tests use httpx mock transport

### US-005: WebhookDispatcher (as EventSink)

**Description:** As a framework developer, I want a webhook dispatcher that implements the EventSink interface so that it integrates cleanly with the emitter and delivers events to registered webhooks with HMAC signatures.

**Implementation Hints:**
- Create `roots/events/webhooks.py` with `WebhookDispatcher(EventSink)` — it implements the sink interface so it can be added to the emitter's sinks list like any other sink. No special-casing in the emitter.
- Constructor takes `storage: StorageBackend`
- `async emit(event: EventEnvelope)` (the EventSink interface method):
  - Query `storage.list_webhooks_by_pattern(event.event)` for matching webhooks
  - For each matching webhook, fire-and-forget POST the event payload via `asyncio.create_task`
  - If webhook has `secret`: compute HMAC-SHA256 of the JSON body using the secret, include as `X-Roots-Signature` header (hex digest)
  - On failure per webhook: log warning, do not retry
- HMAC computation: `body_bytes = event.model_dump_json().encode()`, then `hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()`. The signature is computed on the exact bytes sent in the POST body.
- Usage: `EventEmitter(sinks=[StdoutSink(), WebhookDispatcher(storage)])` — no special wiring needed

**Acceptance Criteria:**
- [ ] WebhookDispatcher implements EventSink interface
- [ ] Adding it to emitter's sinks list triggers webhook delivery
- [ ] Events dispatched to webhooks matching event type pattern
- [ ] HMAC-SHA256 signature included when webhook has secret
- [ ] Signature header is `X-Roots-Signature`
- [ ] Delivery failures logged, not retried
- [ ] Emitter works fine without WebhookDispatcher
- [ ] Tests verify: pattern matching, HMAC computation, delivery, integration with emitter

## Out of Scope

- Webhook retry on delivery failure (v1 is fire-and-forget)
- Event persistence or replay
- Event filtering beyond type pattern matching
- Custom event types defined by consumers (they can use metadata)

## Technical Considerations

- `asyncio.create_task` requires a running event loop — the emitter must be used within async context
- The bounded buffer shedding strategy (cancel oldest) is a design choice — document it clearly
- HMAC signature computation must use the exact JSON bytes that are sent in the body
- `httpx.AsyncClient` should be shared across HttpSink and WebhookDispatcher if possible

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
