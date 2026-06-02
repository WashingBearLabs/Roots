# Roots Integration Guide

> For AI coding agents and developers integrating Roots into applications.
>
> Last updated: 2026-05-25

---

## What Roots Is

Roots is an AI-native process orchestration framework. You define multi-agent workflows as YAML directed graphs, and Roots executes them step by step through a crash-safe tick-based orchestrator.

**The core principle:** Roots nodes represent units of AI work — agents or groups of agents. They do not represent units of computation. If you're tempted to create a node that calls a function, makes an API request, or runs a shell command, that logic belongs inside an agent, not as a separate node.

Roots does not build agents. It calls agents you register. An agent can be a local Python function, a remote HTTP service, or an MCP tool. Roots provides the process structure; your agents provide the intelligence.

---

## Quick Start

### Install

```bash
pip install roots
```

### Minimal Example

```python
import asyncio
from roots import Roots, SqliteBackend

async def my_agent(input: dict) -> dict:
    work_item = input["work_item_state"]
    return {
        "output": {"summary": f"Processed: {work_item['task']}"},
        "escalate": False,
    }

async def main():
    backend = SqliteBackend(":memory:")
    await backend.initialize()

    async with Roots(storage=backend) as app:
        await app.load_process("process.yaml")
        await app.register_agent("my_agent", my_agent)

        run = await app.start_run("my-process", {"task": "Hello world"})
        await app.execute_run(run.id)

        result = await app.get_run(run.id)
        print(result.status)           # "completed"
        print(result.work_item_state)  # includes agent outputs

asyncio.run(main())
```

With this `process.yaml`:

```yaml
id: my-process
name: My Process
version: "1.0.0"
entry_point: do_work

nodes:
  - id: do_work
    type: agent
    label: Do Work
    config:
      agent: my_agent
      output_key: result

  - id: done
    type: end
    label: Done
    config:
      status: completed

edges:
  - from: do_work
    to: done
```

---

## The Roots Class

`Roots` is the primary entry point. It wires together storage, agents, decisions, and events.

```python
from roots import Roots, SqliteBackend, PostgresBackend
from roots import StdoutSink, FileSink, HttpSink
from roots import LLMConfig

app = Roots(
    storage=SqliteBackend("my.db"),       # or PostgresBackend(dsn)
    event_sinks=[StdoutSink()],           # optional: event observers
    default_model="gpt-4o-mini",          # for AI decision nodes
    llm_config=LLMConfig(),              # or pass llm_callable for custom LLM
)
```

Always use it as an async context manager or call `await app.close()` when done:

```python
async with Roots(storage=backend) as app:
    # ... use app ...
```

### Key Methods

| Method | Purpose |
|--------|---------|
| `load_process(path)` | Parse YAML and save to storage |
| `register_agent(name, callable)` | Register a local agent |
| `register_mcp_server(url=..., command=...)` | Auto-discover and register MCP tools as agents |
| `start_run(process_id, work_item)` | Create a new run (returns `RunRecord`) |
| `execute_run(run_id)` | Run to completion (blocks until done or paused) |
| `get_run(run_id)` | Fetch run status and state |
| `get_run_graph(run_id)` | Get headless graph JSON for visualization |
| `resolve_checkpoint(run_id, decision)` | Resume a paused run |
| `close()` | Drain events and release resources |

---

## Writing Agents

An agent is any async callable that accepts a dict and returns a dict. Roots passes an `AgentInput`-shaped dict and expects an `AgentOutput`-shaped dict back.

### What Agents Receive

```python
{
    "work_item_state": {        # accumulated state from prior nodes
        "task": "original input",
        "prior_output": {...},  # output from earlier nodes
    },
    "node_config": {            # the node's config from the process YAML
        "agent": "my_agent",
        "output_key": "result",
    },
    "run_id": "run-abc-123",
}
```

### What Agents Return

```python
{
    "output": {                 # stored under the node's output_key
        "answer": "42",
        "confidence": 0.95,
    },
    "escalate": False,          # set True to pause the run for human review
    "escalation_reason": None,  # explain why (when escalate=True)
}
```

### Agent Contract

