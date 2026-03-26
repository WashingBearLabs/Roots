<!-- Template Version: 2.0.0 -->
---
feature: root-install
status: active
session_ready: true
depends_on: [root-manifest]
vision_ref: "Root Packaging — Import & Install"
type: epic-child
epic: root-packaging
epic_seq: 2
epic_final: false
created: 2026-03-24
updated: 2026-03-24
---

# Feature Spec: Root Import & Install

## Overview

The consumption side of Root packages. `roots install` loads a `.root` package, validates its structure, registers the process definition, checks which agent contracts are satisfied by the current environment, and reports what still needs wiring. Contract validation ensures that installed agents match the schemas declared in the manifest, catching integration mismatches at install time rather than at runtime.

## Goals

- Load and validate Root packages from `.root` archives
- Provide clear reporting of which agent contracts are satisfied and which need wiring
- Enable `roots install` as the primary consumption path for shared processes

## User Stories

### US-001: Package Loading and Validation

**Description:** As a framework developer, I want to load a Root package and validate its integrity so that corrupted or malformed packages are rejected early.

**Implementation Hints:**
- Create `roots/packaging/installer.py`:
  - `validate_package(archive_path: Path) -> list[str]`:
    - Read the archive via `read_archive()`
    - Validate manifest schema (RootManifest.model_validate)
    - Verify `process_file` exists in the archive
    - Verify process YAML parses correctly via `parse_process_dict()`
    - Run structural validation on the parsed process
    - Verify checksum if present
    - If `has_defaults` is True, verify `defaults_module` path exists in archive
    - Return list of error strings (empty = valid)
  - `load_package(archive_path: Path) -> tuple[RootManifest, ProcessDefinition, dict[str, bytes]]`:
    - Calls `validate_package` first — raises if errors
    - Parses the process YAML into ProcessDefinition
    - Returns manifest, process, and all archive contents
- Validation errors should be specific: "manifest.json: field 'package_version' is not valid semver", "process.yaml: Node 'triage' references unregistered agent 'threat_intel' (agent contract exists — will need to be wired)", etc.

**Acceptance Criteria:**
- [x] Valid packages pass validation with zero errors
- [x] Corrupted zip files produce clear error
- [x] Missing manifest.json produces clear error
- [x] Invalid manifest schema produces field-level errors
- [x] Missing process.yaml produces clear error
- [x] Invalid process YAML produces standard validation errors
- [x] Failed checksum produces clear error
- [x] Tests cover valid package, corrupted package, missing files, bad checksum

### US-002: Agent Contract Validation

**Description:** As a consumer, I want agent contracts validated against my registered agents so that I know which agents are wired correctly and which need attention.

**Implementation Hints:**
- Create function in `roots/packaging/installer.py`:
  - `validate_contracts(manifest: RootManifest, registry: AgentRegistry) -> ContractReport`:
  - `ContractReport(BaseModel)`:
    - `satisfied: list[ContractMatch]` — agents that match
    - `missing: list[AgentContract]` — required agents not registered
    - `optional_missing: list[AgentContract]` — optional agents not registered
    - `schema_mismatches: list[SchemaMismatch]` — agents registered but schemas don't match
    - `ready: bool` — True if all required agents satisfied with no schema mismatches
  - `ContractMatch(BaseModel)`:
    - `contract: AgentContract` — the expected contract
    - `registration: dict[str, Any]` — the actual registration
    - `schema_compatible: bool` — whether schemas are compatible
  - `SchemaMismatch(BaseModel)`:
    - `agent_name: str`
    - `direction: str` — "input" or "output"
    - `expected: dict | None` — schema from contract
    - `actual: dict | None` — schema from registration
    - `details: str` — human-readable mismatch description
  - Schema compatibility check: if the contract declares a schema and the registration has a schema, verify the registration's schema is a **superset** of the contract's schema (it accepts at least everything the contract says). If either side has no schema, it's a soft pass (warning, not error). Use jsonschema to compare.
  - A simple heuristic for compatibility: all `required` properties in the contract schema exist in the registration schema with compatible types.

**Acceptance Criteria:**
- [x] Registered agents matching contract name + schema → satisfied
- [x] Unregistered required agents → missing
- [x] Unregistered optional agents → optional_missing (not blocking)
- [x] Registered agent with incompatible schema → schema_mismatch
- [x] `ready` is True only when all required agents satisfied and no schema mismatches
- [x] Agents with no declared schema → soft pass (compatible by default)
- [x] Tests cover: all matched, some missing, schema mismatch, optional agents

### US-003: `roots install` CLI Command

**Description:** As a consumer, I want `roots install` to load a Root package, register its process, and report what I need to do next.

