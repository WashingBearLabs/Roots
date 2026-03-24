<!-- Template Version: 2.0.0 -->
---
feature: process-schema
status: active
session_ready: true
depends_on: []
vision_ref: "T1.1 — Process Schema Layer"
type: epic-child
epic: roots-v1
epic_seq: 1
epic_final: false
created: 2026-03-23
updated: 2026-03-23
---

# Feature Spec: Process Schema Layer

## Overview

The process schema layer is the foundation of Roots. It defines the Pydantic v2 models that represent process graphs — nodes, edges, and their configurations — and provides YAML parsing plus structural validation. Every other component in Roots depends on these models. This spec also includes project scaffolding (pyproject.toml, directory structure) since this is the first feature built.

## Goals

- Establish the complete Pydantic v2 model hierarchy for process graphs that serves as the authoritative schema
- Provide a YAML-to-model parsing pipeline with clear, actionable validation error messages
- Implement structural validation rules (fork/join pairing, edge reference integrity, decision edge rules)

## User Stories

### US-001: Project Scaffolding

**Description:** As a framework developer, I want the repository structure and packaging set up so that all subsequent work has a proper foundation.

**Implementation Hints:**
- Create `pyproject.toml` with project metadata, Python 3.12+ requirement, and dependencies: `pydantic>=2.0`, `pyyaml`, `fastapi`, `uvicorn[standard]`, `typer[all]`, `litellm`, `httpx`, `aiosqlite`, `asyncpg`, `sqlalchemy[asyncio]`, `simpleeval`, `jsonschema`
- Add dev dependencies: `pytest`, `pytest-asyncio`, `pyright`, `ruff`
- Create the full directory structure: `roots/core/`, `roots/agents/`, `roots/storage/`, `roots/events/`, `roots/api/routers/`, `roots/cli/`, `tests/`, `examples/processes/`
- Add `__init__.py` files in all packages (empty for now except `roots/__init__.py` which can have a version string)
- Configure pyright in `pyproject.toml`: `[tool.pyright]` with `typeCheckingMode = "strict"`
- Add `[project.scripts]` entry: `roots = "roots.cli.main:app"`
- Add basic `tests/conftest.py` with a comment placeholder for shared fixtures (will be populated in T1.2)

**Acceptance Criteria:**
- [x] `pyproject.toml` exists with all dependencies and Python 3.12+ constraint
- [x] Full directory structure matches the session prompt layout
- [x] `pip install -e .` succeeds
- [x] `pyright` runs (may have errors on empty files, but the config is correct)
- [x] `pytest` discovers the `tests/` directory
- [x] Typecheck/lint passes (on scaffolding only)

### US-002: Base Node Model and NodeType Enum

**Description:** As a framework developer, I want a base node model and node type enumeration so that all node types share a common structure and new types can be added by extending the enum.

**Implementation Hints:**
- Define `NodeType` as a `StrEnum` in `roots/core/schema.py`: `agent`, `agent_pool`, `decision`, `checkpoint`, `fork`, `join`, `emit`, `end`
- Define `RetryConfig` model: `max_attempts` (int, default 1), `backoff` (StrEnum: fixed/linear/exponential, default fixed), `backoff_seconds` (float, default 5.0), `on_exhaustion` (StrEnum: fail/route, default fail), `fallback_edge` (optional str, required if on_exhaustion is route)
- Define `NodeDefinition` model: `id` (str), `type` (NodeType), `label` (str), `config` (dict — will be typed in US-003/US-004), `metadata` (optional dict, default empty), `retry` (optional RetryConfig)
- Use Pydantic `model_validator(mode="after")` to enforce: retry is only valid on `agent` and `agent_pool` node types. Raise `ValueError("retry config is only valid on agent and agent_pool nodes")`.

**Acceptance Criteria:**
- [ ] `NodeType` enum has all 8 values
- [ ] `RetryConfig` validates all fields with correct defaults
- [ ] `RetryConfig` requires `fallback_edge` when `on_exhaustion` is `route`
- [ ] `NodeDefinition` accepts valid node definitions
- [ ] `NodeDefinition` rejects retry config on non-agent node types
- [ ] Tests in `tests/test_schema.py` cover all node types and retry validation edge cases

### US-003: Agent, AgentPool, and Decision Config Models

