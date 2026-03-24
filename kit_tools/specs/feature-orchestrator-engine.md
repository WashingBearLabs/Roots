<!-- Template Version: 2.0.0 -->
---
feature: orchestrator-engine
status: active
session_ready: true
depends_on: [process-schema, storage-backend, decision-engine, agent-registry, event-system]
vision_ref: "T1.3 — Orchestrator Engine"
type: epic-child
epic: roots-v1
epic_seq: 6
epic_final: false
created: 2026-03-23
updated: 2026-03-23
---

# Feature Spec: Orchestrator Engine

## Overview

The orchestrator is the heart of Roots. It is stateless between ticks — each tick loads run state from storage, executes one node, writes updated state back, and yields. The ProcessRunner class owns a single run's execution loop. The Orchestrator class manages multiple runners. This spec also includes the public embedded API (`Roots` class) and two example YAML process files that serve as end-to-end smoke tests.

## Goals

- Implement tick-based execution that is crash-safe and horizontally scalable
- Dispatch node execution to the appropriate handler (decision engine, agent invoker, etc.)
- Provide the clean embedded API described in the architecture doc
- Deliver working example processes that prove the framework works end-to-end

## User Stories

### US-001: Run State Machine

**Description:** As a framework developer, I want a run lifecycle state machine so that run status transitions are validated and invalid transitions are rejected.

**Implementation Hints:**
- Create `roots/core/state_machine.py`
- Define `RunStatus` as `StrEnum`: `pending`, `running`, `paused`, `completed`, `failed`, `cancelled`
- Valid transitions dict:
  - `pending` → `running`, `cancelled`
  - `running` → `paused`, `completed`, `failed`, `cancelled`
  - `paused` → `running`, `cancelled`
  - `completed` → (terminal)
  - `failed` → (terminal)
  - `cancelled` → (terminal)
- `can_transition(current: RunStatus, target: RunStatus) -> bool`
- `transition(current: RunStatus, target: RunStatus) -> RunStatus` — raises `InvalidTransitionError` if invalid
- `InvalidTransitionError(Exception)`: includes `current`, `target`, `valid_targets: list[RunStatus]`

**Acceptance Criteria:**
- [x] All valid transitions accepted
- [x] Invalid transitions raise InvalidTransitionError with valid targets listed
- [x] Terminal states have no valid transitions
- [x] Tests cover every valid transition and key invalid ones (e.g., completed→running)

### US-002: ProcessRunner — Tick-Based Execution Loop

**Description:** As a framework developer, I want a ProcessRunner that executes one tick per call — load state, execute node, write state — so that execution is crash-safe.

**Implementation Hints:**
- Create `roots/core/orchestrator.py` with `ProcessRunner` class
- Constructor: `run_id: str`, `storage: StorageBackend`, `agent_invoker: AgentInvoker`, `decision_engine: DecisionEngine`, `event_emitter: EventEmitter`, `owner_id: str`
- Method `async tick() -> bool` (returns True if run should continue):
  1. `try:` acquire run lock. If fails → return False.
  2. Load run from storage. If status not `running` → release lock, return False.
  3. Load process definition from storage.
  4. Get current node via `process.get_node(run.current_node_id)`.
  5. Emit `roots.node.entered` — construct event: `create_event(EventType.NODE_ENTERED, run_id=self.run_id, process_id=run.process_id, node_id=node.id, node_type=node.type.value)`. Also append to history: `storage.append_history_event(run_id, "entered", node.id, {})`.
  6. Call the appropriate handler method (US-003/US-004) → get output dict (or None for non-output nodes).
  7. If output and node has `output_key`: write to state via `state[output_key] = output` (the full output dict goes under the key).
  8. Determine next node (US-005 edge evaluation).
  9. Persist atomically: `storage.update_run_atomically(run_id, work_item_state=state, status=status, current_node_id=next_node_id)`. Then append history: `storage.append_history_event(run_id, "completed", node.id, output or {})`. The atomic update ensures state+status+position are always consistent — if crash happens between atomic write and history append, the run state is still correct (history is supplementary).
  10. Emit `roots.node.completed` — include `duration_ms` (measured from step 5 to here).
  11. `finally:` release run lock.
- **Pending → Running transition:** The FIRST tick of a new run transitions it from `pending` to `running` before executing. Check `if run.status == RunStatus.pending: storage.update_run_status(run_id, "running", run.current_node_id or process.entry_point)`.
- Method `async run_to_completion()`: loop `while await self.tick(): pass`.