- The `output` dict is stored in `work_item_state` at the node's `output_key`. Downstream nodes access it via that key.
- Setting `escalate: True` pauses the run. A human (or API call) must resolve the escalation to continue.
- Agents can optionally declare `input_schema` and `output_schema` (JSON Schema) for validation.

### Registration

```python
# Local function
await app.register_agent("my_agent", my_agent_func)

# With schema validation
await app.register_agent(
    "validated_agent",
    my_func,
    input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
    output_schema={"type": "object", "required": ["score"]},
)

# MCP server (auto-discovers tools)
agent_names = await app.register_mcp_server(url="http://localhost:3000/mcp")
# or
agent_names = await app.register_mcp_server(command=["npx", "my-mcp-server"])
```

---

## Process Definitions (YAML)

A process is a directed graph of nodes connected by edges. Every process needs:
- `id`, `name`, `version` — identity
- `entry_point` — which node starts execution
- `nodes` — the steps
- `edges` — the connections

### The 9 Node Types

#### `agent` — Single Agent Invocation
```yaml
- id: analyze
  type: agent
  label: Analyze Data
  config:
    agent: analyzer          # registered agent name
    output_key: analysis     # where result is stored in state
    error_key: analysis_err  # optional: where to store errors
```

#### `agent_pool` — Multiple Agents
```yaml
- id: review
  type: agent_pool
  label: Review Panel
  config:
    agents: [reviewer_1, reviewer_2, reviewer_3]
    execution_mode: parallel     # parallel | sequential | first_pass
    aggregation: majority_vote   # merge_all | majority_vote | weighted_vote | unanimous
    output_key: review_result
    vote_config:                 # required for vote strategies
      vote_key: verdict          # which key in agent output to vote on
      threshold: 0.5             # fraction needed to win
      tie_break: first_agent     # first_agent | reject
```

#### `decision` — Routing Logic
```yaml
- id: route
  type: decision
  label: Route Decision
  config:
    mode: deterministic          # deterministic | ai_bounded | ai_checkpoint | ai_autonomous
    edges:
      - target: approve
        condition: "analysis.score > 0.8"    # simpleeval expression
        label: High quality
      - target: reject
        condition: "analysis.score <= 0.8"
        label: Low quality
```

For AI decision modes, add:
```yaml
    confidence_threshold: 0.7    # below this → escalate to human
    model: gpt-4o               # optional model override
    context_prompt: "Evaluate the analysis results and choose the best path."
    history_depth: 10            # include past decisions for context
```

#### `checkpoint` — Human Review Gate
```yaml
- id: human_review
  type: checkpoint
  label: Human Approval
  config:
    prompt: "Review the generated content before publishing."
```

#### `fork` / `join` — Parallel Branches
```yaml
- id: split
  type: fork
  label: Parallel Processing
  config: {}

- id: merge
  type: join
  label: Merge Results
  config:
    merge_strategy: merge_all   # merge_all | collect
```

**Note:** Fork/join is not crash-safe. If the process crashes during parallel execution, the run cannot reliably resume from where it left off.

#### `emit` — Fire an Event
```yaml
- id: notify
  type: emit
  label: Notify Downstream
  config:
    event_type: "processing.complete"
    payload_keys: [analysis, review_result]  # keys from state to include
```

#### `end` — Terminal Node
```yaml
- id: done
  type: end
  label: Completed
  config:
    status: completed   # completed | failed
```

#### `subprocess` — Compose Processes
```yaml
- id: sub_task
  type: subprocess
  label: Run Sub-Process
  config:
    process_id: child-process      # must exist in storage
    input_mapping:                  # parent state → child work item
      source_data: raw_input
    output_mapping:                 # child output → parent state
      processed: child_result
    output_key: sub_result
    max_depth: 5                    # recursion limit (1-20)
```

### Edges

Edges connect nodes. For most node types, edges are explicit:

```yaml
edges:
  - from: step_1
    to: step_2
  - from: step_2
    to: step_3
    condition: "result.needs_review == true"    # optional
    label: Needs review                          # optional
```

Decision nodes define their edges inline via `config.edges` with `target` instead of `from`/`to`.

### Retry Policy

Any node can have a retry policy:

