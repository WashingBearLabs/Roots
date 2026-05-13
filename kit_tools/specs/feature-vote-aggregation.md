<!-- Template Version: 2.2.0 -->
---
feature: vote-aggregation
status: active
session_ready: true
depends_on: []
vision_ref: "T3.4 — Vote Aggregation Strategy"
type: epic-child
size: M
epic: library-refinements
epic_seq: 1
epic_final: false
created: 2026-05-13
updated: 2026-05-13
---

# Feature Spec: Vote Aggregation for Agent Pools

## Overview

Agent pool nodes currently support only `merge_all` aggregation (shallow dict merge of all agent outputs). This limits agent pools to data collection — they can't express consensus patterns like "3 out of 5 reviewers must agree" or "weighted expert scoring." Vote aggregation adds three consensus strategies (majority, weighted, unanimous) that let agent pools make collective decisions, extending Roots' multi-agent orchestration without requiring new node types.

## Goals

- Add MAJORITY_VOTE, WEIGHTED_VOTE, and UNANIMOUS aggregation strategies to agent pool nodes
- Implement vote tallying with configurable thresholds, weights, and tie-breaking
- Maintain full backward compatibility — existing MERGE_ALL behavior unchanged

## User Stories

### US-001: Extend schema with vote aggregation types

**Description:** As a process author, I want to configure vote aggregation strategies on agent pool nodes so that I can define consensus-based workflows in YAML.

**Implementation Hints:**
- Aggregation enum at `roots/core/schema.py:59-61` — currently only has MERGE_ALL
- AgentPoolNodeConfig at `roots/core/schema.py:86-91` — add optional vote_config field
- Follow existing pattern: VoteConfig as a Pydantic BaseModel alongside AgentNodeConfig, DecisionNodeConfig, etc.
- Use Pydantic model_validator for cross-field validation (vote_config required when aggregation is a vote type)
- See `NodeDefinition` model_validator at schema.py:178 for validation pattern

**Acceptance Criteria:**
- [x] MAJORITY_VOTE, WEIGHTED_VOTE, UNANIMOUS values added to Aggregation enum; TieBreak enum added with FIRST_AGENT (config list order, not arrival order) and REJECT values
- [x] VoteConfig Pydantic model with: vote_key (str), threshold (float, 0.0-1.0, default 0.5), weights (dict[str, float] | None, default None), tie_break (TieBreak, default FIRST_AGENT)
- [x] vote_config: VoteConfig | None added to AgentPoolNodeConfig (default None)
- [x] Validation: vote_config required when aggregation is a vote type, rejected when MERGE_ALL
- [x] Validation: when weights provided, keys must be subset of agents list; weights required when aggregation is WEIGHTED_VOTE
- [x] Validation: vote aggregation types with execution_mode=FIRST_PASS rejected at schema time (model_validator, not runtime)
- [x] Existing MERGE_ALL processes validate without changes
- [x] Tests written/updated for new functionality
- [x] Full test suite passes
- [x] Typecheck/lint passes

### US-002: Implement vote tallying core

**Description:** As a framework developer, I want a standalone vote aggregation module so that vote tallying logic is testable independently of the orchestrator.

**Implementation Hints:**
- Create new module `roots/core/aggregation.py` — keeps orchestrator.py focused on execution flow
- AgentOutput structure at `roots/agents/types.py:67-70` — output is `dict[str, Any]`, vote_key extracts from this
- Follow error pattern: custom AggregationError exception (like OrchestrationError in orchestrator.py)
- Each agent's output dict is the input — extract `output[vote_key]` for the vote value
- Threshold denominator is voting agents only (abstentions excluded) — e.g., 2 voters out of 5 agents, 1 vote for "approve" = 1/2 = 50%, not 1/5
- "First agent" tie-breaking means first in the config agents list, not first to respond (parallel execution has nondeterministic arrival order)

**Acceptance Criteria:**
- [ ] aggregate_votes function in roots/core/aggregation.py accepts list of (agent_name, output_dict) pairs, Aggregation strategy, and VoteConfig
- [ ] Majority vote: most common value wins if proportion >= threshold (denominator = voting agents, not pool size); AggregationError if no value meets threshold
- [ ] Weighted vote: per-agent weights multiply vote count, highest weighted total wins; tie-breaking via TieBreak config
- [ ] Unanimous: all voting agents must return same value; AggregationError on disagreement
- [ ] Agents whose output lacks vote_key treated as abstentions (excluded from denominator)
- [ ] AggregationError raised if all agents abstain (zero votes cast)
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes
- [ ] Typecheck/lint passes

### US-003: Vote result structure and edge cases

**Description:** As a framework consumer, I want vote results to be structured so that downstream nodes can easily access the winning value and I can inspect vote details.

