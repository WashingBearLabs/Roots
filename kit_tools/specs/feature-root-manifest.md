<!-- Template Version: 2.0.0 -->
---
feature: root-manifest
status: active
session_ready: true
depends_on: []
vision_ref: "Root Packaging — Manifest Format & Export"
type: epic-child
epic: root-packaging
epic_seq: 1
epic_final: false
created: 2026-03-24
updated: 2026-03-24
---

# Feature Spec: Root Manifest & Export

## Overview

Defines the `.root` package format and the tooling to create packages from existing processes. A Root package is a zip archive containing a process definition, agent contracts (input/output schemas without implementations), package metadata, and optional bundled files. The `roots pack` CLI command creates packages, and `roots inspect` shows what's inside.

## Goals

- Define a standardized, self-describing package format for Roots process definitions
- Extract agent contracts automatically from registered agents and process definitions
- Provide CLI tooling for creating and inspecting packages

## User Stories

### US-001: Root Manifest Schema

**Description:** As a framework developer, I want a manifest schema that describes everything a Root package contains and requires so that consumers know exactly what they're installing.

**Implementation Hints:**
- Create `roots/packaging/manifest.py` with Pydantic models:
  - `AgentContract(BaseModel)`:
    - `name: str` — agent name as referenced in the process YAML
    - `description: str | None` — what this agent does (human-readable)
    - `input_schema: dict[str, Any] | None` — JSON Schema for expected input
    - `output_schema: dict[str, Any] | None` — JSON Schema for expected output
    - `required: bool = True` — whether the process can function without this agent (some agents are optional in first_pass pools)
    - `timeout_seconds: int = 300` — recommended timeout
    - `tags: list[str] = []` — categorization (e.g., "security", "validation", "enrichment")
  - `ConfigOverride(BaseModel)`:
    - `path: str` — dot-notation path to the configurable value (e.g., "nodes.triage.config.confidence_threshold")
    - `description: str` — what this setting controls
    - `default_value: Any` — the value in the shipped process
    - `value_type: str` — "float", "int", "string", "bool"
    - `constraints: dict[str, Any] | None` — optional min/max/enum constraints
  - `RootManifest(BaseModel)`:
    - `format_version: str = "1.0"` — manifest schema version
    - `package_id: str` — unique package identifier (e.g., "washingbearlabs/incident-response")
    - `package_version: str` — semver (e.g., "1.0.0")
    - `name: str` — human-readable package name
    - `description: str` — what this Root does
    - `author: str | None` — author or organization
    - `license: str | None` — SPDX license identifier
    - `tags: list[str] = []` — package-level tags (e.g., "security", "soc", "compliance")
    - `roots_version: str` — minimum Roots framework version required (e.g., ">=0.1.0")
    - `process_file: str = "process.yaml"` — path to the process definition within the package
    - `agent_contracts: list[AgentContract]` — required and optional agents
    - `config_overrides: list[ConfigOverride] = []` — tunable parameters
    - `has_defaults: bool = False` — whether the package includes default agent implementations
    - `defaults_module: str | None = None` — Python module path for default agents (e.g., "defaults.agents")
    - `readme_file: str | None = "README.md"` — path to README within package
    - `checksum: str | None = None` — SHA-256 of the process.yaml file
- Create `roots/packaging/__init__.py` with exports

**Acceptance Criteria:**
- [x] `AgentContract` model with all fields and validation
- [x] `ConfigOverride` model with all fields
- [x] `RootManifest` model with all fields and validation
- [x] Manifest serializes to/from JSON cleanly (round-trip test)
- [x] `package_id` validated for format (e.g., "org/name" or simple "name")
- [x] `package_version` validated as semver
- [x] Tests cover model creation, validation, serialization

### US-002: Agent Contract Extraction

**Description:** As a package author, I want agent contracts automatically extracted from my process definition and registered agents so that I don't have to manually document every agent.

