<!-- Template Version: 2.0.0 -->
---
feature: decision-engine
status: active
session_ready: true
depends_on: [process-schema, storage-backend]
vision_ref: "T1.4 — Decision Engine"
type: epic-child
epic: roots-v1
epic_seq: 4
epic_final: false
created: 2026-03-23
updated: 2026-03-23
---

# Feature Spec: Decision Engine

## Overview

The decision engine evaluates decision nodes across all four modes: deterministic (safe expression evaluation), ai_bounded, ai_checkpoint, and ai_autonomous. It uses LiteLLM for AI modes, providing model-agnostic support for any LLM provider (Anthropic, OpenAI, Gemini, Ollama, etc.) via a single unified interface. The model is configurable per node. The confidence threshold mechanism provides a safety net — any AI decision below threshold automatically escalates to checkpoint mode. All decisions are recorded to storage for Phase 2 retrieval.

## Goals

- Implement a safe expression evaluator for deterministic decisions (no `eval()`)
- Implement AI decision modes using LiteLLM for model-agnostic structured responses
- Provide confidence threshold escalation as a cross-cutting safety mechanism

## User Stories

### US-001: Safe Expression Evaluator

**Description:** As a framework developer, I want a safe expression evaluator so that deterministic decision conditions can be evaluated against work item state without security risks.

**Implementation Hints:**
- Create `roots/core/decision.py`
- Implement `evaluate_condition(expression: str, state: dict) -> bool`
- Use the `simpleeval` library (already in pyproject.toml from T1.1). Do NOT build a custom parser.
- Use `simpleeval.EvalWithCompoundTypes` class
- Supported operations: dot notation field access (`output.severity`), comparison operators (`==`, `!=`, `>`, `<`, `>=`, `<=`), `in` operator, boolean operators (`and`, `or`, `not`), string/number/boolean/None literals, list literals
- **Dot notation setup:** Flatten the nested state dict into simpleeval's `names` dict. Write a helper `flatten_for_eval(state: dict, prefix="") -> dict` that converts `{"output": {"severity": "critical"}}` into `{"output.severity": "critical", "output": {"severity": "critical"}}`. This allows both `output.severity` and `output` to resolve.
- **Array indexing:** The flatten helper must also walk lists. `{"results": [{"name": "a"}, {"name": "b"}]}` flattens to include `{"results.0.name": "a", "results.1.name": "b", "results.0": {"name": "a"}, ...}`. This is required by the parallel-validation example (collect strategy produces a list).
- Configure simpleeval: set `functions={}` (no function calls allowed), `names=flatten_for_eval(state)`
- On missing key: simpleeval raises `NameNotDefined` — catch and re-raise as `DecisionEvaluationError` with the expression and missing field path
- On type error: catch `TypeError` from simpleeval — re-raise as `DecisionEvaluationError`
- Wrap the whole evaluation in try/except to catch any simpleeval exceptions and provide context

**Acceptance Criteria:**
- [x] Dot notation traverses nested dicts correctly
- [x] All comparison operators work with strings and numbers
- [x] `in` operator works with lists
- [x] Boolean operators combine conditions correctly
- [x] Missing keys produce clear error with field path
- [x] No access to Python builtins or functions
- [x] Array index access via dot notation works (e.g., `results.0.name`)
- [x] Tests cover: nested access, array indexing, all operators, missing keys, type mismatches, boolean logic

### US-002: Deterministic Decision Mode

**Description:** As a framework developer, I want deterministic decision evaluation so that process authors can route execution based on work item state without AI involvement.

**Implementation Hints:**
- Create `DecisionEngine` class in `roots/core/decision.py`
- Method `evaluate(node: NodeDefinition, work_item_state: dict) -> DecisionResult`
- `DecisionResult` model: `selected_edge` (str — target node ID), `mode` (str), `confidence` (float — always 1.0 for deterministic), `reasoning` (optional str)
- For deterministic mode: iterate `node.config.edges`, evaluate each `condition` against state, return the first match
- If no condition matches: raise `DecisionEvaluationError` with message listing the node ID, all conditions tried, and the relevant state values
- Define `DecisionEvaluationError(Exception)`: includes node_id, expression, context

