# ENV_REFERENCE.md

> Last updated: 2026-06-14
> Updated by: Claude

Environment variables used by the Roots framework.

---

## Quick Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `ROOTS_API_KEY` | No | When set, the HTTP API requires a matching `X-API-Key` header |
| `ROOTS_POSTGRES_DSN` | No | PostgreSQL connection string |
| `ROOTS_LLM_BASE_URL` | No | LLM API base URL |
| `ROOTS_LLM_API_KEY` | No | LLM API key |
| `OPENAI_API_KEY` | No | Fallback LLM API key |

No `.env` file is required for basic usage. The framework runs with SQLite and without LLM features by default.

---

## Optional Variables

| Variable | Description | Default | Notes |
|----------|-------------|---------|-------|
| `ROOTS_API_KEY` | API key required on all HTTP data routes when set | None (API unauthenticated) | Clients must send `X-API-Key: <key>`; `/` and `/health` stay open. Recommended whenever binding a non-local host. |
| `ROOTS_POSTGRES_DSN` | PostgreSQL connection string for storage backend | None (uses SQLite) | Format: `postgresql://user:pass@host:port/dbname` |
| `ROOTS_LLM_BASE_URL` | Base URL for LLM API requests | OpenAI API (`https://api.openai.com/v1`) | Any OpenAI-compatible endpoint works |
| `ROOTS_LLM_API_KEY` | API key for LLM requests | None | Takes precedence over `OPENAI_API_KEY` |
| `OPENAI_API_KEY` | Fallback API key for LLM requests | None | Used if `ROOTS_LLM_API_KEY` is not set |

---

## Variable Details

### ROOTS_POSTGRES_DSN

PostgreSQL connection string. When set, enables the PostgreSQL storage backend. When unset, Roots defaults to SQLite.

```bash
# Standard format
export ROOTS_POSTGRES_DSN="postgresql://user:password@localhost:5432/roots"

# With SSL
export ROOTS_POSTGRES_DSN="postgresql://user:password@host:5432/roots?sslmode=require"
```

**Used by:** Storage backend selection, PostgreSQL tests (tests are skipped when unset)

### ROOTS_LLM_BASE_URL

Base URL for the LLM API. The custom LLM shim uses OpenAI-compatible API format, so any provider with an OpenAI-compatible endpoint works.

```bash
# Default (OpenAI)
export ROOTS_LLM_BASE_URL="https://api.openai.com/v1"

# Local model (e.g., Ollama)
export ROOTS_LLM_BASE_URL="http://localhost:11434/v1"

# Other OpenAI-compatible providers
export ROOTS_LLM_BASE_URL="https://api.together.xyz/v1"
```

**Used by:** LLM shim for AI decision nodes

### ROOTS_LLM_API_KEY

API key sent in the `Authorization: Bearer` header for LLM requests. Takes precedence over `OPENAI_API_KEY`.

```bash
export ROOTS_LLM_API_KEY="sk-..."
```

**Used by:** LLM shim authentication

### OPENAI_API_KEY

Fallback API key for LLM requests. Used only when `ROOTS_LLM_API_KEY` is not set. Provided for convenience since many developers already have this set.

```bash
export OPENAI_API_KEY="sk-..."
```

**Used by:** LLM shim authentication (fallback)

---

## Example Configuration

### Minimal (SQLite, no LLM)

No environment variables needed. Roots runs with SQLite storage and LLM features disabled.

### With PostgreSQL

```bash
export ROOTS_POSTGRES_DSN="postgresql://roots:roots@localhost:5432/roots_dev"
```

### With LLM (OpenAI)

```bash
export OPENAI_API_KEY="sk-..."
```

### Full Configuration

```bash
# Storage
export ROOTS_POSTGRES_DSN="postgresql://roots:roots@localhost:5432/roots_dev"

# LLM
export ROOTS_LLM_BASE_URL="https://api.openai.com/v1"
export ROOTS_LLM_API_KEY="sk-..."
```

---

## Adding New Environment Variables

When adding a new environment variable:

1. Add it to this documentation with description and default
2. Use the `ROOTS_` prefix for all Roots-specific variables
3. Make it optional with a sensible default where possible
4. Update `docs/TROUBLESHOOTING.md` if misconfiguration causes confusing errors

---

## Troubleshooting

### LLM Requests Failing

1. Check that `ROOTS_LLM_API_KEY` or `OPENAI_API_KEY` is set
2. Verify `ROOTS_LLM_BASE_URL` points to a reachable endpoint
3. Test connectivity: `curl $ROOTS_LLM_BASE_URL/models -H "Authorization: Bearer $ROOTS_LLM_API_KEY"`

### PostgreSQL Tests Skipping

Set `ROOTS_POSTGRES_DSN` to a valid PostgreSQL connection string. See `docs/TROUBLESHOOTING.md` for details.