**Implementation Hints:**
- Create `roots/packaging/extractor.py`:
  - `extract_agent_contracts(process: ProcessDefinition, registry: AgentRegistry | None = None) -> list[AgentContract]`:
    - Walk all nodes in the process definition
    - For `agent` nodes: extract `config.agent` name
    - For `agent_pool` nodes: extract all names from `config.agents` list
    - Deduplicate agent names
    - For each agent name:
      - If registry is provided and agent is registered: pull `input_schema`, `output_schema`, `timeout_seconds`, and `metadata.get("description")` from the registration
      - If not registered: create a contract with the name and empty schemas (the author can fill these in later)
    - Mark agents as `required=True` by default. For `first_pass` pool mode, mark all but the first agent as `required=False` (they're fallbacks).
    - Return sorted list of AgentContracts
  - `extract_config_overrides(process: ProcessDefinition) -> list[ConfigOverride]`:
    - Walk all nodes and identify commonly-tunable parameters:
      - Decision nodes: `confidence_threshold`, `model`, `context_prompt`
      - Agent nodes with retry: `retry.max_attempts`, `retry.backoff_seconds`
      - Checkpoint nodes: `prompt`
      - Join nodes: `allow_partial`
    - Return as ConfigOverride list with the current values as defaults
    - Path format: `"nodes.{node_id}.config.{field}"` — maps directly to YAML structure

**Acceptance Criteria:**
- [x] Extracts all agent names from agent and agent_pool nodes
- [x] Deduplicates agents referenced multiple times
- [x] Pulls schemas from registry when available
- [x] Creates placeholder contracts for unregistered agents
- [x] first_pass pool agents (except first) marked as optional
- [x] Config overrides extracted for decision thresholds, retry settings, prompts
- [x] Override paths use consistent dot-notation format
- [x] Tests cover extraction from a multi-node process with pools

### US-003: Package Archive Format

**Description:** As a framework developer, I want a standardized archive format so that Root packages are self-contained, verifiable, and easy to distribute.

**Implementation Hints:**
- Create `roots/packaging/archive.py`:
  - The `.root` format is a **zip archive** with this structure:
    ```
    my-process.root (zip)
    ├── manifest.json        # RootManifest serialized
    ├── process.yaml         # The process definition
    ├── README.md            # Optional documentation
    ├── defaults/            # Optional default implementations
    │   ├── __init__.py
    │   └── agents.py        # Default agent callables
    └── config/              # Optional configuration templates
        └── overrides.yaml   # Override values template
    ```
  - `create_archive(manifest: RootManifest, process_path: Path, output_path: Path, extra_files: dict[str, Path] | None = None) -> Path`:
    - Creates a zip archive with the `.root` extension
    - Writes manifest.json (manifest.model_dump_json(indent=2))
    - Copies process.yaml from process_path
    - Copies README.md if it exists alongside the process
    - Copies defaults/ directory if it exists
    - Copies any extra_files (maps archive path → local path)
    - Computes SHA-256 of process.yaml and stores in manifest.checksum
    - Returns the output path
  - `read_archive(archive_path: Path) -> tuple[RootManifest, dict[str, bytes]]`:
    - Opens the zip, reads manifest.json, parses into RootManifest
    - Returns manifest + dict of all file contents keyed by archive path
    - Validates checksum if present
  - `list_archive_contents(archive_path: Path) -> list[str]`:
    - Returns list of file paths in the archive
- Use `zipfile.ZipFile` from stdlib — no extra dependencies

**Acceptance Criteria:**
- [x] `.root` files are valid zip archives
- [x] Archive contains manifest.json + process.yaml at minimum
- [x] `create_archive` writes a valid package
- [x] `read_archive` reads and validates a package
- [x] SHA-256 checksum verified on read
- [x] Extra files (README, defaults) included when present
- [x] Tests verify round-trip: create → read → compare

### US-004: `roots pack` CLI Command

**Description:** As a process author, I want a `roots pack` command so that I can export my process as a distributable Root package.

**Implementation Hints:**
- Add to `roots/cli/main.py` or create `roots/cli/packages.py`:
  - `roots pack <process-path> [--output <path>] [--version <semver>] [--author <name>] [--description <text>] [--include-defaults <dir>]`
  - Flow:
    1. Load the process YAML via `load_process_yaml(process_path)`
    2. Extract agent contracts via `extract_agent_contracts(process)`
    3. Extract config overrides via `extract_config_overrides(process)`
    4. Build a RootManifest with:
       - `package_id` derived from process.id (or user override)
       - `package_version` from --version flag or process.version
       - `name` from process.name
       - `description` from --description or process.description
       - Agent contracts and config overrides from extraction
    5. If `--include-defaults` provided: copy the directory into the archive
    6. Create the archive via `create_archive()`
    7. Output: `{process-id}-{version}.root`
  - Print summary: package name, version, N agent contracts, N config overrides, archive size
  - If a registered Roots instance is available (storage has agents), pull schemas from it. Otherwise, contracts have empty schemas.
- Also add a programmatic API: `Roots.pack_process(process_id, output_path, **kwargs) -> Path`

**Acceptance Criteria:**
- [ ] `roots pack examples/processes/simple-linear.yaml` creates a .root file
- [ ] Output file is a valid zip with correct structure
- [ ] Agent contracts extracted automatically from the process
- [ ] --version, --author, --description flags populate manifest
- [ ] --include-defaults bundles a directory into the package
- [ ] Summary printed with package info
- [ ] Programmatic API available on Roots class
- [ ] Tests verify pack creates valid archive

### US-005: `roots inspect` CLI Command

**Description:** As a consumer, I want a `roots inspect` command so that I can see what a Root package contains and what agents I need to provide before installing it.

**Implementation Hints:**
- `roots inspect <package-path>`:
  - Read the archive via `read_archive()`
  - Display formatted output:
    ```
    Root Package: incident-response v1.2.0
    Author: WashingBearLabs
    Description: SOC incident response and triage workflow
    License: MIT
    Tags: security, soc, incident-response

    Process: incident-response (12 nodes, 14 edges)
      Entry point: ingest
      Node types: 3 agent, 1 agent_pool, 1 decision, 1 checkpoint, 1 emit, 1 end, 1 fork, 1 join

    Required Agents (5):
      ✗ ingest_incident     — Normalizes raw incident data
        Input:  {source_ip: string, event_type: string, ...}
        Output: {normalized: object}
      ✗ threat_intel_lookup  — Enriches with threat intelligence
        ...

    Optional Agents (1):
      ✗ backup_responder    — Fallback if primary responder fails

    Configurable Parameters (3):
      nodes.triage.config.confidence_threshold  float  [0.0-1.0]  default: 0.75
      nodes.triage.config.model                 string             default: "gpt-4o-mini"
      nodes.respond.retry.max_attempts          int    [1-10]      default: 3

    Default Implementations: Yes (defaults/agents.py)
    README: Yes
    Checksum: SHA-256 verified ✓
    ```
  - Use `rich.Table` and `rich.Panel` for formatting
  - `--json` flag outputs raw manifest as JSON (for scripting)
  - Verify checksum and report pass/fail

**Acceptance Criteria:**
- [ ] `roots inspect package.root` shows formatted package summary
- [ ] Agent contracts listed with required/optional status
- [ ] Input/output schemas displayed in readable format
- [ ] Config overrides listed with types, constraints, and defaults
- [ ] Default implementations and README presence noted
- [ ] Checksum verification reported
- [ ] `--json` flag outputs raw manifest JSON
- [ ] Tests verify inspect output for a test package

### US-006: ProcessDefinition Metadata Extension

**Description:** As a framework developer, I want a top-level metadata field on ProcessDefinition so that package info can be stored alongside the process.

**Implementation Hints:**
- Add `metadata: dict[str, Any] = {}` field to `ProcessDefinition` in `roots/core/schema.py`
  - This matches the pattern already used on `NodeDefinition`
  - Default empty dict so it's backward-compatible with existing YAML files
  - Used by packaging to store: `package_id`, `package_version`, `installed_from`, `installed_at`
- Update `_serialize_process` in both storage backends to include the new field
- The field is persisted to storage but not validated beyond being a dict — it's an extensibility point
- Update `parse_process_dict` and `load_process_yaml` to accept and pass through metadata
- Existing processes without metadata continue to work (default empty dict)

**Acceptance Criteria:**
- [ ] `ProcessDefinition` has `metadata: dict[str, Any]` field
- [ ] Existing YAML files without metadata parse correctly (backward compatible)
- [ ] Metadata round-trips through storage (save → load → compare)
- [ ] Metadata preserved through YAML parse → serialize → reparse
- [ ] Tests verify backward compatibility and round-trip

## Out of Scope

- Registry/marketplace (separate future epic)
- Package signing (beyond checksum — cryptographic signing is future work)
- Dependency resolution between packages
- Automatic agent implementation generation
- Process migration between package versions

## Technical Considerations

- The `.root` format is intentionally simple (zip + JSON manifest). More complex formats can come later.
- Agent contracts are the key portability mechanism — they decouple process definitions from agent implementations
- ConfigOverride paths use dot-notation that maps to YAML structure, making it easy to apply overrides by walking the dict
- The `packaging/` module is new — doesn't modify existing modules except adding `metadata` to ProcessDefinition
- Checksum verification is SHA-256 on the process.yaml content, not the entire archive (so metadata changes don't invalidate it)

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