**Acceptance Criteria:**
- [x] First matching edge is selected
- [x] `confidence` is always 1.0 for deterministic
- [x] No-match raises DecisionEvaluationError with helpful context
- [x] Edge order matters (first match wins)
- [x] Tests cover: single match, first-of-multiple match, no match

### US-003: AI Decision Response Model

**Description:** As a framework developer, I want a structured AI response model so that AI decision modes return predictable, validated results.

**Implementation Hints:**
- Define `AIDecisionResponse` Pydantic model: `selected_edge_target` (str), `confidence` (float, 0.0-1.0), `reasoning` (str)
- This model is used as the structured output format for LLM calls via LiteLLM
- Build the prompt template that the AI receives:
  - System prompt: "You are a decision evaluator in a process orchestration system. Evaluate the current state and select the most appropriate next step."
  - Include: the node's `context_prompt`, the current work item state (JSON), and the available edges (target, label, description for each)
  - Instruct the model to respond via tool call matching `AIDecisionResponse`
- Use LiteLLM with tool use for structured output. LiteLLM normalizes tool use across providers (Anthropic, OpenAI, Gemini, etc.). Define the tool in OpenAI-compatible format (LiteLLM translates for other providers):
  ```python
  import litellm

  decision_tool = {
      "type": "function",
      "function": {
          "name": "make_decision",
          "description": "Select the next step in the process",
          "parameters": {
              "type": "object",
              "properties": {
                  "selected_edge_target": {"type": "string", "description": "The target node ID to route to"},
                  "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                  "reasoning": {"type": "string"}
              },
              "required": ["selected_edge_target", "confidence", "reasoning"]
          }
      }
  }

  response = await litellm.acompletion(
      model=model,  # "claude-sonnet-4-20250514", "gpt-4o", "ollama/llama3", etc.
      messages=messages,
      tools=[decision_tool],
      tool_choice={"type": "function", "function": {"name": "make_decision"}}
  )
  ```
- **Response parsing with fallback:** Not all providers support `tool_choice` (Ollama, some local models). Implement a two-path parser:
  1. **Primary path (tool call):** `response.choices[0].message.tool_calls[0].function.arguments` → `json.loads()` → `AIDecisionResponse.model_validate()`
  2. **Fallback path (text response):** If `tool_calls` is None or empty, try to parse `response.choices[0].message.content` as JSON matching AIDecisionResponse. Strip markdown fences (` ```json ... ``` `) if present.
  3. **Both fail:** Raise `DecisionEvaluationError` with the raw response content for debugging.
- Wrap `json.loads()` in try/except `JSONDecodeError` — malformed JSON from the model is a real failure mode, not an edge case.
- The `model` parameter comes from: node config `model` field → framework `default_model` (required, no hardcoded fallback)
- LiteLLM model strings use provider prefixes where needed: `"claude-sonnet-4-20250514"` (Anthropic), `"gpt-4o"` (OpenAI), `"ollama/llama3"` (Ollama), `"gemini/gemini-pro"` (Google). See LiteLLM docs for full list.

**Acceptance Criteria:**
- [x] `AIDecisionResponse` model validates confidence range [0.0, 1.0]
- [x] Prompt template includes context_prompt, state, and edge descriptions
- [x] Model selection follows the fallback chain: node → default → constant
- [x] Tests validate the response model independently (no API call needed)

### US-004: AI Bounded and AI Autonomous Modes

**Description:** As a framework developer, I want ai_bounded and ai_autonomous decision modes so that AI can make constrained decisions with or without human checkpoints.