**Description:** As a framework developer, I want config models for agent, agent_pool, and decision node types so that these core executable nodes have validated configuration.

**Implementation Hints:**
- Create in `roots/core/schema.py`:
  - `AgentNodeConfig(BaseModel)`: `agent` (str), `output_key` (str)
  - `AgentPoolNodeConfig(BaseModel)`: `agents` (list[str], min_length=1), `execution_mode` (StrEnum: parallel/sequential/first_pass), `aggregation` (StrEnum: merge_all, default merge_all), `output_key` (str)
  - `DecisionEdge(BaseModel)`: `target` (str), `condition` (optional str), `label` (optional str), `description` (optional str)
  - `DecisionNodeConfig(BaseModel)`: `mode` (StrEnum: deterministic/ai_bounded/ai_checkpoint/ai_autonomous), `confidence_threshold` (optional float, ge=0.0, le=1.0), `model` (optional str), `context_prompt` (optional str), `checkpoint_prompt` (optional str), `edges` (list[DecisionEdge], min_length=1)
  - Validator on `DecisionNodeConfig`: if mode is `deterministic`, every edge must have a non-empty `condition`. If mode is any AI mode, `confidence_threshold` is required.
  - Validator on `DecisionEdge`: if parent mode is `deterministic`, `condition` must be non-empty string

**Acceptance Criteria:**
- [ ] `AgentNodeConfig` requires agent and output_key
- [ ] `AgentPoolNodeConfig` requires at least one agent
- [ ] `DecisionEdge` model with all fields
- [ ] Deterministic mode requires condition on every edge
- [ ] AI modes require confidence_threshold
- [ ] Tests cover valid configs and each validation failure case

### US-004: Remaining Config Models and Type-Discriminated Node Parsing

**Description:** As a framework developer, I want config models for checkpoint, fork, join, emit, and end nodes, plus a validator that parses the config dict into the correct typed model based on node type.

**Implementation Hints:**
- Create remaining config models in `roots/core/schema.py`:
  - `CheckpointNodeConfig(BaseModel)`: `prompt` (str)
  - `ForkNodeConfig(BaseModel)`: no required fields (fork behavior is implicit from edges)
  - `JoinNodeConfig(BaseModel)`: `merge_strategy` (StrEnum: merge_all/collect, default merge_all), `collect_key` (optional str), `allow_partial` (bool, default False). Validator: `collect_key` required when merge_strategy is `collect`.
  - `EmitNodeConfig(BaseModel)`: `event_type` (str — custom event name like `"process.quality_check_complete"`), `payload_keys` (optional list[str] — keys from work item state to include in event metadata, default empty). If `payload_keys` is set, the orchestrator extracts those keys from work item state and includes them in the event's metadata dict.
  - `EndNodeConfig(BaseModel)`: `status` (StrEnum: completed/failed)
- Add `model_validator(mode="after")` on `NodeDefinition` that parses `config` dict into the correct typed model:
  - Build a mapping: `{NodeType.agent: AgentNodeConfig, NodeType.agent_pool: AgentPoolNodeConfig, ...}`
  - In the validator: `if isinstance(self.config, dict): self.config = CONFIG_MAP[self.type].model_validate(self.config)` — the isinstance guard is critical: if config is already a typed model (e.g., constructed programmatically), skip re-parsing. Without this, DB round-trips and programmatic construction break.
  - On validation failure: raise with context like `"Node 'my_node' (agent): field 'output_key' is required"`

**Acceptance Criteria:**
- [ ] All 5 remaining config models validate correctly
- [ ] `JoinNodeConfig` requires `collect_key` when merge_strategy is collect
- [ ] `EmitNodeConfig` has event_type and optional payload_keys
- [ ] `NodeDefinition.config` is parsed into the correct typed model based on `type`
- [ ] Mismatched config raises error with node ID and type context
- [ ] Tests cover each config model and the discriminator logic

### US-005: Edge Model and Process Definition Model

**Description:** As a framework developer, I want edge and process definition models so that complete process graphs can be represented and validated as Pydantic models.