**Acceptance Criteria:**
- [x] Tick acquires lock, executes one node, releases lock
- [x] Lock always released via try/finally (even on exception)
- [x] Run state loaded fresh from storage each tick (no in-memory caching)
- [x] First tick transitions pending → running and sets current_node to entry_point
- [x] Events emitted with correct fields (run_id, process_id, node_id, node_type, duration_ms)
- [x] History events use lifecycle status strings ("entered", "completed", "failed"), NOT node type strings
- [x] `run_to_completion` loops until done
- [x] Tests with SQLite in-memory + mock agents verify tick behavior

### US-003: Agent and Agent Pool Handlers

**Description:** As a framework developer, I want handlers for agent and agent_pool nodes so that the orchestrator can invoke registered agents.

**Implementation Hints:**
- In ProcessRunner, add private handler methods:
- `async _handle_agent(node: NodeDefinition, state: dict) -> dict`:
  - Build `AgentInput(work_item_state=state, node_config=node.config.model_dump(), run_id=self.run_id)`
  - Emit `roots.agent.invoked` event with agent name
  - Call `self.agent_invoker.invoke(node.config.agent, agent_input)`
  - Emit `roots.agent.returned` event
  - Check `output.escalate` → if True, trigger escalation (pause run, create escalation record with trigger_type=`agent_explicit_signal`, reason=output.escalation_reason)
  - Return `output.output` dict
- `async _handle_agent_pool(node: NodeDefinition, state: dict) -> dict`:
  - Get agent list from `node.config.agents`
  - **parallel mode:** `results = await asyncio.gather(*[self.agent_invoker.invoke(name, input) for name in agents], return_exceptions=True)`. Filter out exceptions (log them). Merge successful outputs: `merged = {}; for r in successful: merged.update(r.output)`. **If ALL agents failed:** raise `OrchestrationError(f"All {len(agents)} agents failed in pool '{node.id}'")` — do NOT silently produce an empty dict, as this would cause confusing downstream failures.
  - **sequential mode:** Loop agents in order. Start with current state. After each: merge output into state copy, pass merged state to next agent. Return final output.
  - **first_pass mode:** Loop agents. "Successful" means: invocation did not throw an exception AND `output.escalate` is not True. Return the first successful output. If all fail (all threw or all escalated), raise `OrchestrationError` with the last error.
  - Check any output for `escalate` signal (same as single agent)
  - Return the aggregated/final output dict

**Acceptance Criteria:**
- [x] Single agent handler invokes agent and returns output
- [x] Agent pool parallel mode invokes all concurrently and merges results
- [x] Agent pool sequential mode chains outputs
- [x] Agent pool first_pass mode returns first success
- [x] Explicit escalation signal (`escalate: true`) triggers escalation
- [x] Agent events emitted (invoked, returned)
- [x] Tests cover each execution mode with mock agents

### US-004: Decision, Checkpoint, Emit, and End Handlers

**Description:** As a framework developer, I want handlers for decision, checkpoint, emit, and end nodes so that the orchestrator can handle the full node type vocabulary.

**Implementation Hints:**
- `async _handle_decision(node, state) -> str`:
  - Call `self.decision_engine.evaluate(node, state)` → `DecisionResult`
  - Record decision to storage: `storage.append_decision(...)` using `result.to_decision_record()`
  - If `result.escalated`:
    - Create checkpoint record: `storage.create_checkpoint(run_id, node.id, "escalation", node.config.checkpoint_prompt or "AI decision requires review", result.ai_recommendation.model_dump() if result.ai_recommendation else None)`
    - Set run to paused: `storage.update_run_status(run_id, "paused")`
    - Emit `roots.decision.escalated` and `roots.checkpoint.reached` events
    - Return None (no next node — run is paused)
  - Else: emit `roots.decision.taken`, return `result.selected_edge` (the target node ID)
- `async _handle_checkpoint(node, state)`:
  - Create planned checkpoint: `storage.create_checkpoint(run_id, node.id, "planned", node.config.prompt)`
  - Set run to paused
  - Emit `roots.checkpoint.reached`
  - Return None (paused)
- `async _handle_emit(node, state)`:
  - Build metadata from `node.config.payload_keys`: extract listed keys from work_item_state
  - Emit custom event: `create_event(node.config.event_type, run_id, process_id, node_id=node.id, metadata=payload_metadata)`
  - Return None (emit nodes produce no output)
- `async _handle_end(node, state)`:
  - Set run status to `node.config.status` (completed or failed)
  - Emit `roots.run.completed` or `roots.run.failed` accordingly
  - Return None
