# Contributing to Roots

Thanks for your interest in improving Roots! This guide covers how to set up a dev
environment, the conventions the codebase follows, and how to get a change merged.

## Getting started

1. **Fork** the repository and clone your fork.
2. Create a virtual environment (Python **3.12+** required) and install with dev tooling:

   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -e ".[dev]"        # or:  uv pip install -e ".[dev]"
   ```

3. Create a branch for your work:

   ```bash
   git checkout -b feature/short-description
   ```

## Development workflow

Before opening a pull request, make sure all three checks pass locally:

```bash
# 1. Tests — must stay green
pytest

# 2. Type checking — the project is strict-pyright clean (0 errors)
pyright roots/

# 3. Lint / format
ruff check .
ruff format .
```

**PostgreSQL tests** are skipped automatically unless a live database is available.
To run them, set `ROOTS_POSTGRES_DSN` to a connection string before invoking `pytest`:

```bash
export ROOTS_POSTGRES_DSN="postgresql://localhost:5432/roots_test"
pytest tests/  # postgres-parametrized cases now run instead of skipping
```

New behavior should come with tests. Place them under `tests/` following the existing
naming (`test_<area>.py`).

## Coding conventions

These conventions are enforced by review and, where possible, by tooling:

- **`from __future__ import annotations`** at the top of every module.
- **Pydantic v2 for all structured data** — no raw dicts for models. Serialize with
  `model_dump(by_alias=True, mode="json")` (edge fields use aliases — see below).
- **Async-first** — all I/O uses `async`/`await`; use `asyncio`, not threads.
- **Naming** — `snake_case` for files/functions, `PascalCase` for classes,
  `SCREAMING_SNAKE_CASE` with a `ROOTS_` prefix for environment variables.
- **Safe evaluation only** — process conditions use `simpleeval`; never `eval()`/`exec()`.
- **Timestamps** — always `datetime.now(datetime.UTC)`, never `utcnow()`.
- **Line length** — 88 characters (`ruff` is configured for this).

A few landmines worth knowing before you touch the core:

- `EdgeDefinition` uses the field alias `from`/`from_node` — always serialize edges
  with `by_alias=True`.
- `NodeDefinition.config` is a union type; guard with `isinstance` before accessing
  type-specific fields.

See [`docs/INTEGRATION_GUIDE.md`](docs/INTEGRATION_GUIDE.md) for architecture context.

## Submitting a pull request

1. Make sure `pytest`, `pyright roots/`, and `ruff check .` all pass.
2. Write a clear PR description: what changed, why, and how you tested it.
3. Keep PRs focused — one logical change per PR is easier to review.
4. The `main` branch is protected (no force-pushes, no direct history rewrites);
   open your PR against `main` from a feature branch.

## Reporting bugs & requesting features

Open an issue with:

- **Bugs:** what you did, what you expected, what happened, and a minimal repro
  (a small process YAML + the steps to run it is ideal).
- **Features:** the problem you're trying to solve, not just the solution you have in
  mind — it helps us find the best fit for the framework.

## Security

Please do **not** open public issues for security vulnerabilities. See
[SECURITY.md](SECURITY.md) for how to report them privately.

## License

By contributing, you agree that your contributions will be licensed under the
[MIT License](LICENSE) that covers the project.