**Implementation Hints:**
- Define `EdgeDefinition` in `roots/core/schema.py`: `id` (optional str — auto-generated via `default_factory=lambda: str(uuid4())`), `from_node` (str, use `Field(alias="from")` for YAML compat since `from` is a Python keyword), `to_node` (str, use `Field(alias="to")`), `label` (optional str), `condition` (optional str), `emit_event` (optional bool, default False)
- Configure model with `model_config = ConfigDict(populate_by_name=True)` so both `from` and `from_node` work
- **IMPORTANT serialization convention:** Always use `model_dump(by_alias=True, mode="json")` when serializing for storage or API output. This ensures the JSON uses `from`/`to` (the YAML-friendly names), not `from_node`/`to_node`. Without this, YAML→DB→load round-trips break because the DB stores `from_node` but the model expects `from` on re-parse.
- Define `ProcessDefinition`: `id` (str), `name` (str), `version` (str), `description` (optional str), `work_item_schema` (optional str — path to JSON schema file), `nodes` (list[NodeDefinition]), `edges` (list[EdgeDefinition]), `entry_point` (str)
- Add `model_validator(mode="after")`:
  - Build `_node_map: dict[str, NodeDefinition]` from nodes list
  - Validate `entry_point` references existing node ID
  - Validate all edge `from_node` and `to_node` reference existing node IDs
- Add helper methods: `get_node(node_id) -> NodeDefinition | None`, `get_outbound_edges(node_id) -> list[EdgeDefinition | DecisionEdge]` — for decision nodes, returns edges from config; for others, returns matching top-level edges

**Acceptance Criteria:**
- [ ] `EdgeDefinition` correctly handles `from` alias
- [ ] `ProcessDefinition` validates entry_point exists in nodes
- [ ] `ProcessDefinition` validates all edge references point to existing nodes
- [ ] `get_node()` and `get_outbound_edges()` work correctly
- [ ] Decision node outbound edges come from config, not top-level edges list
- [ ] Non-decision node outbound edges come from top-level edges list
- [ ] Tests cover valid process definitions and all reference validation failures

### US-006: YAML Parsing Pipeline

**Description:** As a process author, I want to load a YAML file and get either a validated ProcessDefinition or clear error messages so that I can author and debug process definitions confidently.

**Implementation Hints:**
- Create `roots/core/validator.py` with:
  - `load_process_yaml(path: str | Path) -> ProcessDefinition` — reads file, calls `safe_load`, passes dict to `parse_process_dict`, raises on error
  - `parse_process_dict(data: dict) -> ProcessDefinition` — validates dict against Pydantic model, returns ProcessDefinition. This is used by the API (which receives JSON dicts, not YAML).
  - `validate_process_yaml(path: str | Path) -> list[str]` — same as load but returns list of error strings instead of raising
- Use PyYAML `yaml.safe_load()` for parsing
- Catch `yaml.YAMLError` for syntax errors, `pydantic.ValidationError` for schema errors
- Transform Pydantic ValidationError into user-friendly messages: iterate `error.errors()`, for each error extract the `loc` tuple and `msg`. If the loc path includes a node index, resolve it to the node ID. Format: `"Node 'validation_gate' (decision): confidence_threshold — Field required"`
- Rule for node context: if the error's `loc` starts with `('nodes', <index>, ...)`, look up the node at that index and include its `id` and `type` in the message.

**Acceptance Criteria:**
- [ ] Valid YAML files parse into ProcessDefinition objects
- [ ] Invalid YAML syntax produces clear parse errors with line numbers
- [ ] Pydantic validation errors include node ID and type context when applicable
- [ ] `parse_process_dict` works for API/programmatic use
- [ ] `validate_process_yaml` returns error list (empty = valid)
- [ ] Tests cover: valid YAML, YAML syntax error, missing required field, node-level validation error

### US-007: Structural Validator — Basic Rules

**Description:** As a process author, I want structural validation that catches graph-level errors so that my process definitions are correct before execution.

