<!-- Template Version: 2.0.0 -->
---
feature: cli
status: active
session_ready: true
depends_on: [http-api]
vision_ref: "T2.4 — CLI"
type: epic-child
epic: roots-v1
epic_seq: 10
epic_final: false
created: 2026-03-23
updated: 2026-03-23
---

# Feature Spec: CLI

## Overview

The Roots CLI (`roots`) provides command-line access to process validation, server startup, run management, and agent inspection. Built with Typer, it's useful for CI/CD integration, scripting, and quick interactions without writing Python code.

## Goals

- Provide a CLI that covers the most common Roots operations
- Support both embedded mode (direct execution) and server mode (via HTTP API)
- Enable CI/CD integration for process validation and run management

## User Stories

### US-001: CLI Scaffolding

**Description:** As a framework developer, I want the CLI entry point and basic structure so that all commands have a consistent home.

**Implementation Hints:**
- Create `roots/cli/main.py` with Typer app
- Main app with subcommands: `serve`, `validate`, `run`, `status`, `agents`
- Common options: `--storage` (sqlite path or postgres DSN, default `roots.db`), `--verbose` / `-v`
- The `pyproject.toml` already has `[project.scripts] roots = "roots.cli.main:app"` from T1.1
- Helper function to create a `Roots` instance from CLI options (storage type auto-detected from DSN format)

**Acceptance Criteria:**
- [x] `roots --help` shows all subcommands
- [x] `roots --version` shows version
- [x] Common options are available on all commands
- [x] Helper creates Roots instance from CLI options
- [x] Tests verify help output and option parsing

### US-002: `roots serve` Command

**Description:** As a platform operator, I want `roots serve` so that I can start the Roots HTTP API server from the command line.

**Implementation Hints:**
- Command: `roots serve --host 0.0.0.0 --port 8200 --storage roots.db`
- Creates Roots instance with configured storage
- Creates FastAPI app via `create_app(roots)`
- Starts uvicorn: `uvicorn.run(app, host=host, port=port)`
- Optional: `--reload` flag for development mode
- Print startup banner with URL and configured storage backend

**Acceptance Criteria:**
- [x] Server starts and listens on configured host/port
- [x] Storage backend is configured from --storage option
- [x] Startup banner shows URL and backend info
- [x] Ctrl+C shuts down gracefully
- [x] Tests verify server startup (can use short-lived background server)

### US-003: `roots validate` Command

**Description:** As a process author, I want `roots validate` so that I can check process definitions for errors from the command line.

**Implementation Hints:**
- Command: `roots validate <path>` where path is a YAML file or directory
- If directory: validate all `.yaml` / `.yml` files in it
- For each file: run full validation pipeline (YAML parse → Pydantic → structural)
- Output: green checkmark for valid files, red X with error details for invalid
- Exit code: 0 if all valid, 1 if any invalid (CI-friendly)
- Use `typer.echo` with color styling via `rich` (Typer supports this)

**Acceptance Criteria:**
- [x] Single file validation works
- [x] Directory validation finds and validates all YAML files
- [x] Errors include file path, node ID context, and field details
- [x] Exit code is 0 for all valid, 1 for any invalid
- [x] Tests verify valid file, invalid file, directory scanning

### US-004: `roots run` Command

**Description:** As a developer, I want `roots run` so that I can execute a process run from the command line for testing and scripting.

**Implementation Hints:**
- Command: `roots run <process-id-or-path> --work-item '{"key": "value"}' --wait`
- If argument is a file path: load and register the process first
- If argument is a process ID: look it up in storage
- `--work-item`: JSON string or path to JSON file (detect by checking if value is a file path)
- `--wait` (default True): block until run completes, print final status
- `--no-wait`: start run and print run ID, exit immediately
- Print events to stdout as they happen (use StdoutSink)
- Exit code: 0 if completed, 1 if failed, 2 if paused (hit checkpoint)

**Acceptance Criteria:**
- [x] Run executes with process from file path or storage ID
- [x] Work item accepted as JSON string or file path
- [x] `--wait` blocks until completion and prints result
- [x] `--no-wait` prints run ID and exits
- [x] Events printed during execution
- [x] Exit codes reflect run outcome
- [x] Tests verify end-to-end execution via CLI

### US-005: `roots status` and `roots agents` Commands

**Description:** As an operator, I want status and agents commands so that I can inspect the state of runs and registered agents.

**Implementation Hints:**
- `roots status` — list recent runs with their status
  - Options: `--process` (filter by process ID), `--status` (filter by status), `--limit` (default 20)
  - Table output: run_id, process_id, status, current_node, created_at, updated_at
  - Use `rich.table.Table` for formatted output
- `roots status <run_id>` — detailed view of a specific run
  - Show: run info, current position, work item state (truncated), recent history events
- `roots agents` — list registered agents
  - Table output: name, type, callback_url (if remote), registered_at
- `roots agents health` — check health of all remote agents
  - Ping each remote agent's callback_url, show healthy/unhealthy

**Acceptance Criteria:**
- [ ] `roots status` lists runs in a formatted table
- [ ] Status filters work (--process, --status)
- [ ] `roots status <run_id>` shows detailed run info
- [ ] `roots agents` lists all registered agents
- [ ] `roots agents health` pings remote agents
- [ ] Tests verify output formatting and filtering

## Out of Scope

- Interactive mode / REPL
- Process definition authoring via CLI
- Checkpoint resolution via CLI (use API for now)
- Configuration file support (roots.yaml) — use CLI args for v1

## Technical Considerations

- Typer handles argument parsing and help generation
- Use `asyncio.run()` to bridge sync CLI entry point to async Roots internals
- Rich integration via Typer gives nice formatting out of the box
- Exit codes should follow Unix conventions (0=success, 1=error, 2=special)

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