```yaml
- id: flaky_agent
  type: agent
  label: External Call
  config:
    agent: external_api
    output_key: api_result
  retry:
    max_attempts: 3
    backoff: exponential        # fixed | linear | exponential
    backoff_seconds: 2.0
    on_exhaustion: route        # fail | route
    fallback_edge: fallback     # required when on_exhaustion is route
```

---

## State Model

Each run accumulates state in `work_item_state`. When you create a run, you provide the initial work item:

```python
run = await app.start_run("my-process", {"task": "Review this document", "priority": 3})
```

As nodes execute, their outputs are stored at the node's `output_key`:

```python
# After an agent node with output_key="analysis" runs:
state = {
    "task": "Review this document",
    "priority": 3,
    "analysis": {"score": 0.85, "tags": ["technical"]},  # added by the agent
}
```

Downstream nodes and decision conditions access accumulated state. Condition expressions (in decision nodes and edges) use dot notation: `analysis.score > 0.8`.

---

## Run Lifecycle

```
pending → running → completed
                  → failed
                  → paused → running (after checkpoint resolution)
                           → failed
                           → cancelled
```

### Execution Modes

**Embedded (synchronous):** The orchestrator runs inside your application.

```python
async with Roots(storage=backend) as app:
    await app.load_process("process.yaml")
    await app.register_agent("my_agent", my_func)
    run = await app.start_run("process-id", {"key": "value"})
    await app.execute_run(run.id)   # blocks until done or paused
```

**Standalone (server):** Run the HTTP API server.

```bash
roots serve --host 0.0.0.0 --port 8200
```

Or programmatically:

```python
from roots.api.app import create_app

fastapi_app = create_app(roots_instance)
# Mount in your existing app or run with uvicorn
```

**Polling loop:** For background execution of multiple runs:

```python
orchestrator = app._orchestrator
await orchestrator.tick_all()    # advance all pending/running runs one step
await orchestrator.run_loop()    # continuous polling loop
```

### Handling Paused Runs

When a run pauses (checkpoint, escalation, or subprocess pause), resolve it:

```python
await app.resolve_checkpoint(
    run_id=run.id,
    decision="approve",      # approve | reject | redirect
    notes="Looks good",
    redirect_to="other_node",  # required for redirect
)
# Then continue execution:
await app.execute_run(run.id)
```

---

## Decision Modes

Decision nodes route execution based on state. The four modes control the autonomy spectrum:

| Mode | Behavior |
|------|----------|
| `deterministic` | Evaluate condition expressions. First true condition wins. |
| `ai_bounded` | AI model selects a path. If confidence < threshold, escalates to human. |
| `ai_checkpoint` | AI model selects a path. Always escalates for human confirmation. |
| `ai_autonomous` | AI model selects a path. Only escalates if confidence < threshold. |

For AI modes, Roots calls the configured LLM with the current state and edge descriptions. The AI returns a selection with a confidence score. If the score is below `confidence_threshold`, the run pauses for human review.

### LLM Configuration

```python
# Use built-in OpenAI-compatible client
app = Roots(
    storage=backend,
    default_model="gpt-4o",
    llm_config=LLMConfig(
        base_url="https://api.openai.com/v1",  # or Ollama, Together, etc.
        api_key="sk-...",                        # or set OPENAI_API_KEY env var
    ),
)

# Or bring your own LLM callable
async def my_llm(model, messages, tools=None, tool_choice=None):
    # call any LLM provider
    return LLMResponse(content="...", tool_calls=[], raw={})

app = Roots(storage=backend, llm_callable=my_llm)
```

The built-in client works with any OpenAI-compatible API: OpenAI, Ollama, Together, Groq, vLLM, LM Studio.

Environment variables: `OPENAI_API_KEY`, `ROOTS_LLM_API_KEY`, `ROOTS_LLM_BASE_URL`.

---

## Storage Backends

```python
from roots import SqliteBackend, PostgresBackend

# SQLite (good for development, single-instance)
backend = SqliteBackend("roots.db")      # file-based
backend = SqliteBackend(":memory:")       # in-memory

# PostgreSQL (good for production, multi-instance)
backend = PostgresBackend("postgresql://user:pass@host:5432/db")

# Always initialize before use
await backend.initialize()
```

