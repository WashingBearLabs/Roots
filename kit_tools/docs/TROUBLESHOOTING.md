# TROUBLESHOOTING.md

> Last updated: 2026-03-26
> Updated by: Claude

Debugging procedures and common fixes for the Roots framework.

---

## Quick Diagnostics

When investigating an issue:

1. **Check test suite:** `pytest` from the repo root
2. **Check YAML validation:** Roots validates process YAML on load — look for validation errors in output
3. **Check environment:** Verify env vars are set (see `docs/ENV_REFERENCE.md`)
4. **Check Python version:** Roots requires Python 3.11+

---

## Common Issues & Solutions

### Process YAML Fails Validation

**Symptoms:**
- Pydantic validation error on process load
- Error message referencing schema mismatch in process definition

**Cause:**
Process YAML has structural issues. Most common problems:
- Fork node without matching join node (or vice versa)
- Decision node edges missing or referencing non-existent nodes
- Invalid expression syntax in conditions
- Missing required fields (node type, name, edges)

**Solution:**
1. Check fork/join pairing — every `fork` node must have a corresponding `join` node with matching scope
2. Verify decision edges — each edge must reference a valid target node and have a valid condition expression
3. Validate expressions — conditions must be valid `simpleeval` syntax (no Python imports or function definitions)
4. Check required fields against the process schema

**Prevention:**
Use the YAML validation tooling before running processes. Keep process definitions simple and test incrementally.

---

### Tests Skip PostgreSQL

**Symptoms:**
- PostgreSQL-related tests show as `SKIPPED` in pytest output
- SQLite tests pass but PostgreSQL coverage is missing

**Cause:**
The `ROOTS_POSTGRES_DSN` environment variable is not set. PostgreSQL tests are conditionally skipped when no connection string is available.

**Solution:**
```bash
# Set the PostgreSQL connection string
export ROOTS_POSTGRES_DSN="postgresql://user:password@localhost:5432/roots_test"

# Re-run tests
pytest
```

**Prevention:**
Add `ROOTS_POSTGRES_DSN` to your shell profile or `.env` file if you regularly need PostgreSQL test coverage.

---

### Pyright Shows Errors in Third-Party Imports

**Symptoms:**
- Pyright reports type errors on imports from third-party libraries
- Errors appear in CI or editor type checking

**Cause:**
Some third-party libraries lack complete type stubs. This is expected and has been downgraded to warnings in the pyright configuration.

**Solution:**
No action needed — these are expected warnings, not errors. The pyright config has been adjusted to treat these as non-blocking. If you see pyright errors on *Roots* code (not third-party imports), those should be investigated and fixed.

---

### Demo Server Won't Start

**Symptoms:**
- Demo application fails to start
- Address already in use error
- Import errors when launching demo

**Cause:**
Common causes:
- Port already in use by another process
- Running from wrong directory (imports fail)
- Missing dependencies

**Solution:**
```bash
# Check if port is in use (default demo port)
lsof -i :8000

# Kill the process using the port if needed
kill -9 <PID>

# Always run from the repo root
cd /path/to/Roots
python -m demos.<demo_name>
```

**Prevention:**
Always run demo applications from the repository root directory to ensure correct module resolution.

---

### Agent Invocation Fails with Schema Error

**Symptoms:**
- Agent invocation raises a Pydantic validation error
- Error references input or output schema mismatch
- Process halts at an agent node

**Cause:**
The agent's input or output does not match the contract schema. The process definition references a contract with specific input/output Pydantic models, and the agent implementation is producing data that doesn't conform.

**Solution:**
1. Check the agent contract's input schema — ensure the data flowing into the agent node matches
2. Check the agent contract's output schema — ensure the agent returns data matching the expected shape
3. Verify field names, types, and required/optional status match between contract and implementation
4. Look at the full Pydantic validation error for specific field-level mismatches

**Prevention:**
Write tests that exercise agent contracts with representative data. Use Pydantic's `model_validate` to test schemas independently.

---

## Database Issues

### PostgreSQL Connection Fails

```bash
# Test database connectivity
python -c "import asyncpg; import asyncio; asyncio.run(asyncpg.connect('$ROOTS_POSTGRES_DSN'))"
```

If connection fails:
- Verify PostgreSQL is running
- Check DSN format: `postgresql://user:password@host:port/dbname`
- Check network access (firewall, Docker networking)

### SQLite Lock Errors

**Symptoms:** Database locked errors during concurrent test runs.

**Solution:** SQLite doesn't support concurrent writes well. Run tests sequentially or use PostgreSQL for concurrent testing.

---

## Post-Incident

After resolving an issue:

- [ ] Update this file if it's a new common issue
- [ ] Update `docs/GOTCHAS.md` if relevant
- [ ] Consider adding a test case to prevent regression
