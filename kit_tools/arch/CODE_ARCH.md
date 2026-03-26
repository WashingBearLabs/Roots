<!-- Template Version: 2.0.0 -->
<!-- Seeding:
  explorer_focus: tech-stack, architecture
  required_sections:
    - "Overview"
    - "Directory Structure"
    - "Key Modules"
  skip_if: never
-->
# CODE_ARCH.md

> **TEMPLATE_INTENT:** Document the code structure, key modules, and architectural patterns. The map of how code is organized.

> Last updated: 2026-03-26
> Updated by: Claude

---

## Overview

Roots is a modular monolith organized into six clearly separated packages under the `roots/` directory. Each package owns a single domain concern and communicates through well-defined interfaces.

The primary design principles are:

1. **Async-first** — all I/O is async; sync callables are wrapped via `asyncio.to_thread`
2. **Crash-safe execution** — the tick-based orchestrator persists state after each node so runs survive restarts
3. **Pluggable backends** — storage, agents, and event sinks are abstracted behind protocols/ABCs for easy extension

---

## Directory Structure

```
/
├── roots/                  # Main framework package
│   ├── __init__.py         # Roots class (primary entry point)
│   ├── core/               # Orchestrator, schema, decision engine, retry, escalation, state machine
│   ├── agents/             # Agent registry, invoker, MCP integration
│   ├── storage/            # Abstract backend + SQLite + PostgreSQL implementations
│   ├── events/             # Event emitter, sinks, webhook delivery
│   ├── api/                # FastAPI routers (processes, runs, agents, webhooks, graph)
│   ├── packaging/          # Root manifest, archive (.root), import/install
│   └── cli/                # CLI entry point (main.py)
├── tests/                  # 66 test files, 1,173+ tests
├── demo/                   # 5 demo applications
│   └── run_all.py          # Run all demos (serves on localhost:8200)
├── pyproject.toml          # Project config, dependencies, tool settings
└── kit_tools/              # Documentation and tooling
```

---

## Key Modules

| Module | Purpose | Key Files |
|--------|---------|-----------|
| `roots/core/` | Orchestrator tick loop, process schema (YAML-to-Pydantic), decision engine (4 modes), retry logic, escalation, state machine | `orchestrator.py`, `schema.py`, `decision.py` |
| `roots/agents/` | Agent registry (local, remote HTTP, MCP), invocation dispatch | `registry.py`, `invoker.py` |
| `roots/storage/` | Abstract storage protocol, SQLite implementation, PostgreSQL implementation | `backend.py`, `sqlite.py`, `postgres.py` |
| `roots/events/` | Event emitter with bounded buffer, sink protocol, webhook delivery | `emitter.py`, `sinks.py`, `webhooks.py` |
| `roots/api/` | FastAPI routers for processes, runs, agents, webhooks, and graph mutations | `routers/` |
| `roots/packaging/` | .root archive creation, manifest schema, import/install for portable processes | `manifest.py`, `archive.py`, `install.py` |

---

## Data Flow

```
YAML Process Definition
       │
       ▼
Pydantic Validation (core/schema.py)
       │
       ▼
Storage Backend (persist process)
       │
       ▼
Orchestrator Tick Loop (core/orchestrator.py)
       │  ┌─────────────────────────┐
       ├──│ Decision Engine (4 modes)│
       │  └─────────────────────────┘
       ▼
Agent Invocation (agents/invoker.py)
       │
       ▼
State Accumulation (output_key → run state)
       │
       ▼
Event Emission (fire-and-forget, bounded buffer)
       │
       ▼
Webhook Delivery / Sink Consumers
```

---

## Key Patterns

### Tick-Based Execution
The orchestrator processes one node per tick, persisting state after each step. This makes execution crash-safe — if the process dies, it resumes from the last completed tick. Fork/join is the one exception: it is NOT crash-safe in v1.

### State Accumulation via output_key
Each node declares an `output_key`. When the node completes, its result is stored in the run state under that key. Downstream nodes read from prior output keys, creating an implicit data pipeline through the graph.

### Serialization Convention
All Pydantic models use `model_dump(by_alias=True, mode="json")` for serialization. This is critical because `EdgeDefinition` uses field aliases (`from` aliased to `from_node` in Python). Forgetting `by_alias=True` produces invalid data.

### Error Handling
Nodes support configurable retry with exponential backoff. Failed nodes trigger escalation policies (skip, halt, or notify). Errors are captured in run state, not raised as exceptions during orchestration.

### Event System
Events are fired in a fire-and-forget pattern with a bounded in-memory buffer. Sinks (webhooks, logging, custom) consume events asynchronously. The buffer prevents memory growth if sinks are slow.

---

## Background Jobs / Async Workers

There are no background job queues or cron-based workers. The orchestrator uses a polling tick loop that advances runs. Event emission is fire-and-forget within the async event loop.

---

## Caching Strategy

There is no caching layer in v1. All reads go directly to the storage backend.

---

## Important Classes / Functions

| Name | Location | Purpose |
|------|----------|---------|
| `Roots` | `roots/__init__.py` | Top-level facade — wires together orchestrator, storage, agents, events |
| `ProcessRunner` | `roots/core/orchestrator.py` | Manages a single run's lifecycle through the graph |
| `Orchestrator` | `roots/core/orchestrator.py` | Tick loop that advances all active runs |
| `DecisionEngine` | `roots/core/decision.py` | Evaluates node transitions using 4 decision modes |
| `AgentInvoker` | `roots/agents/invoker.py` | Dispatches work to local, HTTP, or MCP agents |
| `EventEmitter` | `roots/events/emitter.py` | Publishes events to registered sinks with bounded buffer |
| `StorageBackend` | `roots/storage/backend.py` | Abstract protocol for process/run persistence |

---

## State Management

### Backend State

All process definitions and run state are persisted via the `StorageBackend` protocol. SQLite is the default (file: `roots.db`), PostgreSQL is available for production workloads via DSN configuration. Run state is a JSON document updated after each orchestrator tick.

### Session Management

No user sessions — the framework is stateless between API requests. PostgreSQL advisory locks are session-scoped for concurrent orchestrator instances (requires connection pinning).

---

## External Service Integrations

| Service | Purpose | Integration Point |
|---------|---------|-------------------|
| Remote HTTP agents | Execute workflow steps on external services | `roots/agents/invoker.py` |
| MCP servers | Model Context Protocol agent integration | `roots/agents/invoker.py` |
| Webhook endpoints | Deliver event notifications to external URLs | `roots/events/webhooks.py` |

---

## Dependencies Worth Noting

| Dependency | Purpose | Gotchas |
|------------|---------|---------|
| `pydantic` v2 | All model definitions and validation | Must use `model_dump(by_alias=True)` for edge serialization |
| `simpleeval` | Safe expression evaluation in decision engine | Strict mode: no function calls allowed, resource limits enforced. Pyright flags it — use `# type: ignore` |
| `asyncpg` | PostgreSQL async driver | Pyright strict mode flags it — use `# type: ignore`. Advisory locks are session-scoped |
| `aiosqlite` | SQLite async driver | Default storage backend |
| `fastapi` | HTTP API framework | All routers are async |
| `uvicorn` | ASGI server | Used by `roots serve` CLI command |

---

## Code Style Quick Reference

- **Python**: ruff for linting, strict pyright for type checking — run `pyright roots/` and `ruff check roots/` before committing
- **Commits**: Conventional commits (`feat:`, `fix:`, `chore:`, `docs:`)

See `kit_tools/docs/CONVENTIONS.md` for full style guide.
