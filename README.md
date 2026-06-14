<div align="center">

# 🌳 Roots

**An AI-native process orchestration framework.**

Define multi-step, multi-agent workflows as YAML directed graphs and execute them
with a crash-safe, tick-based orchestrator — pluggable storage, decision modes,
and event-driven extensibility included.

[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Type-checked: pyright strict](https://img.shields.io/badge/pyright-strict-brightgreen.svg)](https://github.com/microsoft/pyright)

</div>

---

## What is Roots?

Roots lets you describe a process — an agent pipeline, an approval workflow, a
fan-out/fan-in computation — as a **directed graph in YAML**, then runs it with a
stateless-between-ticks orchestrator that persists state after every node. Because
state is checkpointed continuously, runs survive crashes and restarts.

The process definition is decoupled from implementation: nodes reference **agents**
by name (a local Python callable, a remote HTTP service, or an MCP tool), and the
graph routes between them using deterministic conditions or AI-driven decisions.

```
   ┌─────────┐      ┌──────────┐      ┌─────────┐
   │  agent  │─────▶│ decision │─────▶│   end   │
   └─────────┘      └────┬─────┘      └─────────┘
                         │ condition
                         ▼
                    ┌─────────┐
                    │subprocess│  ← compose whole processes
                    └─────────┘
```

## Features

- **YAML process graphs** — 10 node types (agent, agent_pool, decision, checkpoint,
  fork, join, emit, end, iterator, subprocess) and 4 decision modes
  (deterministic, ai_bounded, ai_checkpoint, ai_autonomous).
- **Crash-safe orchestrator** — tick-based execution persists state after each node;
  fork/join and parallel pools checkpoint per-branch and resume only incomplete work.
- **Process composition** — `subprocess` nodes call other processes; `iterator` nodes
  fan out a process over a list, with depth limits and cycle detection.
- **Pluggable storage** — SQLite by default, PostgreSQL for production.
- **Agent registry** — local Python callables, remote HTTP agents, and MCP tools,
  with SSRF-validated outbound calls.
- **Events & subscriptions** — webhooks, bounded-buffer emission, and `on`/`once`/
  `wait_for` callback subscriptions.
- **Root packaging** — bundle a process and its agents into a portable `.root` archive.
- **HTTP API & CLI** — full FastAPI surface and a `roots` command-line tool.
- **Typed end-to-end** — Pydantic v2 models, ships `py.typed`, strict `pyright`.

## Installation

Roots is published to PyPI as **`rootsflow`** (the import package is `roots`):

```bash
pip install rootsflow      # or:  uv pip install rootsflow
```

Then use it:

```python
from roots import Roots, SqliteBackend
```

Or install from source for development:

```bash
git clone https://github.com/WashingBearLabs/Roots.git
cd Roots
pip install -e ".[dev]"    # editable, with tests / type-checker / linter
```

Requires **Python 3.12+**. Roots is in active beta (`0.x`).

## Quick start

### 1. Define a process (`echo.yaml`)

```yaml
id: echo
name: Echo Process
version: "1.0.0"

nodes:
  - id: greet
    type: agent
    label: Greet
    config:
      agent: echo_agent
      output_key: greeting
  - id: done
    type: end
    label: Done
    config:
      status: completed

edges:
  - from: greet
    to: done

entry_point: greet
```

### 2. Run it from Python

```python
import asyncio
from roots import Roots, SqliteBackend


async def echo_agent(work_item_state: dict) -> dict:
    return {"message": f"Hello, {work_item_state.get('name', 'world')}!"}


async def main() -> None:
    backend = SqliteBackend("roots.db")
    await backend.initialize()

    async with Roots(storage=backend) as app:
        await app.register_agent("echo_agent", echo_agent)
        await app.load_process("echo.yaml")

        run, _event = await app.start_and_wait("echo", {"name": "Roots"})
        print(run.status, run.work_item_state)


asyncio.run(main())
```

### 3. Or use the CLI

```bash
roots validate echo.yaml          # validate a process definition
roots run echo.yaml --work-item '{"name": "Roots"}'
roots serve                       # start the HTTP API on 127.0.0.1:8000
roots status                      # list recent runs
roots pack ./echo.yaml -o echo.root   # bundle into a portable .root archive
```

## Node types

| Type | Purpose |
|------|---------|
| `agent` | Invoke a single agent (local / HTTP / MCP) |
| `agent_pool` | Run several agents in parallel or sequence, then merge or vote |
| `decision` | Route on conditions — deterministic or AI-driven |
| `checkpoint` | Pause for human approval |
| `fork` / `join` | Split into parallel branches and recombine (crash-safe) |
| `emit` | Emit a custom event |
| `iterator` | Fan a subprocess out over a list (`for_each`) |
| `subprocess` | Call another process as a child run |
| `end` | Terminate the run with a status |

## Architecture

A **tick-based orchestrator** advances each run one node at a time, persisting state
to the `StorageBackend` after every step. Each node declares an `output_key`; its
result is accumulated into the run's state for downstream nodes to read — an implicit
data pipeline through the graph. Expression conditions are evaluated with `simpleeval`
(no `eval`/`exec`), and all models use Pydantic v2.

See [`docs/INTEGRATION_GUIDE.md`](docs/INTEGRATION_GUIDE.md) for the full guide.

## ⚠️ Security note

Roots is **beta software**. The HTTP API has **no authentication** in the current
release and binds to `127.0.0.1` by default. **Do not expose `roots serve` to an
untrusted network** (e.g. `--host 0.0.0.0`) without putting your own authentication
layer in front of it. Process definitions, event sinks, and MCP command-agents are
treated as trusted input.

## Documentation

- [Integration Guide](docs/INTEGRATION_GUIDE.md) — end-to-end usage, node reference, examples
- [`demo/`](demo/) — runnable reference applications
- [`examples/`](examples/) — sample process definitions and packaging

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for development
setup, coding conventions, and the pull-request process.

## License

[MIT](LICENSE) © 2026 Joshua Johnston