- **Dispatch dict:** `_handlers = {NodeType.agent: _handle_agent, NodeType.agent_pool: _handle_agent_pool, NodeType.decision: _handle_decision, ...}`
- Fork/join: `_handlers[NodeType.fork] = _handle_fork` → raises `NotImplementedError("Fork/join execution in T2.2")`

**Acceptance Criteria:**
- [x] Decision handler calls engine, records result, handles escalation
- [x] Checkpoint handler creates record and pauses run
- [x] Emit handler fires custom event with payload from state
- [x] End handler sets terminal status and emits appropriate event
- [x] Dispatch dict routes all node types to handlers
- [x] Fork/join stubs raise NotImplementedError
- [x] Tests cover each handler independently

### US-005: Edge Evaluation and State Accumulation

**Description:** As a framework developer, I want edge evaluation and state accumulation so that the orchestrator advances correctly and work item state grows as nodes execute.

**Implementation Hints:**
- After handler returns, determine next node:
  - If handler returned a node ID string (decision): use it directly
  - If handler returned None (checkpoint, end, escalation): run is paused/done, no advancement
  - For all other nodes: call `process.get_outbound_edges(node.id)` → get list of EdgeDefinition. Take the first edge's `to_node` as the next node. If no edges: raise `OrchestrationError(f"Node '{node.id}' has no outbound edges")`
- **State accumulation:** After node handler returns output dict:
  - Get `output_key` from node config (available on AgentNodeConfig and AgentPoolNodeConfig)
  - Write: `work_item_state[output_key] = output_dict` — the entire output dict stored under the key
  - Example: if output is `{"findings": [...], "score": 85}` and output_key is `"validation"`, state becomes `{..., "validation": {"findings": [...], "score": 85}}`
  - Nodes without output_key (decision, checkpoint, emit, end) skip this step
- Define `OrchestrationError(Exception)` in `roots/core/orchestrator.py`

**Acceptance Criteria:**
- [x] Decision node next node comes from handler return value
- [x] Non-decision nodes advance via first outbound edge
- [x] Missing outbound edge raises OrchestrationError
- [x] Output written to `state[output_key]` as full dict
- [x] State accumulates across multiple nodes (each gets its own key)
- [x] Nodes without output_key don't modify state
- [x] Tests verify 3-node process with state accumulation: node1 writes to key1, node2 reads key1 from state

### US-006: Orchestrator Class

**Description:** As a framework developer, I want an Orchestrator class that manages multiple ProcessRunners for concurrent run handling.

**Implementation Hints:**
- Create `Orchestrator` class in `roots/core/orchestrator.py`
- Constructor: `storage`, `agent_registry`, `decision_engine`, `event_emitter`, `poll_interval: float = 1.0`
- `self.owner_id = f"orchestrator-{uuid4()}"` — generated once, used for all locks
- `async start_run(process_id, work_item) -> RunRecord`:
  - Verify process exists in storage
  - `storage.create_run(process_id, work_item)` — returns RunRecord with pending status
  - Set current_node_id to the process's entry_point in the run record
  - Return the run record
- `async tick_all()`:
  - List runs with status `running` OR `pending`
  - For each: create a ProcessRunner, call tick(). Use `asyncio.gather` for concurrent tick execution.
- `async run_loop()`: `while True: await self.tick_all(); await asyncio.sleep(self.poll_interval)` — for standalone server. Catch `asyncio.CancelledError` for graceful shutdown.
- `async execute_run(run_id)`: create ProcessRunner, call `run_to_completion()`. Synchronous run-to-done for embedded mode.

**Acceptance Criteria:**
- [ ] `start_run` creates run with pending status
- [ ] `tick_all` processes all pending and running runs
- [ ] `execute_run` runs a single run to completion
- [ ] `run_loop` polls continuously and handles cancellation
- [ ] owner_id is unique per Orchestrator instance
- [ ] Tests verify start → execute → completed flow

### US-007: Roots Embedded API — Core Methods

**Description:** As a framework consumer, I want a clean `Roots` class with core methods so that I can use the framework with minimal boilerplate.

**Implementation Hints:**
- Create `roots/__init__.py` with `Roots` class
- Constructor: `storage: StorageBackend`, `event_sinks: list[EventSink] = []`, `default_model: str = "openai/gpt-4o-mini"` (neutral default — any LiteLLM model string works: `"claude-sonnet-4-20250514"`, `"gpt-4o"`, `"ollama/llama3"`, etc.)
- Internally creates and wires:
  - `self._agent_registry = AgentRegistry()`
  - `self._agent_invoker = AgentInvoker(self._agent_registry)`
  - `self._decision_engine = DecisionEngine(default_model=default_model)`
  - `self._event_emitter = EventEmitter(sinks=event_sinks)`
  - `self._orchestrator = Orchestrator(storage, self._agent_registry, self._decision_engine, self._event_emitter)`
  - `self.storage = storage`