**Implementation Hints:**
- Add `validate_structure(process: ProcessDefinition) -> list[str]` to `roots/core/validator.py`
- Implement these rules (return all errors, don't stop at first):
  - **Decision edge exclusivity:** Nodes of type `decision` must NOT appear as `from_node` in the top-level `edges` list. Error: `"Decision node '{id}' must not have top-level outbound edges — define edges in the node's config block"`
  - **Edge completeness:** Every non-end, non-decision, non-join node must have at least one outbound edge in the top-level edges list. Error: `"Node '{id}' ({type}) has no outbound edges"`
  - **End node existence:** At least one node of type `end` must exist. Error: `"Process has no end node"`
  - **Reachability:** BFS/DFS from `entry_point` — all nodes must be reachable. Warning (not error): `"Node '{id}' is unreachable from entry point"`
  - **Fallback edge validity:** If a node has retry config with `on_exhaustion: route`, the `fallback_edge` must be a valid node ID. Error: `"Node '{id}': fallback_edge '{edge}' does not reference a valid node"`
- Integrate into `load_process_yaml`: after Pydantic validation, run structural validation. If errors, raise `ProcessValidationError` with all error strings.
- Define `ProcessValidationError(Exception)`: `errors: list[str]`

**Acceptance Criteria:**
- [ ] Decision nodes with top-level outbound edges are rejected
- [ ] Non-terminal nodes without outbound edges are flagged
- [ ] Missing end nodes are flagged
- [ ] Unreachable nodes produce warnings
- [ ] Invalid fallback_edge references are caught
- [ ] All errors returned together (not one at a time)
- [ ] Tests cover each rule independently

### US-008: Structural Validator — Fork/Join Pairing

**Description:** As a process author, I want fork/join pairing validation so that parallel execution structures are guaranteed correct at definition time.

**Implementation Hints:**
- Add fork/join validation to `validate_structure` in `roots/core/validator.py`:
- **Algorithm:** For each `fork` node:
  1. Get all outbound edges (these are the branch entry points)
  2. For each branch, trace forward (BFS) through the graph until hitting a `join` node or a dead end
  3. All branches must terminate at the SAME `join` node
  4. No branch may reach an `end` node without passing through the `join`
  5. No branch may reach a different `join` node
- Compute the fork→join mapping as a dict: `fork_join_map: dict[str, str]` mapping fork node ID → join node ID. Store this as a **regular field** (not private/underscore-prefixed) on ProcessDefinition so it survives serialization. Alternatively, recompute it: add a call to fork/join validation inside `parse_process_dict()` so the map is recomputed every time a process is loaded from storage. **Recommended approach:** recompute on load — it's cheap and avoids serialization concerns entirely. Add a `recompute_fork_join_map()` method on ProcessDefinition that `parse_process_dict` and `load_process_yaml` both call after structural validation.
- **Errors:**
  - `"Fork node '{id}' has no outbound edges — need at least 2 branches"`
  - `"Fork node '{id}': branches converge at different join nodes ('{join1}', '{join2}')"`
  - `"Fork node '{id}': branch starting at '{node}' reaches end node without passing through a join"`
  - `"Fork node '{id}': branch starting at '{node}' has no path to a join node"`
  - `"Join node '{id}' is not paired with any fork node"`
- Every `join` must be the target of exactly one `fork`. Every `fork` must have exactly one downstream `join`.

**Acceptance Criteria:**
- [ ] Valid fork/join pairs pass validation
- [ ] Unpaired fork nodes are detected
- [ ] Unpaired join nodes are detected
- [ ] Branches that escape to end nodes without join are caught
- [ ] Branches converging at different joins are caught
- [ ] Fork→join mapping is stored on ProcessDefinition for runtime use
- [ ] Tests cover: valid 2-branch, valid 3-branch, missing join, divergent joins, branch escaping to end

## Out of Scope

- Runtime execution of any node type (that's T1.3+)
- Storage persistence of process definitions (that's T1.2)
- YAML generation or process definition authoring tools
- Process versioning or migration logic (Phase 2)

## Technical Considerations

- All models use Pydantic v2 — use `model_config = ConfigDict(...)` for configuration
- The `from` keyword conflict for edges requires `Field(alias="from")` with `populate_by_name=True`
- The model_validator approach for node config typing is simpler than Pydantic discriminated unions for this use case
- Keep `schema.py` importable without side effects (no IO, no database)
- The fork→join mapping computed during validation is needed at runtime — recompute it on every load (cheap BFS, avoids serialization concerns)
- **Serialization convention:** Always use `model_dump(by_alias=True, mode="json")` for storage and API. This is a project-wide rule — document in conftest or a shared util.

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
- Conventions: [CONVENTIONS.md](../docs/CONVENTIONS.md)
