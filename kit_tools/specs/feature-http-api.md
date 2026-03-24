<!-- Template Version: 2.0.0 -->
---
feature: http-api
status: active
session_ready: true
depends_on: [process-schema, storage-backend, decision-engine, agent-registry, event-system, orchestrator-engine, retry-escalation, fork-join]
vision_ref: "T2.3 — HTTP API"
type: epic-child
epic: roots-v1
epic_seq: 9
epic_final: false
created: 2026-03-23
updated: 2026-03-23
---

# Feature Spec: HTTP API

## Overview

The HTTP API is a FastAPI server exposing the full Roots framework via REST endpoints. It covers process CRUD, run management, checkpoint resolution, agent registration, webhook management, headless graph data, and graph mutation endpoints.

## Goals

- Expose all Roots functionality via a clean REST API
- Provide headless graph data endpoints for visual editors
- Support embedded/standalone/hybrid deployment via application factory

## User Stories

### US-001: FastAPI Application Factory

**Description:** As a framework developer, I want an application factory so that the API can be configured for any deployment mode.

**Implementation Hints:**
- Create `roots/api/app.py` with `create_app(roots: Roots) -> FastAPI`
- Store Roots instance in `app.state.roots`
- Dependency function: `async def get_roots(request: Request) -> Roots: return request.app.state.roots`
- Register all routers with prefix: `/processes`, `/runs`, `/agents`, `/webhooks`
- Add CORS middleware (allow all origins in v1 — no auth)
- `GET /` → `{"name": "roots", "version": "0.1.0"}`
- `GET /health` → `{"status": "ok"}`

**Acceptance Criteria:**
- [ ] `create_app` returns configured FastAPI instance
- [ ] Roots accessible via dependency injection in all routes
- [ ] Root and health endpoints work
- [ ] Tests use `httpx.AsyncClient(transport=ASGITransport(app=app))`

### US-002: Process CRUD Routes

**Description:** As an API consumer, I want process management endpoints.

**Implementation Hints:**
- Create `roots/api/routers/processes.py` and request/response models in `roots/api/models.py`
- `POST /processes`: accept JSON body with process definition dict. Parse via `parse_process_dict()`, save to storage. Return `{id, name, version, created_at}`. Status 201.
- `GET /processes`: list all. Return array of `{id, name, version, description}`.
- `GET /processes/{id}`: return full process definition as JSON. 404 if not found.
- `PUT /processes/{id}`: update. Parse and validate new definition. 404 if not found.
- `DELETE /processes/{id}`: remove. 404 if not found. Status 204.
- `GET /processes/{id}/validate`: validate without saving. Return `{valid: bool, errors: [str]}`.

**Acceptance Criteria:**
- [ ] Full CRUD lifecycle works
- [ ] Validation endpoint returns detailed errors
- [ ] 404 on non-existent process ID
- [ ] 201 on create, 204 on delete
- [ ] Tests cover CRUD lifecycle and validation

### US-003: Run CRUD Routes

**Description:** As an API consumer, I want to create, list, get, and cancel runs.

**Implementation Hints:**
- Create `roots/api/routers/runs.py`
- `POST /runs`: body `{process_id, work_item}`. Create run via `roots.start_run()`. Launch background execution: `asyncio.create_task(roots.execute_run(run.id))` — store task ref on `app.state._background_tasks` set to prevent GC. Return RunResponse. Status 201.
- `GET /runs`: query params `process_id` (optional), `status` (optional). Return list of RunResponse.
- `GET /runs/{run_id}`: return run details. 404 if not found.
- `DELETE /runs/{run_id}`: cancel run — update status to `cancelled` via state machine. 404 if not found. 409 if terminal state.

**Acceptance Criteria:**
- [ ] Creating a run starts background execution
- [ ] Background tasks tracked to prevent garbage collection
- [ ] List runs with filters works
- [ ] Cancel validates state transition (409 if already terminal)
- [ ] Tests cover create, list, get, cancel

### US-004: Run Lifecycle Routes

**Description:** As an API consumer, I want pause, resume, and history endpoints.

**Implementation Hints:**
- `POST /runs/{run_id}/pause`: validate transition running→paused. 409 if not running. Update status.
- `POST /runs/{run_id}/resume`: validate transition paused→running. 409 if not paused. Update status. Restart background execution with new `asyncio.create_task`.
- `GET /runs/{run_id}/history`: return list of history events ordered by timestamp. Each event: `{event_type, node_id, data, created_at}`.
- All lifecycle endpoints validate state transitions via the state machine. Return 409 Conflict with `{"detail": "Cannot transition from {current} to {target}. Valid targets: [...]"}`.

**Acceptance Criteria:**
- [ ] Pause transitions running→paused
- [ ] Resume transitions paused→running and restarts execution
- [ ] Invalid transitions return 409 with helpful message
- [ ] History returns ordered events
- [ ] Tests cover pause/resume cycle and invalid transitions

### US-005: Checkpoint and Escalation Resolution Routes

**Description:** As an API consumer, I want to view and resolve checkpoints/escalations.

**Implementation Hints:**
- Create `roots/api/routers/checkpoints.py`
- `GET /runs/{run_id}/checkpoint`: check storage for pending checkpoint, then pending escalation. Return whichever is found with `{id, run_id, node_id, type, prompt, ai_recommendation, status}`. 404 if neither pending.
- `POST /runs/{run_id}/checkpoint`: body `{decision: "approve"|"reject"|"redirect", notes: str|null, redirect_to: str|null}`. Delegate to `roots.resolve_checkpoint()`.
  - `redirect_to` required when decision is `redirect` — return 422 if missing
  - `redirect_to` validated against process nodes — return 400 if invalid node
  - After resolution, if run is resumed, start background execution
