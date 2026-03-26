<!-- Template Version: 2.0.0 -->
<!-- Seeding:
  explorer_focus: architecture, dependencies
  required_sections:
    - "Endpoints" or "Commands" or "Interface"
  skip_if: no-api
-->
# API_GUIDE.md

> **TEMPLATE_INTENT:** Document API endpoints, CLI commands, or library interface. The external contract.

> Last updated: 2026-03-26
> Updated by: Claude

---

## Overview

Base URL: `http://localhost:8200` (development)

The Roots API is a RESTful HTTP API served by FastAPI. All endpoints accept and return JSON. The API provides full CRUD for processes, run management, agent registration, webhook configuration, and graph mutations.

---

## Authentication

No authentication is required in v1. All endpoints are open.

---

## Common Response Codes

| Code | Meaning |
|------|---------|
| 200 | Success |
| 201 | Created |
| 204 | Deleted (no content) |
| 400 | Bad request / validation error |
| 404 | Resource not found |
| 422 | Unprocessable entity (Pydantic validation failure) |
| 500 | Internal server error |

---

## Process CRUD

### List Processes

```
GET /processes
```

**Response:** Array of process objects.

---

### Create Process

```
POST /processes
```

**Body:** Process definition (YAML-derived JSON with nodes, edges, metadata).

**Response:** Created process object with generated ID.

---

### Get Process

```
GET /processes/{process_id}
```

**Response:** Single process object.

---

### Update Process

```
PUT /processes/{process_id}
```

**Body:** Updated process definition.

**Response:** Updated process object.

---

### Delete Process

```
DELETE /processes/{process_id}
```

**Response:** 204 No Content.

---

## Run Management

### Create Run

```
POST /runs
```

**Body:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `process_id` | string | yes | ID of the process to execute |
| `input` | object | no | Initial input data for the run |

**Response:** Created run object with run ID and initial state.

---

### List Runs

```
GET /runs
```

**Response:** Array of run objects.

---

### Get Run

```
GET /runs/{run_id}
```

**Response:** Single run object with current state.

---

### Delete Run

```
DELETE /runs/{run_id}
```

**Response:** 204 No Content.

---

### Pause Run

```
POST /runs/{run_id}/pause
```

**Response:** Updated run object with paused status.

---

### Resume Run

```
POST /runs/{run_id}/resume
```

**Response:** Updated run object with active status.

---

### Run History

```
GET /runs/{run_id}/history
```

**Response:** Array of state transitions and events for the run.

---

## Checkpoints

### Get Checkpoint

```
GET /runs/{run_id}/checkpoint
```

**Response:** Current checkpoint data for the run.

---

### Create Checkpoint

```
POST /runs/{run_id}/checkpoint
```

**Body:** Checkpoint data (snapshot of run state).

**Response:** Created checkpoint object.

---

## Agents

### List Agents

```
GET /agents
```

**Response:** Array of registered agent objects.

---

### Register Agent

```
POST /agents
```

**Body:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `name` | string | yes | Agent identifier |
| `type` | string | yes | Agent type: `local`, `http`, or `mcp` |
| `url` | string | no | Endpoint URL (required for `http` and `mcp` types) |
| `config` | object | no | Additional agent configuration |

**Response:** Created agent object.

---

### Delete Agent

```
DELETE /agents/{agent_id}
```

**Response:** 204 No Content.

---

### Agent Health Check

```
GET /agents/{agent_id}/health
```

**Response:** Health status of the agent.

---

## Webhooks

### List Webhooks

```
GET /webhooks
```

**Response:** Array of registered webhook objects.

---

### Create Webhook

```
POST /webhooks
```

**Body:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `url` | string | yes | Delivery URL |
| `events` | array | yes | List of event types to subscribe to |

**Response:** Created webhook object.

---

### Delete Webhook

```
DELETE /webhooks/{webhook_id}
```

**Response:** 204 No Content.

---

### Test Webhook (Ping)

```
POST /webhooks/{webhook_id}/ping
```

**Response:** Ping result with delivery status.

---

## Graph Operations

### Get Process Graph

```
GET /processes/{process_id}/graph
```

**Response:** Full graph structure (nodes, edges, positions) for the process.

---

### Get Run Graph

```
GET /runs/{run_id}/graph
```

**Response:** Graph structure annotated with current run state (active node, completed nodes).

---

### Add Node

```
POST /processes/{process_id}/graph/nodes
```

**Body:** Node definition (type, config, position).

**Response:** Updated graph with the new node.

---

### Update Node

```
PUT /processes/{process_id}/graph/nodes/{node_id}
```

**Body:** Updated node properties.

**Response:** Updated graph.

---

### Delete Node

```
DELETE /processes/{process_id}/graph/nodes/{node_id}
```

**Response:** Updated graph with node removed.

---

### Add Edge

```
POST /processes/{process_id}/graph/edges
```

**Body:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `from_node` | string | yes | Source node ID |
| `to_node` | string | yes | Target node ID |
| `condition` | string | no | Optional transition condition |

**Response:** Updated graph with the new edge.

---

### Delete Edge

```
DELETE /processes/{process_id}/graph/edges/{edge_id}
```

**Response:** Updated graph with edge removed.

---

### Update Node Positions

```
PUT /processes/{process_id}/graph/positions
```

**Body:** Array of `{ node_id, x, y }` position objects.

**Response:** Updated graph with new positions.
