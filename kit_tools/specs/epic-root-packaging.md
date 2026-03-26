<!-- Template Version: 2.0.0 -->
---
epic: root-packaging
status: completed
vision_ref: "Root Packaging & Registry — portable, versionable process packages"
created: 2026-03-24
updated: 2026-03-25
completed: 2026-03-25
---

# Epic: Root Packaging — Portable Process Packages

## Goal

Make Roots process definitions portable, versionable, and shareable. A "Root" (capital R) is a packaged process definition that includes the process graph, agent contracts (schemas without implementations), optional default agent implementations, configuration overrides, and metadata. Consumers can export their tuned processes as Root packages, share them, and others can install and wire up their own agent implementations.

The end state: `roots pack` exports a working process as a `.root` archive. `roots inspect` shows what it needs. `roots install` loads it and tells you which agents to wire up. Default implementations work out of the box for testing. Open-source security playbooks, compliance workflows, and operational procedures become installable, executable artifacts — not unread wiki pages.

## Decomposition

| Seq | Feature Spec | Stories | Status | Dependencies |
|-----|-------------|---------|--------|--------------|
| 1 | feature-root-manifest.md | 6 | Completed | None |
| 2 | feature-root-install.md | 5 | Completed | feature-root-manifest.md |
| 3 | feature-root-defaults.md | 5 | Completed | feature-root-install.md |

## Completion Criteria

- [x] `roots pack` exports a process as a `.root` archive with manifest and agent contracts
- [x] `roots inspect` shows package contents, required agents, and compatibility info
- [x] `roots install` loads a package, validates agent contracts, and reports what needs wiring
- [x] Default agent implementations bundled in a package work without additional configuration
- [x] Configuration overrides allow installers to customize thresholds, prompts, and models without modifying the process
- [x] Round-trip test: pack a process → install on a fresh Roots instance → run with defaults → passes

## Notes

- The `.root` format is a zip archive with a standardized structure (like `.whl` for Python packages)
- Agent contracts are the key innovation: they declare what agents a process needs (name, input/output schemas, description) without coupling to implementations
- This epic does NOT include a registry/marketplace — that's a separate future epic
- The existing ProcessDefinition, AgentRegistration, and validator infrastructure supports this with minimal changes
- ProcessDefinition needs a new top-level `metadata` field (currently missing — nodes have it but the process itself doesn't)
