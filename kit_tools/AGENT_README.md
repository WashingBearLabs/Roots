# AGENT_README.md

> Last updated: 2026-03-26
> Updated by: Claude

Navigation guide for AI assistants working in the Roots framework codebase. Defines read order, patterns to follow, and areas to avoid.

---

## Quick Start — Read Order

**For general orientation:**
1. **This file** — How to navigate the Roots codebase and update docs
2. **SYNOPSIS.md** — Roots framework overview: YAML-driven process orchestration with agent contracts
3. **arch/CODE_ARCH.md** — Module layout, orchestrator patterns, agent/storage abstractions
4. **arch/SERVICE_MAP.md** — LLM integrations, storage backends, what calls what

**For troubleshooting:**
5. **docs/TROUBLESHOOTING.md** — Common issues: YAML validation, PostgreSQL tests, pyright warnings
6. **docs/ENV_REFERENCE.md** — Environment variables for LLM and database configuration

**For making changes:**
7. **docs/GOTCHAS.md** — Known landmines: fork/join crash safety, expression evaluation quirks
8. **arch/DECISIONS.md** — Why YAML over code, why custom LLM shim over LiteLLM, etc.
9. **specs/*.md** — Active feature specs (check archive/ for completed ones)
10. **roadmap/*.md** — Milestones and backlog

---

## Session Start Checklist

Before starting any work session:

- [ ] Read `SYNOPSIS.md` to understand current project state
- [ ] Check `specs/*.md` for active feature specs
- [ ] Check `roadmap/MILESTONES.md` for milestone tracking
- [ ] Review `arch/CODE_ARCH.md` for structure and patterns
- [ ] Scan `docs/GOTCHAS.md` for relevant landmines
- [ ] Check `AUDIT_FINDINGS.md` for open findings
- [ ] Check `arch/DECISIONS.md` for recent architectural decisions

**For troubleshooting tasks, also read:**
- [ ] `docs/TROUBLESHOOTING.md` for debugging procedures
- [ ] `docs/ENV_REFERENCE.md` for environment variable reference

**Flag anything that looks like:**
- Security concerns → Document in `arch/SECURITY.md`
- Code that violates documented patterns
- Documentation that appears stale or inconsistent with code

---

## Session End Checklist

Before closing any session, use this checklist to prevent documentation drift:

### Always Update
| File | Update If... |
|------|--------------|
| `SESSION_LOG.md` | Always — log what was done this session |
| `specs/*.md` | Working on a feature — update acceptance criteria, add Implementation Notes |
| `roadmap/MILESTONES.md` | Milestone progress changed |

### Architecture Changes
| File | Update If... |
|------|--------------|
| `SYNOPSIS.md` | Project scope, status, or purpose changed |
| `arch/CODE_ARCH.md` | New modules, patterns, node types, or storage backends added |
| `arch/SERVICE_MAP.md` | New dependencies or integrations added |
| `arch/DATA_MODEL.md` | Schema changes, new tables, new relationships |
| `arch/DECISIONS.md` | A non-trivial "why" decision was made |

### Operations Changes
| File | Update If... |
|------|--------------|
| `docs/TROUBLESHOOTING.md` | Discovered new common issues or debug procedures |
| `docs/ENV_REFERENCE.md` | New environment variables or secrets |

### Reference Changes
| File | Update If... |
|------|--------------|
| `docs/GOTCHAS.md` | Discovered a landmine or tech debt worth noting |
| `testing/TESTING_GUIDE.md` | Testing approach changed, new test patterns |

---

## Patterns to Follow

### Code Organization
- Process orchestration code lives in `roots/core/`
- Agent implementations and contracts live in `roots/agents/`
- Storage backends (SQLite, PostgreSQL) live in `roots/storage/`
- YAML process definitions live alongside their consuming modules
- Demo applications live in `demos/`
- Root packaging code lives in `roots/packaging/`

### Naming Conventions
- **Files:** `snake_case.py` for all Python modules
- **Classes:** `PascalCase` (e.g., `ProcessEngine`, `AgentContract`)
- **Functions/methods:** `snake_case`
- **Environment variables:** `SCREAMING_SNAKE_CASE` with `ROOTS_` prefix
- **Process YAML keys:** `snake_case`

### Data Modeling
- **Pydantic for all models** — no raw dicts for structured data
- Input/output schemas on agent contracts are Pydantic models
- Process definitions are validated via Pydantic models loaded from YAML

### Async-First
- All I/O-bound operations use `async`/`await`
- Orchestrator tick loop is async
- Storage backends expose async interfaces
- Use `asyncio` — no threads for concurrency

### Expression Evaluation
- Use `simpleeval` for safe expression evaluation in process conditions
- Never use Python `eval()` or `exec()`

### Error Handling
- Custom exception classes for domain errors
- Orchestrator errors are logged and do not crash the tick loop
- Agent invocation errors are captured in process state

---

## Off-Limits / Requires Human Review

The following changes should be drafted but **not applied** without human approval:

- [ ] **Authentication/Authorization flows** — Not implemented yet; design decisions pending
- [ ] **Database migrations** — Manual SQL in storage backends; schema changes need careful review
- [ ] **Infrastructure changes** — No IaC in this project; deployment is manual
- [ ] **Dependency updates** — Major version bumps need testing (especially after LiteLLM supply chain incident)
- [ ] **Files marked `# HUMAN-REVIEW-REQUIRED`** — Explicitly flagged
- [ ] **Secrets or API keys** — Never commit; use environment variables
- [ ] **LLM shim changes** — Custom shim replaced LiteLLM for security reasons; changes need review

When in doubt, ask before applying.

---

## Documentation Structure

```
kit_tools/
├── AGENT_README.md          # This file - AI assistant guide
├── SYNOPSIS.md              # Project overview and current state
├── SESSION_LOG.md           # Development session history
├── AUDIT_FINDINGS.md        # Code quality validation findings
│
├── arch/                    # Architecture documentation
│   ├── CODE_ARCH.md         # Code structure, patterns, modules
│   ├── SERVICE_MAP.md       # Dependencies, integrations, topology
│   ├── DATA_MODEL.md        # Database schema
│   ├── SECURITY.md          # Auth, secrets, permissions
│   ├── DECISIONS.md         # Architectural decision records
│   └── patterns/            # Detailed pattern documentation
│
├── docs/                    # Operational documentation
│   ├── TROUBLESHOOTING.md   # Debugging guide
│   ├── ENV_REFERENCE.md     # Environment variables
│   ├── GOTCHAS.md           # Known issues and landmines
│   └── feature_guides/      # Feature-specific docs
│
├── specs/                   # Feature Specs
│   ├── feature-*.md         # Active feature specs
│   └── archive/             # Completed feature specs
│
├── testing/                 # Test documentation
└── roadmap/                 # Milestones and backlog
    ├── MILESTONES.md        # High-level milestone tracking
    └── BACKLOG.md           # Future work items
```

---

## File Naming Conventions

| Type | Pattern | Example |
|------|---------|---------|
| Feature specs | `feature-feature-name.md` (kebab-case) | `feature-root-packaging.md` |
| Feature guides | `FEATURENAME_FEATURE_GUIDE.md` | `PACKAGING_FEATURE_GUIDE.md` |
| Milestone tracking | `MILESTONES.md` | `MILESTONES.md` |
| Architecture patterns | `PATTERNNAME.md` | `ORCHESTRATOR.md` |
| Decision records | Date prefix in `DECISIONS.md` | `2026-03-23: YAML for process definitions` |

---

## Documentation Standards

### Required Metadata
Every documentation file should include at the top:

```markdown
# FILENAME.md
> Last updated: 2026-03-26
> Updated by: [Human/Claude]
```

### Cross-References
Link related documentation:
- Architectural decisions → `arch/DECISIONS.md`
- Environment variables → `docs/ENV_REFERENCE.md`
- Debugging → `docs/TROUBLESHOOTING.md`

### Staleness Indicator
If a doc hasn't been updated in 30+ days and the related code has changed, it should be flagged for review.

---

## How to Update This File

This `AGENT_README.md` should be updated when:
- New patterns are established
- New off-limits areas are identified
- The documentation structure changes
- New file naming conventions are adopted

Keep this file accurate — it's the source of truth for AI assistants working in this codebase.
