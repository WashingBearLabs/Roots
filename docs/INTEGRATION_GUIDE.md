# Roots Integration Guide

> For AI coding agents and developers integrating Roots into applications.
>
> Last updated: 2026-06-02

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
| `start_run(process_id, work_item, metadata=None)` | Create a new run with optional metadata (returns `RunRecord`) |
| `execute_run(run_id)` | Run to completion (blocks until done or paused) |
| `get_run(run_id)` | Fetch run status and state |
| `get_run_graph(run_id)` | Get headless graph JSON for visualization |
| `resolve_checkpoint(run_id, decision)` | Resume a paused run |
| `on(event_type, callback, run_id=None)` | Register event callback (returns subscription_id) |
| `once(event_type, callback, run_id=None)` | One-shot event callback |
| `off(subscription_id)` | Remove event subscription |
| `wait_for(event_type, run_id=None, timeout=...)` | Await a specific event (asyncio-friendly) |
| `start_and_wait(process_id, work_item, timeout=...)` | Start a run and wait for completion (race-free) |
| `close()` | Drain events, cancel pending futures, release resources |

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

# With orchestration context (agent can start child runs, resolve checkpoints)
await app.register_agent("sequencer", sequencer_func, needs_context=True)

# MCP server (auto-discovers tools)
agent_names = await app.register_mcp_server(url="http://localhost:3000/mcp")
# or
agent_names = await app.register_mcp_server(command=["npx", "my-mcp-server"])
```

### Agent Context

Agents registered with `needs_context=True` receive an `AgentContext` object at `input["_roots_context"]` that provides controlled access to orchestration operations:

```python
async def sequencer_agent(input: dict) -> dict:
    ctx = input["_roots_context"]  # AgentContext instance

    # Start a child run
    child_run = await ctx.start_run("child-process", {"data": input["work_item_state"]["items"][0]})

    # Execute and wait for completion (returns RunRecord)
    result = await ctx.execute_run(child_run.id)

    # Check status
    if result.status == "paused":
        await ctx.resolve_checkpoint(child_run.id, "approve")
        result = await ctx.execute_run(child_run.id)

    return {"output": {"child_status": result.status}, "escalate": False}
```

Context methods: `start_run`, `get_run`, `execute_run`, `resolve_checkpoint`. Context is in-process only — not available for remote HTTP or MCP agents. Nesting depth is tracked and enforced (default max 5).

---

## Process Definitions (YAML)

A process is a directed graph of nodes connected by edges. Every process needs:
- `id`, `name`, `version` — identity
- `entry_point` — which node starts execution
- `nodes` — the steps
- `edges` — the connections

### The 10 Node Types

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

Fork/join is crash-safe — completed branches are checkpointed to storage and survive orchestrator crashes. On restart, only incomplete branches are re-executed.

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

#### `iterator` — Dynamic Fan-Out
```yaml
- id: process_stories
  type: iterator
  label: Implement Stories
  config:
    items_key: stories              # key in work_item_state containing the list
    process_id: execute-story       # subprocess to run per item
    execution_mode: sequential      # sequential | parallel
    item_key: story                 # each item placed here in child work_item
    input_mapping:                  # additional parent state passed to every child
      epic_context: epic_context
    output_key: story_results       # results collected as ordered list
    on_item_failure: continue       # continue | stop | stop_after_n
    max_failures: 3                 # only for stop_after_n
    max_concurrency: 5              # only for parallel mode (caps concurrent children)
    max_depth: 5                    # recursion limit (1-20)