Both backends implement the same `StorageBackend` interface. You can also implement your own by subclassing `StorageBackend` from `roots.storage.base`.

---

## Events

Roots emits 22 event types as side effects of orchestration. Events are informational — orchestrator state is never derived from them. Storage is always the source of truth.

### Event Types

Run lifecycle: `RUN_STARTED`, `RUN_COMPLETED`, `RUN_FAILED`, `RUN_PAUSED`, `RUN_ESCALATED`

Node lifecycle: `NODE_ENTERED`, `NODE_COMPLETED`, `NODE_FAILED`, `NODE_RETRYING`

Agent lifecycle: `AGENT_INVOKED`, `AGENT_RETURNED`, `AGENT_FAILED`

Decision lifecycle: `DECISION_EVALUATED`, `DECISION_TAKEN`, `DECISION_ESCALATED`

Checkpoint lifecycle: `CHECKPOINT_REACHED`, `CHECKPOINT_RESOLVED`

Escalation lifecycle: `ESCALATION_TRIGGERED`, `ESCALATION_RESOLVED`

Subprocess lifecycle: `SUBPROCESS_STARTED`, `SUBPROCESS_COMPLETED`, `SUBPROCESS_FAILED`

### Event Sinks

```python
from roots import StdoutSink, FileSink, HttpSink

app = Roots(
    storage=backend,
    event_sinks=[
        StdoutSink(),                        # print to console
        FileSink("events.jsonl"),             # append to file
        HttpSink("https://my-api/events"),    # POST to URL
    ],
)
```

Events are fire-and-forget with a bounded in-memory buffer (default 100). Slow sinks don't block orchestration.

---

## Headless UI

Roots is headless — it exposes process and run state as JSON for any frontend to consume.

```python
graph = await app.get_run_graph(run.id)
```

Returns:

```json
{
    "process_id": "my-process",
    "run_id": "run-123",
    "run_status": "running",
    "nodes": [
        {
            "id": "step_1",
            "type": "agent",
            "label": "Step 1",
            "status": "completed",
            "started_at": "2026-05-25T10:00:00",
            "completed_at": "2026-05-25T10:00:05",
            "position": {"x": 100, "y": 50},
            "metadata": {}
        }
    ],
    "edges": [
        {
            "id": "edge-1",
            "from": "step_1",
            "to": "step_2",
            "condition": null,
            "status": "traversed",
            "label": null
        }
    ]
}
```

Node positions default to `{x: 0, y: 0}`. Set them via `metadata.position` in the YAML or through the graph mutation API.

---

## HTTP API

When running as a server (`roots serve` or `create_app()`), the full REST API is available:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Health check |
| `/processes` | GET | List processes |
| `/processes` | POST | Create process from YAML |
| `/processes/{id}` | GET | Get process definition |
| `/processes/{id}` | DELETE | Delete process |
| `/processes/{id}/versions` | GET | List version history |
| `/processes/{id}/versions/{v}` | GET | Get specific version |
| `/runs` | GET | List runs (filterable by process/status) |
| `/runs` | POST | Create and start a run |
| `/runs/{id}` | GET | Get run status and state |
| `/runs/{id}/graph` | GET | Get headless graph JSON |
| `/runs/{id}/children` | GET | Get subprocess child runs |
| `/runs/{id}/checkpoint` | POST | Resolve checkpoint/escalation |
| `/agents` | GET | List registered agents |
| `/decisions` | GET | Query decision history |
| `/webhooks` | GET/POST/DELETE | Manage webhook subscriptions |
| `/graph/{id}/nodes/{nid}/position` | PUT | Update node position |

---

## Packaging (.root Archives)

Roots processes can be packaged as portable `.root` archives for distribution:

```bash
# Pack a process
roots pack process.yaml --output my-workflow.root

# Install a package
roots install my-workflow.root
```

Programmatically:

```python
# Pack
app.pack_process("process.yaml", output_path="my-workflow.root")

# Install
report = await app.install_package("my-workflow.root")
print(report.ready)            # True if all required agents are registered
print(report.missing)          # agents that need to be provided
print(report.satisfied)        # agents that are already registered
```

