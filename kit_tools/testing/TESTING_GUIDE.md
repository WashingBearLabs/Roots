<!-- Template Version: 2.0.0 -->
<!-- Seeding:
  explorer_focus: testing, tech-stack
  required_sections:
    - "Quick Start"
    - "Test Structure"
  skip_if: no-tests
-->
# TESTING_GUIDE.md

> **TEMPLATE_INTENT:** Document test structure, running tests, and writing new tests. Quality assurance reference.

> Last updated: 2026-06-13
> Updated by: Claude

## Quick Start

```bash
# Run all tests
pytest tests/

# Run a specific test file
pytest tests/test_sqlite.py -v

# Run tests matching a keyword
pytest -k fork_join

# Run with coverage
pytest tests/ --cov=roots
```

---

## Testing Strategy

| Type | Coverage | Tools |
|------|----------|-------|
| Unit | Core logic, schema validation, decision engine, state machine | pytest, pytest-asyncio |
| Integration | Storage backends (SQLite, PostgreSQL), API routers, agent invocation | pytest, pytest-asyncio, AsyncMock |
| End-to-end | Full process execution through orchestrator tick loop | pytest, pytest-asyncio |

---

## Test Structure

```
tests/                          # 75 test files, 1,716 tests
├── conftest.py                 # Shared fixtures (see below)
├── test_sqlite.py              # SQLite storage backend tests
├── test_postgres.py            # PostgreSQL storage tests (auto-skipped without DSN)
├── test_orchestrator.py        # Orchestrator tick loop tests
├── test_decision.py            # Decision engine (4 modes) tests
├── test_schema.py              # Pydantic model validation tests
├── test_agents.py              # Agent registry and invocation tests
├── test_events.py              # Event emitter and sink tests
├── test_webhooks.py            # Webhook delivery tests
├── test_api_*.py               # FastAPI router tests
├── test_packaging_*.py         # .root archive and manifest tests
└── ...                         # Additional test files
```

---

## Key Fixtures (conftest.py)

| Fixture | Scope | Purpose |
|---------|-------|---------|
| `sqlite_storage` | function | Fresh SQLite storage backend for each test |
| `storage` | function | Parameterized fixture — runs tests against both SQLite and PostgreSQL (when available) |
| `sample_process` | function | A minimal valid process definition for testing |
| `roots_instance` | function | Fully wired `Roots` instance with in-memory storage |

---

## Configuration

The project uses `asyncio_mode="auto"` globally, so all async test functions are automatically recognized — no need for `@pytest.mark.asyncio` decorators.

This is configured in `pyproject.toml`:
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

---

## Writing Tests

### Example: Testing a Storage Operation

```python
from __future__ import annotations

import pytest
from roots.core.schema import ProcessDefinition


async def test_save_and_load_process(sqlite_storage):
    process = ProcessDefinition(name="test", nodes=[], edges=[])
    await sqlite_storage.save_process(process)

    loaded = await sqlite_storage.get_process(process.id)
    assert loaded is not None
    assert loaded.name == "test"
```

### Example: Testing with Mocked Agents

```python
from __future__ import annotations

from unittest.mock import AsyncMock


async def test_agent_invocation(roots_instance):
    mock_agent = AsyncMock(return_value={"result": "ok"})
    roots_instance.agents.register("test-agent", mock_agent)

    # Run a process that invokes test-agent
    # ...
    mock_agent.assert_called_once()
```

### Example: Testing Events with CollectorSink

```python
from __future__ import annotations

from roots.events.sinks import CollectorSink


async def test_event_emission(roots_instance):
    sink = CollectorSink()
    roots_instance.events.add_sink(sink)

    # Trigger some action that emits events
    # ...

    assert len(sink.events) > 0
    assert sink.events[0].type == "run.started"
```

---

## PostgreSQL Tests

PostgreSQL tests require the `ROOTS_POSTGRES_DSN` environment variable to be set. Without it, these tests are automatically skipped.

```bash
# Enable PostgreSQL tests
export ROOTS_POSTGRES_DSN="postgresql://user:pass@localhost:5432/roots_test"
pytest tests/

# Run only PostgreSQL tests
pytest tests/test_postgres.py -v
```

---

## Mock Patterns

| What to Mock | How | When |
|--------------|-----|------|
| HTTP agents | `AsyncMock` returning expected response dict | Testing agent invocation without network |
| LLM calls | `AsyncMock` returning structured decision output | Testing LLM-based decision mode |
| Event sinks | `CollectorSink` instance | Asserting on emitted events |
| Storage | Use `sqlite_storage` fixture (real but ephemeral) | Most tests use real storage, not mocks |

---

## Running Tests in CI

Tests are run with `pytest tests/` in CI. PostgreSQL tests run when `ROOTS_POSTGRES_DSN` is available in the CI environment; otherwise they are skipped. Type checking is run separately via `pyright roots/`.
