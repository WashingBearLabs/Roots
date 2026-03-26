<!-- Template Version: 2.0.0 -->
<!-- Seeding:
  explorer_focus: tech-stack, infrastructure
  required_sections:
    - "Prerequisites"
    - "Quick Start"
  skip_if: never
-->
# LOCAL_DEV.md

> **TEMPLATE_INTENT:** Complete local development setup guide. Get a new developer running quickly.

> Last updated: 2026-03-26
> Updated by: Claude

---

## Prerequisites

| Requirement | Version | Installation |
|-------------|---------|--------------|
| Python | 3.12+ | [python.org](https://www.python.org/downloads/) or `brew install python@3.12` |
| pip | Latest | Bundled with Python |

### Optional but Recommended

- **PostgreSQL**: Only needed if you want to test with PostgreSQL storage instead of the default SQLite
- **pyright**: For type checking (`pip install pyright` or use the VS Code Pylance extension)

---

## Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/WashingBearLabs/Roots.git
cd Roots

# 2. Install dependencies (dev extras include test/lint tools)
pip install -e ".[dev]"

# 3. Start the API server
roots serve

# Or run all demos (opens browser to localhost:8200)
python demo/run_all.py
```

After these steps, the API should be available at: `http://localhost:8200`

---

## Detailed Setup

### 1. Environment Configuration

No `.env` file is required for basic usage. The framework runs with sensible defaults out of the box.

**Optional environment variables:**

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROOTS_POSTGRES_DSN` | (unset) | PostgreSQL connection string; when set, uses PostgreSQL instead of SQLite |
| `ROOTS_DB_PATH` | `roots.db` | Path to the SQLite database file |

### 2. Database Setup

**SQLite (default):** No setup required. A `roots.db` file is created automatically in the working directory on first run.

**PostgreSQL (optional):**

```bash
# Set the DSN to use PostgreSQL
export ROOTS_POSTGRES_DSN="postgresql://user:pass@localhost:5432/roots"

# Tables are created automatically on first connection
```

### 3. Install Dependencies

```bash
# Production dependencies only
pip install -e .

# Development dependencies (pytest, pyright, ruff, etc.)
pip install -e ".[dev]"
```

### 4. Start the Application

```bash
# Start the API server
roots serve

# Or run programmatically
python -c "from roots import Roots; import asyncio; asyncio.run(Roots().serve())"
```

---

## Running with Docker

Docker is not required and no Docker configuration is provided. Roots runs directly on Python 3.12+ with no containerization needed for local development.

---

## Mocking External Services

### Remote HTTP Agents

Tests use `AsyncMock` to mock HTTP agent calls. No external services need to be running for the test suite.

### LLM Integrations

LLM-based decision modes are mocked with `AsyncMock` in tests. No API keys are required for development or testing.

---

## Test Data

### Demo Processes

```bash
# Run all 5 demo applications (serves UI at localhost:8200)
python demo/run_all.py
```

The demos create sample YAML process definitions and execute them, providing a good starting point for understanding the framework.

---

## Common Local Development Tasks

### Reset Database

```bash
# SQLite: just delete the file
rm roots.db

# PostgreSQL: drop and recreate
dropdb roots && createdb roots
```

### Run Tests

```bash
# All tests
pytest tests/

# Specific test file
pytest tests/test_sqlite.py -v

# Filter by test name
pytest -k fork_join

# With coverage
pytest tests/ --cov=roots
```

### Linting / Formatting

```bash
# Type checking
pyright roots/

# Lint check
ruff check roots/

# Auto-fix lint issues
ruff check roots/ --fix
```

---

## Troubleshooting Local Setup

### Import errors after install

**Symptom:** `ModuleNotFoundError: No module named 'roots'`

**Cause:** Package not installed in editable mode.

**Fix:**
```bash
pip install -e ".[dev]"
```

---

### PostgreSQL tests skipped

**Symptom:** PostgreSQL-related tests show as skipped.

**Cause:** The `ROOTS_POSTGRES_DSN` environment variable is not set. Tests auto-skip when PostgreSQL is unavailable.

**Fix:**
```bash
export ROOTS_POSTGRES_DSN="postgresql://user:pass@localhost:5432/roots_test"
pytest tests/
```

---

### Pyright errors on third-party libraries

**Symptom:** Pyright reports type errors in `simpleeval` or `asyncpg` imports.

**Cause:** These libraries lack complete type stubs. This is expected.

**Fix:**
Use `# type: ignore` comments on the specific import lines. This is the project convention.

---

## IDE Setup

### VS Code

Recommended extensions:
- **Pylance** — Python language server with pyright type checking
- **Ruff** — Fast Python linter integration
- **Python** — Microsoft Python extension

Workspace settings (`.vscode/settings.json`):
```json
{
  "python.analysis.typeCheckingMode": "strict",
  "python.analysis.diagnosticSeverityOverrides": {},
  "editor.formatOnSave": true,
  "ruff.enable": true
}
```

---

## Useful Local Commands

```bash
# Start the API server
roots serve

# Run all demos with browser UI
python demo/run_all.py

# Run the full test suite
pytest tests/

# Type check the codebase
pyright roots/

# Lint the codebase
ruff check roots/

# Install package in editable mode with dev extras
pip install -e ".[dev]"
```