```

The iterator reads a list from `work_item_state` at runtime and runs a subprocess for each item. Results are collected as an ordered list of uniform envelopes: `{"_item_index": 0, "_status": "completed", "_item_value": ..., "output": {...}}`.

Sequential mode processes items one at a time with crash recovery (completed items survive restarts). Parallel mode uses `asyncio.create_task` with optional concurrency limiting via `max_concurrency`.

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

# With metadata for tagging and filtering
run = await app.start_run(
    "my-process",
    {"task": "Review this document"},
    metadata={"epic_id": "abc", "triggered_by": "user-123"},
)
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

Roots emits 27 event types as side effects of orchestration. Events are informational — orchestrator state is never derived from them. Storage is always the source of truth.

### Event Types

Run lifecycle: `RUN_STARTED`, `RUN_COMPLETED`, `RUN_FAILED`, `RUN_PAUSED`, `RUN_ESCALATED`

Node lifecycle: `NODE_ENTERED`, `NODE_COMPLETED`, `NODE_FAILED`, `NODE_RETRYING`

Agent lifecycle: `AGENT_INVOKED`, `AGENT_RETURNED`, `AGENT_FAILED`

Decision lifecycle: `DECISION_EVALUATED`, `DECISION_TAKEN`, `DECISION_ESCALATED`

Checkpoint lifecycle: `CHECKPOINT_REACHED`, `CHECKPOINT_RESOLVED`

Escalation lifecycle: `ESCALATION_TRIGGERED`, `ESCALATION_RESOLVED`

Subprocess lifecycle: `SUBPROCESS_STARTED`, `SUBPROCESS_COMPLETED`, `SUBPROCESS_FAILED`

Iterator lifecycle: `ITERATOR_STARTED`, `ITERATOR_ITEM_COMPLETED`, `ITERATOR_ITEM_FAILED`, `ITERATOR_COMPLETED`, `ITERATOR_FAILED`

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

### Event Subscriptions

For in-process applications that need to react to events without polling:

```python
from roots.events.types import EventType

# Register a persistent callback
sub_id = app.on(EventType.RUN_COMPLETED, my_callback, run_id="run-123")

# One-shot callback (auto-removed after first fire)
sub_id = app.once(EventType.NODE_FAILED, my_error_handler)

# Wait for an event (asyncio-friendly, required timeout)
event = await app.wait_for(EventType.RUN_COMPLETED, run_id=run.id, timeout=300)

# Start a run and wait for completion in one call (race-free)
run, event = await app.start_and_wait("my-process", {"key": "value"}, timeout=600)

# Multi-event matching (list of types)
event = await app.wait_for(
    [EventType.RUN_COMPLETED, EventType.RUN_FAILED],
    run_id=run.id,
    timeout=300,
)

# Unsubscribe
app.off(sub_id)
```

Callbacks are async callables receiving an `EventEnvelope`. Exceptions in callbacks are caught and logged — they never break orchestration. Subscription dispatch uses a separate bounded buffer from sinks.

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
| `/runs` | GET | List runs (filterable by process/status/metadata) |
| `/runs` | POST | Create and start a run (with optional metadata) |
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

## Run Metadata

Attach application-specific metadata to runs at creation time for tagging, grouping, and filtering:

```python
# Create a run with metadata
run = await app.start_run(
    "my-process",
    {"task": "Process document"},
    metadata={"project_id": "proj-123", "user": "alice", "priority": 5},
)

# Filter runs by metadata
runs = await app.storage.list_runs(
    metadata_filter={"project_id": "proj-123"}  # shorthand for $eq
)

# Using operators
runs = await app.storage.list_runs(
    metadata_filter={
        "project_id": {"$eq": "proj-123"},      # exact match
        "user": {"$in": ["alice", "bob"]},       # set membership
        "debug": {"$exists": True},              # key presence
    }
)
```

Via the REST API, pass `metadata_filter` as a JSON-encoded query parameter: `GET /runs?metadata_filter={"project_id":"proj-123"}`.

Metadata values must be JSON scalars (str, int, float, bool, None). Keys must match `^[a-zA-Z_][a-zA-Z0-9_]*$`. Metadata is immutable after creation.

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

### Dynamic Fan-Out with Iterator

```yaml
# Process each item in a list — N determined at runtime
- id: process_items
  type: iterator
  label: Process All Items
  config:
    items_key: documents           # list in work_item_state
    process_id: analyze-document   # subprocess per item
    execution_mode: parallel
    item_key: document
    output_key: analysis_results
    on_item_failure: continue      # don't stop on individual failures
    max_concurrency: 10            # limit parallel children
```

### Start Run and Wait (Event-Driven)

```python
# Race-free: registers subscription before starting the run
run, event = await app.start_and_wait(
    "my-process",
    {"task": "analyze"},
    timeout=300,
)
if event.event == "roots.run.completed":
    print("Success:", run.work_item_state)
else:
    print("Failed:", event.metadata)
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
- **Don't set `_subprocess_depth` or `_roots_context` in work_item_state.** These are reserved keys used by the orchestrator for depth tracking and agent context injection.
