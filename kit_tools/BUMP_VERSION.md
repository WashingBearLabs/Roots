<!-- Template Version: 2.0.0 -->
# BUMP_VERSION.md

> **TEMPLATE_INTENT:** Project-specific version bumping runbook. Read by `/kit-tools:bump-version` every time it runs. Tells the skill where the version lives and what extra steps are needed beyond the standard bump.

> Last updated: 2026-06-14
> Updated by: Claude

This runbook drives a **two-repo release**: it bumps the package version in the Roots
repo *and* updates the public docs + changelog on the WashingBearLabs site, so the site
never drifts from the released version. Run `/kit-tools:bump-version` (or
`/kit-tools:complete-implementation`) before every release.

---

## Version Source

The version has a single source of truth. `pyproject.toml` reads it dynamically
(`[tool.hatch.version] path = "roots/__init__.py"`), and `roots/api/app.py` imports it —
so **only one file holds the literal**.

| Field | Value |
|-------|-------|
| **File** | `roots/__init__.py` |
| **Format** | plain (Python) |
| **Field path** | `__version__ = "X.Y.Z"` |

Do **not** add the version literal anywhere else — `pyproject.toml` (dynamic) and
`roots/api/app.py` (`from roots import __version__`) both derive it from this file.

---

## Versioning Strategy

[Semantic Versioning](https://semver.org/). Roots is pre-1.0 (`0.x`, beta):

- **Minor** (`0.X.0`) — new features, node types, or other non-trivial additions.
- **Patch** (`0.0.X`) — bug fixes, security fixes, docs, internal cleanup.
- **Major** (`X.0.0`) — reserved for the 1.0 stabilization; breaking API changes until then are called out in the changelog under a **Breaking** heading.

---

## Changelog

The **public changelog lives on the WashingBearLabs site**, not in this repo (single
source of truth). It is updated as an Additional Version Location below.

| Field | Value |
|-------|-------|
| **File** | `../WashingBearLabs/apps/site/src/content/docs/roots-release-notes.md` |
| **Format** | Keep-a-Changelog style: `## X.Y.Z — YYYY-MM-DD` + summary + `### Added / Changed / Fixed` |

There is intentionally no `CHANGELOG.md` in this repo.

---

## Pre-Bump Steps

The release must be green before the version is bumped. Run from the repo root and stop
if any fail:

```bash
ruff check .
pyright roots/
pytest -q
```

Also confirm the working tree is clean (`git status`) and you are on `main` and in sync
with `origin/main`.

---

## Additional Version Locations

### Other files in this repo

None. `roots/api/app.py` and `pyproject.toml` both derive the version from
`roots/__init__.py` — never hard-code it in a second place.

### External repo — WashingBearLabs site

> Assumes the Washing Bear Labs site repo is a sibling clone at `../WashingBearLabs`.
> If it lives elsewhere, adjust the paths. These edits are committed and pushed in the
> site repo, separately from this repo (a push to its `main` branch deploys the live
> site).

| File | What to update |
|------|----------------|
| `../WashingBearLabs/apps/site/src/content/docs/roots.md` | Frontmatter `title: Roots (X.Y.Z)` → the new version. |
| `../WashingBearLabs/apps/site/src/content/docs/roots-release-notes.md` | Add a new `## X.Y.Z — YYYY-MM-DD` entry directly **below** the `[Back to Roots Docs]` link and its `---` divider (newest on top). Write a one-line summary, then `### Added` / `### Changed` / `### Fixed` sections describing what shipped in this release (derive from the merged feature specs, `EXECUTION_LOG.md`, and the git log since the last tag). |

After editing, validate the site still builds:

```bash
cd ../WashingBearLabs/apps/site && pnpm build    # runs `astro check && astro build`
```

---

## Post-Bump Steps

After all versions and the changelog are updated (in this repo) build and validate the
distribution:

```bash
rm -rf dist/
uv build
uvx twine check dist/*
```

**Publishing is a manual step** — it requires a PyPI API token that must not pass
through automation. The maintainer runs it themselves:

```bash
uv publish --token pypi-...        # or: export UV_PUBLISH_TOKEN=... && uv publish
```

PyPI metadata is immutable per version — never reuse a version number, and make sure the
changelog/version are correct **before** publishing.

---

## Commit Convention

Two commits in two repos:

### This repo (Roots)

| Field | Value |
|-------|-------|
| **Message format** | `release: vX.Y.Z` |
| **Tag** | `vX.Y.Z` (annotated) |
| **Branch** | Commit directly to `main` (protected — normal push only, no force-push). |

### WashingBearLabs repo (site docs + changelog)

| Field | Value |
|-------|-------|
| **Message format** | `docs: Roots vX.Y.Z release notes` |
| **Tag** | none |
| **Branch** | Commit and push to `main` (auto-deploys). Stage **only** the two `apps/site/src/content/docs/roots*.md` files — never `dist/`, `node_modules/`, or `.astro/`. |

---

> **Note:** This runbook is read by `/kit-tools:bump-version` each time the skill runs. Keep it current — stale instructions here mean missed steps on every release.