**Implementation Hints:**
- The aggregate_votes return dict is stored at state[output_key] by the orchestrator
- Downstream decision conditions access state like `state["vote_result"]` — winning_value must be directly accessible without knowing internal structure
- Tie-breaking applies to all strategies: majority (multiple values at same count), weighted (equal weighted totals), unanimous (N/A — ties are agreements)

**Acceptance Criteria:**
- [ ] Result dict has winning_value at top level (directly accessible by downstream conditions as state[output_key]["winning_value"])
- [ ] Result dict includes vote_counts (dict mapping vote values to count), strategy (str), and participating_agents (int)
- [ ] TieBreak.FIRST_AGENT selects the value voted for by the first agent in config order that voted for a tied value
- [ ] TieBreak.REJECT raises AggregationError when a tie occurs
- [ ] Tests cover: 3-way tie with first_agent, 2-way tie with reject, all-abstain, single-voter
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes
- [ ] Typecheck/lint passes

### US-004: Wire vote aggregation into orchestrator

**Description:** As a process author, I want vote aggregation to work end-to-end when I run an agent pool with vote configuration so that my workflows produce consensus results.

**Implementation Hints:**
- Agent pool handler at `roots/core/orchestrator.py:494-630` — _handle_agent_pool, _pool_parallel, _pool_sequential
- Currently aggregation field is unused; all modes use dict.update() for merging
- After collecting results from _pool_parallel/_pool_sequential, dispatch to aggregate_votes when config.aggregation is a vote type
- first_pass + vote aggregation is already rejected at schema validation time (US-001) — no runtime check needed here
- Keep MERGE_ALL path using existing dict.update() behavior (don't change to deep_merge — avoid subtle behavioral change)
- Note: _pool_parallel currently interleaves escalation checks with result assembly — will need refactoring to collect named results before dispatching to aggregate_votes

**Acceptance Criteria:**
- [ ] _handle_agent_pool routes to aggregate_votes when aggregation is a vote type
- [ ] Vote aggregation works with parallel execution mode (all agents vote concurrently)
- [ ] Vote aggregation works with sequential execution mode (agents vote in order)
- [ ] AggregationError from vote tallying triggers run failure (same pattern as RetryExhaustedError)
- [ ] MERGE_ALL behavior unchanged — existing agent pool tests still pass
- [ ] Tests written/updated for new functionality
- [ ] Full test suite passes
- [ ] Typecheck/lint passes

## Out of Scope

- Quorum requirements (minimum number of agents must vote) — can add later as VoteConfig field
- Custom aggregation functions (user-provided callables)
- Vote audit trail beyond existing event system and state storage
- Aggregation for non-vote types (e.g., COLLECT as list aggregation) — separate concern

## Technical Considerations

- The `aggregation` field on AgentPoolNodeConfig exists but is currently **unused** — even MERGE_ALL is hardcoded via dict.update(). This feature makes the field functional.
- Vote values are extracted from agent output dicts via `vote_key`. Values can be any hashable type (string, int, bool).
- The `deep_merge` utility in orchestrator.py is used by fork/join, not agent pools. Agent pools use shallow dict.update(). Don't change this.

## Design Considerations

N/A — no UI components.

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
- Known Issues: [GOTCHAS.md](../docs/GOTCHAS.md)

## Implementation Notes

<!-- Populated during implementation -->

## Refinement Notes

### Research Conducted
- Explored Aggregation enum (schema.py:59-61): only MERGE_ALL, placeholder for expansion
- Explored orchestrator agent pool handlers (orchestrator.py:494-630): aggregation field completely unused
- Confirmed deep_merge utility exists but is only used by join nodes, not agent pools
- Verified AgentOutput.output is dict[str, Any] — vote_key extraction is straightforward

### Scope Adjustments
- Excluded COLLECT aggregation (list-based) — different concern from voting
- first_pass execution mode explicitly incompatible with vote aggregation (needs all agents)
- Split US-002 into US-002 (core tallying) and US-003 (result structure + edge cases) after validation review flagged 10-criterion ceiling
- Removed individual_votes from result dict (premature — event system handles per-agent observability)

### Decisions Made
- New module roots/core/aggregation.py rather than adding to orchestrator.py — separation of concerns
- TieBreak enum (FIRST_AGENT | REJECT) rather than arbitrary tie-breaking — deterministic and predictable
- "First agent" means first in config agents list, not first to respond — deterministic across parallel/sequential
- Abstention model: missing vote_key = don't count, excluded from denominator — matches real-world voting semantics
- Threshold denominator = voting agents only (abstentions excluded) — 2 voters out of 5 agents, threshold applies to 2
- first_pass + vote aggregation rejected at schema validation time, not runtime — fail fast, don't commit upstream state
- AggregationError in orchestrator triggers run failure (same as RetryExhaustedError pattern)

## Open Questions

None — all design questions resolved during planning and validation review.