**Implementation Hints:**
- In `DecisionEngine.evaluate`, add handling for `ai_bounded` and `ai_autonomous`:
  - Both call LiteLLM with the same prompt structure and tool definition
  - Parse response into `AIDecisionResponse`
  - Validate that `selected_edge_target` is one of the defined edge targets (bounded constraint)
  - If the AI picks an edge not in the list: log a warning, escalate to checkpoint (set `escalated=True`) rather than crashing — the human can redirect to a valid edge
  - **Confidence threshold check** (both modes): if `response.confidence < node.config.confidence_threshold`, override to checkpoint behavior (return a `DecisionResult` with `escalated=True`)
  - For `ai_autonomous` with confidence above threshold: return the decision directly
  - For `ai_bounded` with confidence above threshold: return the decision directly (same behavior as autonomous above threshold — the distinction is semantic for process authors)
- Add `escalated` (bool, default False) and `ai_recommendation` (optional AIDecisionResponse) to `DecisionResult`

**Acceptance Criteria:**
- [ ] AI modes call LiteLLM with correct prompt and tool definition
- [ ] Response is validated — edge target must be in defined edges
- [ ] Invalid edge target raises DecisionEvaluationError
- [ ] Confidence below threshold sets `escalated=True` on result
- [ ] Confidence above threshold returns decision normally
- [ ] Tests mock `litellm.acompletion` (no real API calls)

### US-005: AI Checkpoint Mode

**Description:** As a framework developer, I want ai_checkpoint mode so that AI proposes a decision but execution pauses for human confirmation.

**Implementation Hints:**
- In `DecisionEngine.evaluate`, add handling for `ai_checkpoint`:
  - Call LiteLLM same as bounded/autonomous
  - Always set `escalated=True` on the result (checkpoint mode always pauses)
  - Include the `ai_recommendation` (the full AIDecisionResponse) in the result
  - The orchestrator uses this to create a checkpoint record with the AI's recommendation
  - The `checkpoint_prompt` from the node config is used by the orchestrator when surfacing the checkpoint — the decision engine just passes it through in the result
- Add `checkpoint_prompt` (optional str) to `DecisionResult`

**Acceptance Criteria:**
- [ ] ai_checkpoint always returns `escalated=True`
- [ ] AI recommendation is included in the result
- [ ] checkpoint_prompt from node config is passed through
- [ ] Confidence threshold still applies (but result is escalated regardless)
- [ ] Tests verify escalation behavior

### US-006: Decision History Recording

**Description:** As a framework developer, I want all decisions recorded to storage so that Phase 2 can retrieve historical decisions as AI context.

**Implementation Hints:**
- Add `record_decision` method to `DecisionEngine` that takes storage backend, run context, and decision result
- Call `storage.append_decision(run_id, process_id, node_id, mode, input_state, decision, confidence)`
- This should be called by the orchestrator after every decision evaluation (T1.3 will integrate this)
- The decision engine itself doesn't call storage — it returns the result and the orchestrator records it
- Actually, simpler: just add a `to_decision_record()` method on `DecisionResult` that produces the data dict for storage

**Acceptance Criteria:**
- [ ] `DecisionResult.to_decision_record()` produces a complete record dict
- [ ] Record includes: mode, selected edge, confidence, reasoning, input state snapshot
- [ ] Deterministic decisions are recorded (confidence 1.0, no reasoning)
- [ ] AI decisions include reasoning from the AI response
- [ ] Tests verify record format for all modes

## Out of Scope

- Decision history retrieval for AI context (Phase 2 — T3.1)
- Custom decision modes or plugins
- Caching of AI decisions
- Model fine-tuning or prompt optimization

## Technical Considerations

- LiteLLM should be a required dependency but AI features gracefully degrade if no API keys are configured — deterministic mode works with zero LLM configuration
- `simpleeval` needs careful configuration to prevent code execution
- Mock `litellm.acompletion` in all tests — use `unittest.mock.AsyncMock` patching `litellm.acompletion`
- The confidence threshold check is the same logic for all three AI modes — don't duplicate it
- The `default_model` needs to come from somewhere configurable — the `Roots` class constructor passes it to the decision engine

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
