<!-- Template Version: 2.0.0 -->
<!-- Seeding:
  explorer_focus: tech-stack, architecture
  required_sections:
    - "Code Style"
  skip_if: never
-->
# CONVENTIONS.md

> **TEMPLATE_INTENT:** Document coding standards and style guidelines. What 'good' looks like here.

> Last updated: 2026-03-26
> Updated by: Claude

## Code Style

### Python
- **Language version:** Python 3.12+
- **Type checker:** pyright in strict mode
- **Linter:** ruff
- **Models:** Pydantic v2 for all data models
- **Enumerations:** `StrEnum` for all enum types
- **Future annotations:** `from __future__ import annotations` required on every file
- **Async-first:** All I/O functions are async; use `asyncio.to_thread` for sync callables

---

## Serialization Rules

- Always use `model_dump(by_alias=True, mode="json")` when serializing Pydantic models
- This is critical because `EdgeDefinition` aliases `from_node` to `from` — omitting `by_alias=True` produces invalid data
- For datetime fields, `mode="json"` ensures proper ISO format serialization

---

## Datetime Convention

- Always use `datetime.now(datetime.UTC)` for current time
- Never use `datetime.utcnow()` — it is deprecated and returns a naive datetime
- All stored datetimes are UTC

---

## Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Files | snake_case | `event_emitter.py` |
| Classes | PascalCase | `ProcessRunner` |
| Functions | snake_case | `get_agent` |
| Constants | SCREAMING_SNAKE | `MAX_RETRIES` |
| Env vars | SCREAMING_SNAKE | `ROOTS_POSTGRES_DSN` |
| Pydantic models | PascalCase | `NodeDefinition` |
| Enums | PascalCase (StrEnum) | `DecisionMode` |

---

## Git Conventions

### Branch Naming
```
feature/short-description
fix/issue-description
chore/maintenance-task
```

### Commit Messages (Conventional Commits)
```
feat: add webhook ping endpoint
fix: correct edge serialization alias
chore: update dev dependencies
docs: add API guide to kit_tools
test: add fork/join coverage
refactor: extract decision engine from orchestrator
```

---

## Import Order

1. `from __future__ import annotations`
2. Standard library imports
3. Third-party imports
4. Local imports

---

## Type Checking Notes

- pyright is run in strict mode: `pyright roots/`
- Third-party libraries without full type stubs (`simpleeval`, `asyncpg`) use `# type: ignore` on import lines
- `NodeDefinition.config` is a union type — always use `isinstance` guards before accessing type-specific fields

---

## Testing Conventions

- Use `pytest` with `pytest-asyncio`
- `asyncio_mode="auto"` is set globally — no need for `@pytest.mark.asyncio` decorators
- Mock HTTP/LLM calls with `AsyncMock`
- Use `CollectorSink` for asserting on emitted events
- See `kit_tools/testing/TESTING_GUIDE.md` for full details
