# DECISIONS.md

> Last updated: 2026-06-25
> Updated by: Claude

This file records significant architectural and technical decisions for the Roots framework.

---

## Decision Log

### 2026-03-23: YAML for Process Definitions

**Status:** Accepted

**Context:**
Roots needs a way for users to define processes (workflows). The question is whether processes should be defined in code (Python DSL) or in a declarative format.

**Options Considered:**

1. **Python DSL (code-defined workflows)**
   - Pros: Full language power, IDE autocomplete, type checking
   - Cons: Harder to validate statically, mixes logic with structure, steeper learning curve for non-developers

2. **YAML declarative definitions**
   - Pros: Readable by non-developers, easy to validate, portable, can be packaged in .root files
   - Cons: Less expressive, requires a parser/validator, no native IDE support

**Decision:**
Use YAML as the format for process definitions.

**Rationale:**
YAML aligns with the goal of making processes accessible to non-developers. Static validation is straightforward, and YAML files are easily packaged and shared. The declarative approach enforces a clean separation between process structure and agent implementation.

**Consequences:**
- Process definitions are data, not code — enables tooling, validation, and packaging
- Complex conditional logic requires expression evaluation (see simpleeval decision)
- Need robust YAML schema validation

---

### 2026-03-23: Stateless-Between-Ticks Orchestrator Design

**Status:** Accepted

**Context:**
The orchestrator drives process execution. It could maintain in-memory state across ticks or reload state from storage each tick.

**Options Considered:**

1. **Stateful orchestrator (in-memory state)**
   - Pros: Faster (no I/O per tick), simpler tick logic
   - Cons: State lost on crash, harder to scale horizontally, harder to debug

2. **Stateless-between-ticks orchestrator**
   - Pros: Crash-resilient (state in storage), horizontally scalable, debuggable (inspect storage)
   - Cons: I/O on every tick, slightly more complex tick logic

**Decision:**
The orchestrator is stateless between ticks. All process state is persisted to storage after each tick and reloaded at the start of the next.

**Rationale:**
Crash resilience and debuggability outweigh the performance cost of per-tick I/O. This design also enables future horizontal scaling and makes the orchestrator easier to reason about.

**Consequences:**
- Every tick is self-contained: load state, evaluate, persist state
- Storage backends must support atomic state updates
- Enables future multi-instance orchestrator deployment

---

### 2026-03-23: Agent Contracts Decouple Process from Implementation

**Status:** Accepted

**Context:**
Processes reference agents (AI or human actors that perform work). The question is how tightly coupled a process definition should be to a specific agent implementation.

**Options Considered:**

1. **Direct binding (process references agent class/function)**
   - Pros: Simple, no indirection
   - Cons: Changing agent implementation requires changing process definition

2. **Agent contracts (interface layer)**
   - Pros: Process defines what it needs (input/output schema), agent implements the contract; swappable
   - Cons: Additional abstraction layer, contract validation needed

**Decision:**
Introduce agent contracts as an interface layer. Processes reference contracts (with input/output schemas), and agents implement those contracts.

**Rationale:**
Decoupling enables process portability — a process can run with different agent implementations (e.g., AI agent vs. human agent vs. mock agent). This is essential for testing and for the .root packaging format.

**Consequences:**
- Processes are portable and testable with mock agents
- Agent implementations can be swapped without changing process definitions
- Schema validation ensures contract compliance at invocation time

---

### 2026-03-24: Custom LLM Shim (Replacing LiteLLM)

**Status:** Accepted (supersedes initial LiteLLM decision)

**Context:**
Initially chose LiteLLM for model-agnostic AI decision nodes. On 2026-03-24, a PyPI supply chain attack affecting LiteLLM was discovered, requiring immediate removal of the dependency.

**Options Considered:**

1. **Keep LiteLLM (pin to known-good version)**
   - Pros: No code changes needed
   - Cons: Supply chain risk, trust compromised, unclear scope of attack

2. **Custom LLM shim**
   - Pros: No third-party dependency for critical path, full control, minimal surface area
   - Cons: Must maintain ourselves, less model coverage initially

3. **Switch to another wrapper library**
   - Pros: Still get multi-model support
   - Cons: Same supply chain risk category, another dependency to trust

**Decision:**
Replace LiteLLM with a custom LLM shim that directly calls provider APIs (OpenAI-compatible endpoint format).