Packages include agent contracts (expected agent names, schemas) so the installer knows what agents to provide.

---

## CLI Reference

```bash
roots serve [--host HOST] [--port PORT] [--reload]  # Start API server
roots validate <process.yaml>                        # Validate a process YAML
roots load <process.yaml> [--storage PATH]           # Load process into storage
roots agents list                                    # List registered agents
roots agents get <name>                              # Get agent details
roots pack <process.yaml> [--output FILE]            # Create .root package
roots install <file.root> [--storage PATH] [--force] # Install .root package
```

---

## Common Patterns

### Process with AI Decision

```yaml
id: triage
name: Incident Triage
version: "1.0.0"
entry_point: ingest

nodes:
  - id: ingest
    type: agent
    label: Ingest
    config:
      agent: ingest_agent
      output_key: incident

  - id: classify
    type: decision
    label: Classify Severity
    config:
      mode: ai_bounded
      confidence_threshold: 0.75
      edges:
        - target: auto_resolve
          label: Low severity
          description: "Routine issue, auto-resolve"
        - target: escalate
          label: High severity
          description: "Critical issue, needs human attention"

  - id: auto_resolve
    type: agent
    label: Auto Resolve
    config:
      agent: resolver
      output_key: resolution

  - id: escalate
    type: checkpoint
    label: Human Review
    config:
      prompt: "AI classified this as high severity. Review and decide."

  - id: done
    type: end
    label: Done
    config:
      status: completed

edges:
  - from: ingest
    to: classify
  - from: auto_resolve
    to: done
  - from: escalate
    to: done
```

### Parallel Review Panel with Voting

```yaml
- id: review_panel
  type: agent_pool
  label: Expert Review
  config:
    agents: [expert_1, expert_2, expert_3]
    execution_mode: parallel
    aggregation: majority_vote
    output_key: panel_verdict
    vote_config:
      vote_key: recommendation
      threshold: 0.67
      tie_break: reject
```

### Subprocess Composition

```yaml
# Parent process references a child process
- id: validation
  type: subprocess
  label: Run Validation Pipeline
  config:
    process_id: validation-pipeline
    input_mapping:
      document: raw_content       # parent's raw_content → child's document
    output_mapping:
      validation_result: report   # child's report → parent's validation_result
    output_key: validation_output
    max_depth: 3
```

### Retry with Fallback

```yaml
- id: external_call
  type: agent
  label: Call External API
  config:
    agent: api_caller
    output_key: api_response
  retry:
    max_attempts: 3
    backoff: exponential
    backoff_seconds: 2.0
    on_exhaustion: route
    fallback_edge: use_cached
```

---

## Key Serialization Rule

All Pydantic models in Roots use `model_dump(by_alias=True, mode="json")` for serialization. This is critical because `EdgeDefinition` aliases `from_node` to `from` (a Python reserved word). Forgetting `by_alias=True` produces invalid data. If you're working with Roots models programmatically, always use this convention.

---

## Expression Syntax

Decision conditions and edge conditions use `simpleeval` for safe evaluation. Available syntax:

- Comparisons: `score > 0.8`, `status == "approved"`
- Boolean logic: `a > 1 and b < 5`, `not is_spam`
- Arithmetic: `total * 0.1 + bonus`
- Dot notation for nested state: `analysis.score`, `review.tags`
- Array indexing: `results.0.name` (translates to `results[0].name`)

**Not allowed:** Function calls, imports, arbitrary Python. This is intentional — process definitions may come from untrusted sources.

---

## What Not to Do

- **Don't create nodes for code execution.** A node that calls a function, makes an HTTP request, or runs a shell command is wrong. Wrap that logic in an agent.
- **Don't derive state from events.** Events are side effects. The storage backend is the source of truth.
- **Don't use `eval()` or `exec()`.** Roots uses `simpleeval` for safe expression evaluation. Never bypass this.
- **Don't forget `by_alias=True`.** When serializing Roots models, always use `model_dump(by_alias=True, mode="json")`.
- **Don't use `utcnow()`.** Use `datetime.now(datetime.UTC)` for all datetime operations.
- **Don't rely on fork/join for critical workflows.** Fork/join is not crash-safe in the current version.