- Public methods:
  - `async load_process(path: str)`: parse YAML via `load_process_yaml(path)`, save to storage
  - `async register_agent(name, callable, input_schema=None, output_schema=None)`: convenience for `_agent_registry.register_local(name, callable, ...)`
  - `async start_run(process_id, work_item) -> RunRecord`: delegate to orchestrator
  - `async execute_run(run_id)`: delegate to orchestrator
  - `async get_run(run_id) -> RunRecord | None`: delegate to storage
- Add `async close()` method: calls `self._event_emitter.close()` and `await self.storage.close()`. Consumers should call this on shutdown to drain pending events and release connections. Support async context manager (`__aenter__`/`__aexit__`) for clean `async with Roots(...) as roots:` usage.
- Re-export from `roots/__init__.py`: `Roots`, `SqliteBackend`, `PostgresBackend`, `StdoutSink`, `FileSink`, `HttpSink`
- **Update `tests/conftest.py`:** Replace the placeholder comment from T1.2 with the actual `roots_instance` fixture now that the Roots class exists. The fixture creates `Roots(storage=sqlite_storage)`, registers a simple echo agent (`async def echo_agent(input): return {"echo": input["work_item_state"]}`), yields the instance, and calls `close()` in teardown.

**Acceptance Criteria:**
- [ ] `Roots(storage=SqliteBackend(":memory:"))` instantiates cleanly
- [ ] `load_process` parses and stores a YAML process
- [ ] `register_agent` registers a local callable
- [ ] `start_run` + `execute_run` drives a run to completion
- [ ] All public types importable from `roots` package
- [ ] `close()` drains events and closes storage
- [ ] Works as async context manager
- [ ] `roots_instance` fixture added to conftest.py (replacing T1.2 placeholder)
- [ ] End-to-end test: 5-line script that loads process, registers agent, runs to completion

### US-008: Roots Embedded API — Graph and Resolution

**Description:** As a framework consumer, I want graph data and checkpoint resolution methods so that I can inspect run state and resolve human-in-the-loop decisions.

**Implementation Hints:**
- Add to `Roots` class:
- `async get_run_graph(run_id) -> dict`: Build the headless graph JSON structure from architecture doc Section 7:
  ```python
  {
      "process_id": "...",
      "run_id": "...",
      "run_status": "running",
      "nodes": [
          {
              "id": "planning",
              "type": "agent_pool",
              "label": "Planning Phase",
              "status": "completed",  # derive from history
              "started_at": "...",    # from history events
              "completed_at": "...",  # from history events
              "position": {"x": 0, "y": 0},  # from node metadata
              "metadata": {}
          }
      ],
      "edges": [
          {
              "id": "edge-123",
              "from": "planning",
              "to": "validation",
              "condition": null,
              "status": "traversed",  # derive from history
              "label": null
          }
      ]
  }
  ```
  - Load process + run in 2 queries. Load history events in a 3rd query (still efficient — no N+1).
  - **Node status derivation** from history events (which store lifecycle status strings "entered"/"completed"/"failed"):
    - `"completed"`: history contains an event with `(node_id, "completed")`
    - `"running"`: `run.current_node_id == node.id` AND run status is `running`
    - `"failed"`: history contains `(node_id, "failed")`
    - `"paused"`: `run.current_node_id == node.id` AND run status is `paused`
    - `"pending"`: no history events for this node
    - `"skipped"`: no history events AND node is past the current position (downstream of a decision that took a different branch)
  - Edge status: `"traversed"` if history has events for both the `from` and `to` node, `"pending"` otherwise.
  - **Position convention:** `node.metadata.get("position", {"x": 0, "y": 0})`. Positions start at `{x:0, y:0}` for all nodes until the visual editor writes them via the mutation API. Document this default.
- `async resolve_checkpoint(run_id, decision, notes=None, redirect_to=None)`:
  - Delegate to the resolution logic (from T2.1). For now, implement inline:
  - Get pending checkpoint or escalation from storage
  - If `decision == "approve"`: resolve record, set run to running, set current_node to next node (first outbound edge of checkpoint node, or for escalations: the recommended edge from AI recommendation if available)
  - If `decision == "reject"`: resolve record, set run to failed
  - If `decision == "redirect"`: validate redirect_to is a valid node, resolve record, set run to running with current_node = redirect_to
  - Emit appropriate resolved event