**Rationale:**
The supply chain attack demonstrated unacceptable risk in depending on a third-party library for the critical LLM integration path. A custom shim with minimal code is easier to audit and has zero transitive dependency risk. The OpenAI-compatible API format covers most providers.

**Consequences:**
- Full control over LLM integration code
- Must add provider support manually (currently OpenAI-compatible only)
- Configured via `ROOTS_LLM_BASE_URL` and `ROOTS_LLM_API_KEY` environment variables
- Falls back to `OPENAI_API_KEY` for convenience

---

### 2026-03-24: Fork/Join NOT Crash-Safe in v1

**Status:** Superseded (2026-06) — fork/join and parallel agent_pool are now crash-safe via per-branch `branch_results` checkpointing (Embedding Enhancements epic). Retained as a record of the original v1 trade-off.

**Context:**
Fork/join nodes enable parallel branches in a process. Making fork/join fully crash-safe (resumable after a crash mid-fork) adds significant complexity to state management.

**Options Considered:**

1. **Crash-safe fork/join**
   - Pros: No data loss on crash during parallel execution
   - Cons: Complex state tracking, partial completion recovery, significantly more code

2. **Non-crash-safe fork/join (restart from fork point)**
   - Pros: Simple implementation, fork/join still works correctly when no crash occurs
   - Cons: Crash during fork/join loses progress on in-flight branches

**Decision:**
Fork/join is NOT crash-safe in v1. A crash during fork execution will require re-execution from the fork node.

**Rationale:**
v1 prioritizes shipping a correct and usable orchestrator. Crash-safe fork/join is a significant engineering effort that can be added later. The stateless-between-ticks design means the fork node itself is safely persisted; only in-flight branch progress is lost.

**Consequences:**
- Long-running parallel branches have a risk window during execution
- Tech debt item tracked in backlog for future hardening
- Users should be aware of this limitation for critical processes

---

### 2026-03-24: simpleeval for Safe Expression Evaluation

**Status:** Accepted

**Context:**
Process definitions need conditional expressions (e.g., decision node edges, guard conditions). These expressions are defined in YAML and evaluated at runtime.

**Options Considered:**

1. **Python `eval()`**
   - Pros: Full Python expression support
   - Cons: Arbitrary code execution vulnerability, unsafe with user-provided YAML

2. **simpleeval library**
   - Pros: Safe sandboxed evaluation, supports common operators and functions, well-maintained
   - Cons: Limited expression syntax (no imports, no function definitions)

3. **Custom expression parser**
   - Pros: Full control over syntax
   - Cons: Significant implementation effort, likely to have bugs

**Decision:**
Use the `simpleeval` library for all expression evaluation in process definitions.

**Rationale:**
Safety is non-negotiable for evaluating expressions from YAML files (which may come from .root packages). simpleeval provides a proven sandboxed evaluator that covers the expression complexity needed for process conditions.

**Consequences:**
- Expressions in YAML are safe to evaluate even from untrusted sources
- Limited to simpleeval's supported syntax (arithmetic, comparisons, basic string ops)
- No risk of code injection through process definitions

---

### 2026-03-24: .root Packaging Format

**Status:** Accepted

**Context:**
Roots processes, agent contracts, and configuration need to be packaged for sharing and distribution. Need a packaging format that is simple, inspectable, and self-contained.

**Options Considered:**

1. **Python wheel (.whl)**
   - Pros: Standard Python packaging, pip-installable
   - Cons: Overkill for YAML+config, requires setuptools boilerplate, not inspectable without extraction

2. **Custom zip archive (.root)**
   - Pros: Simple, inspectable (just a zip), can include manifest, custom validation
   - Cons: Non-standard, need custom tooling for install/publish

3. **Git-based distribution**
   - Pros: Versioning built-in
   - Cons: Requires git, not self-contained, no manifest

**Decision:**
Use a `.root` packaging format: a zip archive containing a manifest, process YAML files, agent contracts, and configuration.

**Rationale:**
A zip-based format is simple to create, inspect, and distribute. The manifest provides metadata and validation. This format is purpose-built for Roots and avoids the overhead of Python packaging for what are fundamentally YAML definitions and schemas.

**Consequences:**
- Custom CLI tooling for pack/unpack/install/publish
- Manifest schema defines package metadata and dependencies
- Foundation for future Root Registry (T3.8)
- Easy to inspect: `unzip -l package.root` shows contents

---

