<!-- Template Version: 2.0.0 -->
---
feature: root-defaults
status: active
session_ready: true
depends_on: [root-install]
vision_ref: "Root Packaging — Default Implementations & Templates"
type: epic-child
epic: root-packaging
epic_seq: 3
epic_final: true
created: 2026-03-24
updated: 2026-03-24
---

# Feature Spec: Root Defaults & Configuration Templates

## Overview

Default agent implementations bundled inside Root packages that work out of the box. When a consumer installs a Root with `--apply-defaults`, the package's bundled agent implementations are registered automatically — the process runs immediately without any wiring. This is critical for onboarding: "install and run" is a much better first experience than "install, read the contracts, implement 5 agents, then run." Consumers replace defaults with their own implementations as they integrate the Root into their stack.

## Goals

- Enable "install and run" experience with bundled default agent implementations
- Provide a safe mechanism for loading Python code from packages
- Support configuration templates that pre-fill common override scenarios

## User Stories

### US-001: Default Agent Loading

**Description:** As a consumer, I want default agent implementations loaded automatically when I install a Root with `--apply-defaults` so that the process works immediately.

**Implementation Hints:**
- Create `roots/packaging/defaults.py`:
  - `load_defaults(archive_contents: dict[str, bytes], manifest: RootManifest, roots: Roots) -> list[str]`:
    - If `manifest.has_defaults` is False: return empty list
    - Extract the `defaults/` directory from archive contents
    - Write defaults to a temporary directory (or the install directory)
    - Import the module specified by `manifest.defaults_module` (e.g., "defaults.agents")
    - The module must expose a `register_agents(roots: Roots) -> list[str]` function that:
      - Calls `roots.register_agent(name, callable, input_schema, output_schema)` for each default agent
      - Returns list of registered agent names
    - Call `register_agents(roots)` and return the registered names
  - **Security consideration:** Loading arbitrary Python code from a package is inherently risky. Mitigations:
    - Print a clear warning: `"⚠ Loading default agents from package. Only install packages from trusted sources."`
    - The `--apply-defaults` flag is opt-in, not automatic
    - Future: package signing (out of scope for this spec)
- The `defaults/agents.py` convention:
  ```python
  """Default agent implementations for the incident-response Root."""

  async def ingest_incident(input: dict) -> dict:
      """Normalizes raw incident data. Replace with your SIEM integration."""
      state = input["work_item_state"]
      return {
          "source_ip": state.get("source_ip", "unknown"),
          "event_type": state.get("event_type", "unknown"),
          "severity": "medium",
          "normalized": True,
      }

  async def threat_intel_lookup(input: dict) -> dict:
      """Mock threat intel. Replace with your TI platform integration."""
      return {"threat_score": 0.5, "known_iocs": [], "source": "default"}

  def register_agents(roots):
      """Register all default agents. Called by roots install --apply-defaults."""
      agents = [
          ("ingest_incident", ingest_incident, {...}, {...}),
          ("threat_intel_lookup", threat_intel_lookup, {...}, {...}),
      ]
      registered = []
      for name, fn, in_schema, out_schema in agents:
          roots.register_agent(name, fn, input_schema=in_schema, output_schema=out_schema)
          registered.append(name)
      return registered
  ```

**Acceptance Criteria:**
- [ ] `load_defaults` extracts and imports default agent module from archive
- [ ] `register_agents` convention registers all agents with the Roots instance
- [ ] Returns list of registered agent names
- [ ] Prints security warning before loading
- [ ] Skips gracefully when `has_defaults` is False
- [ ] Tests verify default loading with a mock package
- [ ] Tests verify security warning is displayed

### US-002: Default Agent Scaffolding

**Description:** As a package author, I want `roots pack` to scaffold a defaults directory when I'm creating a package so that I have a starting point for writing default implementations.

**Implementation Hints:**
- Add `--scaffold-defaults` flag to `roots pack`:
  - Creates a `defaults/` directory alongside the process YAML
  - Generates `defaults/__init__.py` and `defaults/agents.py`
  - The generated `agents.py` contains:
    - A stub function for each agent contract extracted from the process
    - Each stub has the correct function signature, a docstring with the contract description, and a `# TODO: implement` comment
    - A `register_agents(roots)` function that registers all stubs
  - Example generated code:
    ```python
    async def threat_intel_lookup(input: dict) -> dict:
        """Enriches with threat intelligence data.

        Expected input schema: {source_ip: string, event_type: string}
        Expected output schema: {threat_score: number, known_iocs: array}

        TODO: Replace with your threat intelligence platform integration.
        """
        return {"threat_score": 0.0, "known_iocs": [], "source": "stub"}
    ```
- This is a development-time convenience — the author runs `roots pack --scaffold-defaults`, fills in the stubs, then packs again with `--include-defaults defaults/`

**Acceptance Criteria:**
- [ ] `--scaffold-defaults` creates a defaults/ directory with agents.py
- [ ] Generated stubs match extracted agent contracts (names, schemas in docstrings)
- [ ] Generated `register_agents` function registers all agents
- [ ] Stubs return minimal valid output matching output schema structure
- [ ] Generated code is syntactically valid Python
- [ ] Tests verify scaffold generation for a multi-agent process

### US-003: Configuration Templates

**Description:** As a package author, I want to include configuration templates so that consumers can quickly apply common configurations without reading all the override options.