**Implementation Hints:**
- `roots install <package-path> [--force] [--apply-defaults]`:
  - Flow:
    1. Validate package via `validate_package()`
    2. Load package via `load_package()`
    3. Check if process ID already exists in storage. If yes and --force not set: error. If --force: overwrite.
    4. Save the process definition to storage via `storage.save_process(process)`
    5. Store package metadata on the process: `process.metadata["package_id"]`, `["package_version"]`, `["installed_at"]`, `["installed_from"]` (archive filename)
    6. If `--apply-defaults` and package has defaults: load and register default agents (see feature-root-defaults.md US-001)
    7. Run contract validation against currently registered agents
    8. Print installation report:
    ```
    Installed: incident-response v1.2.0

    Agent Status:
      ✓ ingest_incident      — Satisfied (local callable)
      ✓ threat_intel_lookup   — Satisfied (remote HTTP)
      ✗ geo_lookup            — MISSING — Enriches with geolocation data
      ✗ execute_response      — MISSING — Executes the chosen response action
      ~ backup_responder      — Optional, not registered

    Configurable Parameters:
      nodes.triage.config.confidence_threshold = 0.75  (override with roots config set ...)
      nodes.triage.config.model = "gpt-4o-mini"

    Next steps:
      1. Register missing agents: geo_lookup, execute_response
      2. Optionally configure parameters (see: roots config list incident-response)
      3. Run: roots run incident-response --work-item '{"source_ip": "..."}'
    ```
  - Exit code: 0 if installed (even with missing agents), 1 if validation failed
- Also add programmatic API: `Roots.install_package(archive_path, force=False, apply_defaults=False) -> ContractReport`

**Acceptance Criteria:**
- [x] `roots install package.root` loads process into storage
- [x] Existing process ID blocked without --force
- [x] --force overwrites existing process
- [x] Package metadata stored on the process definition
- [x] Contract report shows satisfied/missing/optional status
- [x] Next steps printed with specific agent names to register
- [x] Programmatic API available on Roots class
- [x] Tests verify install flow with and without pre-registered agents

### US-004: Configuration Override Application

**Description:** As a consumer, I want to apply configuration overrides after installing a Root so that I can tune thresholds, prompts, and models without modifying the process YAML.

**Implementation Hints:**
- Create `roots/packaging/config.py`:
  - `apply_override(process: ProcessDefinition, path: str, value: Any) -> ProcessDefinition`:
    - Parse the dot-notation path: `"nodes.triage.config.confidence_threshold"`
    - Walk the process structure to find the target field
    - Path components: `"nodes"` → look up node by ID (next component) → `"config"` → config field name
    - Validate the new value against the ConfigOverride constraints if available
    - Return a new ProcessDefinition with the override applied (don't mutate in place)
    - Raise `ConfigError` if path is invalid or value fails constraints
  - `apply_overrides_from_file(process: ProcessDefinition, overrides_path: Path) -> ProcessDefinition`:
    - Read a YAML file of overrides:
      ```yaml
      overrides:
        nodes.triage.config.confidence_threshold: 0.9
        nodes.triage.config.model: "gpt-4o"
        nodes.respond.retry.max_attempts: 5
      ```
    - Apply each override sequentially
    - Return the modified process
  - `list_overrides(manifest: RootManifest) -> list[ConfigOverride]`:
    - Returns the config overrides from the manifest with their current values, constraints, and descriptions
- Add CLI: `roots config list <process-id>` — shows available overrides for an installed process
- Add CLI: `roots config set <process-id> <path> <value>` — applies a single override and saves to storage
- Add CLI: `roots config apply <process-id> <overrides-file>` — applies overrides from YAML file

**Acceptance Criteria:**
- [ ] `apply_override` modifies the correct field in the process
- [ ] Invalid paths raise ConfigError with helpful message
- [ ] Value constraint validation works (min/max/enum)
- [ ] `apply_overrides_from_file` reads YAML and applies all overrides
- [ ] `roots config list` shows available overrides for installed process
- [ ] `roots config set` applies and persists a single override
- [ ] `roots config apply` applies overrides from file
- [ ] Tests verify override application on decision thresholds and retry settings

### US-005: Installed Package Tracking

**Description:** As an operator, I want to see which Root packages are installed and their status so that I can manage my environment.

**Implementation Hints:**
- Add CLI: `roots packages list` — shows installed packages:
  ```
  Installed Packages:
    incident-response  v1.2.0  (installed 2026-03-24)  2/4 agents wired
    content-review     v2.0.1  (installed 2026-03-20)  3/3 agents wired  ✓ Ready
  ```
  - Reads all processes from storage, filters those with `metadata.package_id`
  - Runs contract validation for each to show wiring status
- Add CLI: `roots packages status <package-id>` — detailed view:
  - Shows manifest info, agent wiring status, applied overrides, last run info
- Add CLI: `roots packages uninstall <package-id>` — removes the process from storage
  - Warns if there are active runs using this process
  - --force to skip the warning
- Add programmatic APIs to Roots class: `list_installed_packages()`, `get_package_status(package_id)`, `uninstall_package(package_id)`

**Acceptance Criteria:**
- [ ] `roots packages list` shows all installed packages with wiring status
- [ ] `roots packages status` shows detailed package info
- [ ] `roots packages uninstall` removes the process
- [ ] Uninstall warns about active runs
- [ ] Programmatic APIs available on Roots class
- [ ] Tests verify list, status, and uninstall flows

## Out of Scope

- Registry/marketplace integration
- Automatic agent discovery/download
- Package dependency resolution (package A requires package B)
- Process migration between package versions
- Rollback to previous package version

## Technical Considerations

- Contract validation uses jsonschema for schema compatibility — a registration schema is "compatible" if it accepts everything the contract schema requires. This is an approximation, not full JSON Schema subset checking.
- Override paths walk the ProcessDefinition structure which includes Pydantic models — the walker needs to handle both dict access and Pydantic model attribute access
- Installed packages are tracked via `process.metadata` — no new storage tables needed
- The `--apply-defaults` flag is a convenience for `roots install` that delegates to feature-root-defaults — it's a forward reference that becomes functional after spec 3

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