### 2026-06-14: Public Release — Distribution, Auth, and Docs Model

**Status:** Accepted

**Context:**
Roots' first public release (v0.1.0) required decisions about how it is distributed, secured, and documented.

**Decisions:**

- **Distribution name `rootsflow`, import name `roots`.** The PyPI names `roots` and `root` are both taken. The distribution (what you `pip install`) is `rootsflow`; the import package stays `roots` (`from roots import Roots`) — a short, available name without renaming the codebase.
- **Optional API authentication via `ROOTS_API_KEY`.** The HTTP API is unauthenticated by default (single-trust v1 model) but gains an opt-in API-key guard (`X-API-Key`); the server binds `127.0.0.1` and warns loudly on non-local binds. A full auth model (JWT/per-tenant) remains future work.
- **Single version source.** The version literal lives only in `roots/__init__.py`; `pyproject.toml` (dynamic via hatchling) and `roots/api/app.py` derive from it.
- **Website is the product home + changelog source of truth.** Canonical homepage is the Open Builds project page on washingbearlabs.com; the public changelog lives on the site (`roots-release-notes.md`), not as a repo `CHANGELOG.md`. `/kit-tools:bump-version` (driven by `kit_tools/BUMP_VERSION.md`) keeps the site docs + changelog in sync on each release — a two-repo release.
- **`kit_tools/` stays public but scrubbed.** Internal dev docs remain public (transparency + a future spec-based contribution model), with unreleased-project names and private-infra specifics removed.

**Consequences:**
- Users `pip install rootsflow`, then `import roots`.
- Each release updates two repos (code + site) via one runbook.

---

### 2026-06-25: Dotted-Path Resolution for Iterator/Subprocess State Reads

**Status:** Accepted

**Context:**
Agent nodes store their entire output dict under a single `output_key`, so values nest one level down (e.g. `state["epic_plan"]["stories"]`). Iterator (`items_key`, `input_mapping`) and subprocess (`input_mapping`) nodes read run state by exact top-level key only, so a nested list under `items_key` hard-failed and nested `input_mapping` values were silently dropped from child runs. Surfaced by the Poppy FR4 cross-repo handoff.

**Options Considered:**

1. **Reuse `flatten_for_eval`** (the existing decision-condition resolver)
   - Pros: one resolver; already supports list indices (`results.0.name`)
   - Cons: rebuilds the entire flattened dict (recursively walking every list) on every call — wasteful per-iteration on large accumulated state

2. **Dedicated dict-only path walker (`resolve_state_path`)**
   - Pros: O(path depth), not O(state size); explicit missing-vs-`None` sentinel
   - Cons: a second resolver; no list-index support (diverges from condition expressions)

**Decision:**
Add a dedicated `resolve_state_path` (dict-walk only, `STATE_PATH_MISSING` sentinel) in `decision.py`, used via a shared `ProcessRunner._resolve_input_mapping` at all four read sites. Dotless keys remain plain top-level lookups (fully back-compatible). Iterator `input_mapping` now **raises** on an unresolved key instead of silently skipping, matching the subprocess path's fail-loud behavior.

**Rationale:**
Per-iteration cost and an explicit raise-vs-skip choice matter more than sharing one resolver. Dict-only keeps the walker cheap, and the use cases (dict→dict nesting) don't need list indices. Raising surfaces wiring bugs immediately rather than handing children empty values.

**Consequences:**
- Iterator/subprocess `items_key`/`input_mapping` accept dotted paths (`epic_plan.stories`); dotless keys are unchanged.
- **Behavior change:** a missing iterator `input_mapping` key now raises `OrchestrationError` (was a silent skip) — see `docs/GOTCHAS.md`.
- List indices (`results.0.name`) work in decision conditions but **not** in these mapping keys.
- Shipped in v0.1.1.

---

<!-- Copy the template below for new decisions -->
<!--
### YYYY-MM-DD: [Decision Title]

**Status:** [Proposed / Accepted / Deprecated / Superseded]

**Context:**
[What is the issue that we're seeing that motivates this decision?]

**Options Considered:**

1. **[Option A]**
   - Pros: [List]
   - Cons: [List]

2. **[Option B]**
   - Pros: [List]
   - Cons: [List]

**Decision:**
[What is the change that we're proposing and/or doing?]

**Rationale:**
[Why did we choose this option over others?]

**Consequences:**
[What becomes easier or harder as a result of this decision?]
-->