**Implementation Hints:**
- Add `config_templates` to RootManifest:
  ```python
  class ConfigTemplate(BaseModel):
      name: str           # e.g., "high-security", "permissive", "production"
      description: str    # "Strict thresholds for production SOC operations"
      overrides: dict[str, Any]  # path → value mapping
  ```
  - Add `config_templates: list[ConfigTemplate] = []` to RootManifest
- Package authors define templates in the manifest:
  ```json
  "config_templates": [
    {
      "name": "high-security",
      "description": "Strict thresholds — escalates on any uncertainty",
      "overrides": {
        "nodes.triage.config.confidence_threshold": 0.95,
        "nodes.respond.retry.max_attempts": 5
      }
    },
    {
      "name": "permissive",
      "description": "Lower thresholds — AI handles more autonomously",
      "overrides": {
        "nodes.triage.config.confidence_threshold": 0.5,
        "nodes.respond.retry.max_attempts": 2
      }
    }
  ]
  ```
- Add CLI: `roots config templates <process-id>` — lists available templates
- Add CLI: `roots config apply-template <process-id> <template-name>` — applies all overrides from the named template
- Templates show up in `roots inspect` output

**Acceptance Criteria:**
- [ ] `ConfigTemplate` model added to manifest schema
- [ ] Templates serialized in manifest.json
- [ ] `roots config templates` lists available templates with descriptions
- [ ] `roots config apply-template` applies all overrides from a template
- [ ] Templates visible in `roots inspect` output
- [ ] Tests verify template application on installed process

### US-004: Package README Rendering

**Description:** As a consumer, I want to read a Root package's README so that I understand how to use it, what it does, and how to customize it.

**Implementation Hints:**
- Add CLI: `roots packages readme <package-path-or-id>`:
  - If argument is a `.root` file: extract README.md from the archive
  - If argument is an installed process ID: check if README was stored during install
  - Print the markdown content to terminal (rendered with `rich.Markdown` for formatting)
- During `roots install`: if the package contains README.md, store it in `process.metadata["readme"]` (as a string) so it's accessible after install without the archive
- The README convention for Root packages:
  ```markdown
  # Incident Response Root

  ## What This Does
  [Description of the process flow]

  ## Required Agents
  [Table of agents to implement with descriptions]

  ## Quick Start
  1. `roots install incident-response.root --apply-defaults`
  2. `roots run incident-response --work-item '{"source_ip": "..."}'`

  ## Customization
  [Common override scenarios and templates]

  ## Replacing Default Agents
  [How to wire your own implementations]
  ```

**Acceptance Criteria:**
- [ ] `roots packages readme package.root` displays README from archive
- [ ] `roots packages readme incident-response` displays README from installed process
- [ ] README stored in process metadata during install
- [ ] Markdown rendered with rich formatting in terminal
- [ ] Missing README handled gracefully (message, not error)
- [ ] Tests verify README extraction and storage

### US-005: End-to-End Pack → Install → Run

**Description:** As a framework developer, I want an end-to-end test that proves the full packaging lifecycle works so that we have confidence in the feature.

**Implementation Hints:**
- Create `tests/test_packaging_e2e.py`:
  - Test flow:
    1. Create a process YAML with 3 agent nodes + 1 decision node
    2. Create default agent implementations in a temporary directory
    3. Pack: `roots pack process.yaml --include-defaults defaults/ --version 1.0.0 --author "Test"`
    4. Verify: `roots inspect package.root` — check output contains correct info
    5. Install on a FRESH Roots instance (different storage): `roots install package.root --apply-defaults`
    6. Verify: contract validation shows all agents satisfied (defaults wired them)
    7. Run: `roots run process-id --work-item '{"test": true}'` — execute the process
    8. Verify: process completes successfully
    9. Override: `roots config set process-id nodes.gate.config.confidence_threshold 0.99`
    10. Verify: the override is persisted
  - This is the "smoke test" for the entire packaging feature
- Also create `examples/packaging/` with:
  - A sample Root package (pre-built `.root` file)
  - A script that installs and runs it
  - README explaining the packaging workflow

**Acceptance Criteria:**
- [ ] End-to-end test passes: pack → inspect → install → run → configure
- [ ] Process executes successfully with default agents
- [ ] Config override persists and affects execution
- [ ] Example package and script exist in examples/packaging/
- [ ] Test uses in-memory SQLite (fast, hermetic)
- [ ] Test is self-contained (creates temp files, cleans up)

## Out of Scope

- Package signing (cryptographic signatures beyond checksum)
- Registry/marketplace integration
- Automatic agent implementation generation via AI
- Version migration between package versions
- Dependency resolution between packages
- Sandboxing of default agent execution

## Technical Considerations

- **Security:** Loading Python code from packages is the biggest risk. The `--apply-defaults` flag being opt-in is the primary mitigation. Future work: package signing, sandboxing, or WebAssembly-based agent execution.
- Default agents are written to a temp directory and imported dynamically — clean up on close. Use `importlib.util.spec_from_file_location` for isolated import.
- Configuration overrides modify the ProcessDefinition in storage — they're persistent, not session-scoped.
- The README stored in metadata could be large — consider a size limit (e.g., 100KB max).
- Config templates are manifest-level, not stored separately — they travel with the package.

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