**Acceptance Criteria:**
- [ ] `get_run_graph` returns correct JSON structure matching architecture doc
- [ ] Node statuses derived correctly from execution history
- [ ] Edge statuses derived from traversal history
- [ ] Graph loads in max 2 storage queries
- [ ] `resolve_checkpoint` handles approve/reject/redirect
- [ ] Approve resumes run, reject fails run, redirect redirects
- [ ] Tests verify graph structure for a partially-completed run

### US-009: Example Process YAML Files

**Description:** As a framework user, I want working example process definitions that demonstrate the framework's capabilities and serve as smoke tests.

**Implementation Hints:**
- Create `examples/processes/simple-linear.yaml`:
  ```yaml
  id: simple-linear
  name: Simple Linear Process
  version: "1.0.0"
  description: Minimal two-node sequential process
  nodes:
    - id: step1
      type: agent
      label: First Step
      config:
        agent: echo_agent
        output_key: step1_output
    - id: step2
      type: agent
      label: Second Step
      config:
        agent: echo_agent
        output_key: step2_output
    - id: done
      type: end
      label: Complete
      config:
        status: completed
  edges:
    - from: step1
      to: step2
    - from: step2
      to: done
  entry_point: step1
  ```
- Create `examples/processes/parallel-validation.yaml`:
  ```yaml
  id: parallel-validation
  name: Parallel Validation Process
  version: "1.0.0"
  description: Fork/join with parallel validators and decision gate
  nodes:
    - id: start
      type: checkpoint
      label: Ready to validate
      config:
        prompt: "Confirm the work item is ready for validation."
    - id: split
      type: fork
      label: Split for parallel validation
      config: {}
    - id: quality_check
      type: agent
      label: Quality Check
      config:
        agent: quality_checker
        output_key: quality_result
    - id: security_check
      type: agent
      label: Security Check
      config:
        agent: security_checker
        output_key: security_result
    - id: compliance_check
      type: agent
      label: Compliance Check
      config:
        agent: compliance_checker
        output_key: compliance_result
    - id: merge
      type: join
      label: Merge Results
      config:
        merge_strategy: collect
        collect_key: validation_results
    - id: gate
      type: decision
      label: Validation Gate
      config:
        mode: deterministic
        edges:
          - target: pass
            condition: "validation_results.0.state.quality_result.pass == true"
            label: All checks passed
          - target: fail
            condition: "validation_results.0.state.quality_result.pass == false"
            label: Checks failed
    - id: pass
      type: end
      label: Validation Passed
      config:
        status: completed
    - id: fail
      type: end
      label: Validation Failed
      config:
        status: failed
  edges:
    - from: start
      to: split
    - from: split
      to: quality_check
    - from: split
      to: security_check
    - from: split
      to: compliance_check
    - from: quality_check
      to: merge
    - from: security_check
      to: merge
    - from: compliance_check
      to: merge
    - from: merge
      to: gate
  entry_point: start
  ```
- Create `examples/run_simple.py`: end-to-end script using embedded API (load process, register echo agent, start run, execute, print result)
- Validate both YAML files parse correctly via `load_process_yaml`

**Acceptance Criteria:**
- [ ] `simple-linear.yaml` parses and validates successfully
- [ ] `parallel-validation.yaml` parses and validates successfully (including fork/join pairing)
- [ ] `run_simple.py` executes end-to-end when run directly
- [ ] Both files serve as documentation of the YAML format
- [ ] Tests load both files and verify parsing

## Out of Scope

- Fork/join execution (T2.2 — the YAML validates, but the parallel-validation example can't run until T2.2)
- Retry logic integration (T2.1)
- HTTP API (T2.3)

## Technical Considerations

- The orchestrator must NEVER hold run state in memory between ticks — always reload from storage
- Lock release in `finally` is critical — test exception paths
- `asyncio.gather` for agent pool parallel mode: use `return_exceptions=True` so one failure doesn't cancel all
- **Agent callable ergonomics (v2 consideration):** v1 passes full AgentInput dict to all callables. In v2, consider supporting both `def(dict)->dict` (receives just state, for simple agents) and `def(AgentInput)->AgentOutput` (full control). For v1, the uniform interface is simpler and avoids signature inspection magic.
- The pending→running transition in the first tick avoids a race condition where two orchestrators both try to start the same pending run
- `get_run_graph` performance: 2 queries max (process + run). Derive statuses in Python from history, not N+1 queries.

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