- For planned checkpoints with `approve`: next node is the checkpoint node's outbound edge target
- For escalations with `approve`: next node is the AI's recommended edge target (if available) or requires `redirect_to`

**Acceptance Criteria:**
- [ ] GET returns pending checkpoint or escalation details
- [ ] GET returns 404 when nothing pending
- [ ] Approve resumes execution from correct next node
- [ ] Reject fails the run
- [ ] Redirect resumes from specified node
- [ ] Missing redirect_to on redirect returns 422
- [ ] Invalid redirect_to returns 400
- [ ] Tests cover all resolution paths

### US-006: Agent Registry Routes

**Description:** As an API consumer, I want agent registration endpoints for remote agents.

**Implementation Hints:**
- Create `roots/api/routers/agents.py`
- `GET /agents`: list all registered agents. Return `[{name, type, callback_url, created_at}]`.
- `POST /agents`: register remote agent. Body: `{name, type: "remote", callback_url, input_schema, output_schema, timeout_seconds}`. Also persist to storage for recovery after restart.
- `DELETE /agents/{name}`: deregister. 404 if not found.
- `GET /agents/{name}/health`: for remote agents, GET the callback_url with 5s timeout. Return `{name, status: "healthy"|"unhealthy", response_time_ms, error}`. For local agents: return `{status: "healthy"}` (always).

**Acceptance Criteria:**
- [ ] Remote agents registered via API
- [ ] Agent list returns all agents (local + remote)
- [ ] Deregister removes agent
- [ ] Health check pings remote callback URL
- [ ] Health check returns unhealthy on timeout/error
- [ ] Tests cover registration, listing, health check

### US-007: Webhook Routes

**Description:** As an API consumer, I want webhook management endpoints.

**Implementation Hints:**
- Create `roots/api/routers/webhooks.py`
- `GET /webhooks`: list all
- `POST /webhooks`: body `{url, events: ["roots.run.*"], secret: null}`. Save to storage. Return webhook record. Status 201.
- `DELETE /webhooks/{id}`: remove. 404 if not found.
- `POST /webhooks/{id}/test`: send a test event `roots.webhook.test` with `{test: true}` metadata to the webhook URL. Return `{status: "delivered"|"failed", response_code, error}`.

**Acceptance Criteria:**
- [ ] Webhook CRUD works
- [ ] Test ping delivers event and reports result
- [ ] 404 on non-existent webhook
- [ ] Tests cover CRUD and test ping

### US-008: Graph Data Read Endpoints

**Description:** As an API consumer, I want graph data endpoints so that visual editors can render process and run state.

**Implementation Hints:**
- Create `roots/api/routers/graph.py`
- `GET /processes/{id}/graph`: return process as node/edge JSON (same structure as `get_run_graph` but without execution state — all nodes `"pending"`, all edges `"pending"`). Positions from node metadata.
- `GET /runs/{run_id}/graph`: delegate to `roots.get_run_graph(run_id)`. Returns full graph with execution state merged in.
- Both endpoints: 404 if process/run not found
- Max 2 storage queries per endpoint

**Acceptance Criteria:**
- [ ] Process graph returns correct node/edge structure
- [ ] Run graph returns execution state merged in
- [ ] Node and edge status values match architecture doc
- [ ] 404 on missing process/run
- [ ] Tests verify structure matches expected format

### US-009: Graph Mutation Endpoints

**Description:** As an API consumer, I want graph mutation endpoints so that visual editors can modify process definitions.

**Implementation Hints:**
- In `roots/api/routers/graph.py`:
- `POST /processes/{id}/nodes`: body `{id, type, label, config, metadata}`. Add node to process definition. Re-validate entire process. Return updated node. 400 if validation fails.
- `PUT /processes/{id}/nodes/{node_id}`: update node config/label/metadata. Re-validate. 404 if node not found.
- `DELETE /processes/{id}/nodes/{node_id}`: remove node AND all edges referencing it. Re-validate. 404 if not found.
- `POST /processes/{id}/edges`: body `{from, to, label, condition}`. Add edge. Re-validate. 400 if validation fails.
- `DELETE /processes/{id}/edges/{edge_id}`: remove edge. Re-validate.
- `PUT /processes/{id}/nodes/{node_id}/position`: body `{x, y}`. Update node metadata position only — no re-validation needed (positions are opaque).
- Re-validation: after each mutation, run `validate_structure()`. If errors, roll back the change and return 400 with validation errors.

**Acceptance Criteria:**
- [ ] Nodes can be added, updated, and removed
- [ ] Edges can be added and removed
- [ ] Positions can be updated independently
- [ ] Mutations trigger re-validation
- [ ] Invalid mutations return 400 with validation errors
- [ ] Removing a node also removes its edges
- [ ] Tests cover each mutation type

## Out of Scope

- Authentication / authorization
- Rate limiting, pagination
- WebSocket real-time updates

## Technical Considerations

- Background tasks need GC protection — store refs in `app.state._background_tasks` set, remove when done
- Re-validation after mutations is critical — never persist an invalid process definition
- Test with `httpx.AsyncClient(transport=ASGITransport(app=app))` for async testing

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
