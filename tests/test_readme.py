"""Tests for Package README rendering — US-004."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from roots.agents.registry import AgentRegistry
from roots.packaging.archive import read_archive
from roots.packaging.installer import install_package
from roots.storage.sqlite import SqliteBackend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SIMPLE_PROCESS = """\
id: readme-test-process
name: README Test Process
version: "1.0.0"
description: A test process
nodes:
  - id: step1
    type: agent
    label: First Step
    config:
      agent: worker_agent
      output_key: step1_out
  - id: done
    type: end
    label: Complete
    config:
      status: completed
edges:
  - from: step1
    to: done
entry_point: step1
"""

_SAMPLE_README = """\
# Test Root

## What This Does
This is a test process.

## Required Agents
| Agent | Description |
|-------|-------------|
| worker_agent | Does work |

## Quick Start
1. Install it
2. Run it
"""


def _minimal_manifest_data(
    package_id: str = "test-org/readme-test",
    package_version: str = "1.0.0",
) -> dict[str, Any]:
    return {
        "package_id": package_id,
        "package_version": package_version,
        "name": "README Test",
        "description": "A test process",
        "roots_version": ">=0.1.0",
        "agent_contracts": [{"name": "worker_agent", "description": "Does work"}],
    }


def _build_archive(
    tmp_path: Path,
    manifest_data: dict[str, Any] | None = None,
    process_yaml: str = _SIMPLE_PROCESS,
    readme: str | None = _SAMPLE_README,
    name: str = "test.root",
) -> Path:
    process_bytes = process_yaml.encode()
    if manifest_data is None:
        manifest_data = _minimal_manifest_data()
    manifest_data.setdefault(
        "checksum", hashlib.sha256(process_bytes).hexdigest()
    )
    archive_path = tmp_path / name
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest_data, indent=2))
        zf.writestr("process.yaml", process_bytes)
        if readme is not None:
            zf.writestr("README.md", readme)
    return archive_path


@pytest.fixture
async def storage(tmp_path: Path) -> SqliteBackend:
    db_path = tmp_path / "test.db"
    backend = SqliteBackend(str(db_path))
    await backend.initialize()
    return backend


# ---------------------------------------------------------------------------
# README extraction from archive
# ---------------------------------------------------------------------------


class TestReadmeFromArchive:
    def test_readme_present_in_archive(self, tmp_path: Path):
        archive = _build_archive(tmp_path, readme=_SAMPLE_README)
        _manifest, contents = read_archive(archive)

        assert "README.md" in contents
        assert contents["README.md"].decode("utf-8") == _SAMPLE_README

    def test_readme_absent_from_archive(self, tmp_path: Path):
        archive = _build_archive(tmp_path, readme=None)
        _manifest, contents = read_archive(archive)

        assert "README.md" not in contents


# ---------------------------------------------------------------------------
# README stored in process metadata during install
# ---------------------------------------------------------------------------


class TestReadmeStoredDuringInstall:
    @pytest.mark.asyncio
    async def test_readme_stored_in_metadata(
        self, tmp_path: Path, storage: SqliteBackend
    ):
        registry = AgentRegistry()
        archive = _build_archive(tmp_path, readme=_SAMPLE_README)
        await install_package(archive, storage, registry)

        process = await storage.get_process("readme-test-process")
        assert process is not None
        assert process.metadata.get("readme") == _SAMPLE_README

    @pytest.mark.asyncio
    async def test_no_readme_stored_when_absent(
        self, tmp_path: Path, storage: SqliteBackend
    ):
        registry = AgentRegistry()
        archive = _build_archive(tmp_path, readme=None)
        await install_package(archive, storage, registry)

        process = await storage.get_process("readme-test-process")
        assert process is not None
        assert "readme" not in process.metadata


# ---------------------------------------------------------------------------
# CLI readme command (integration-style via function calls)
# ---------------------------------------------------------------------------


class TestReadmeDisplay:
    def test_readme_from_archive_file(self, tmp_path: Path):
        """roots packages readme package.root displays README from archive."""
        archive = _build_archive(tmp_path, readme=_SAMPLE_README)

        # Simulate what the CLI does: read archive and check for README
        _manifest, contents = read_archive(archive)
        assert "README.md" in contents
        readme_text = contents["README.md"].decode("utf-8")
        assert "# Test Root" in readme_text
        assert "## What This Does" in readme_text

    @pytest.mark.asyncio
    async def test_readme_from_installed_process(
        self, tmp_path: Path, storage: SqliteBackend
    ):
        """roots packages readme <process-id> displays README from installed process."""
        registry = AgentRegistry()
        archive = _build_archive(tmp_path, readme=_SAMPLE_README)
        await install_package(archive, storage, registry)

        process = await storage.get_process("readme-test-process")
        assert process is not None
        readme_text = process.metadata.get("readme")
        assert readme_text is not None
        assert "# Test Root" in readme_text

    def test_missing_readme_in_archive_graceful(self, tmp_path: Path):
        """Missing README handled gracefully — no error raised."""
        archive = _build_archive(tmp_path, readme=None)
        _manifest, contents = read_archive(archive)
        # Should not raise, just not contain README
        assert "README.md" not in contents

    @pytest.mark.asyncio
    async def test_missing_readme_in_process_graceful(
        self, tmp_path: Path, storage: SqliteBackend
    ):
        """Missing README in installed process handled gracefully."""
        registry = AgentRegistry()
        archive = _build_archive(tmp_path, readme=None)
        await install_package(archive, storage, registry)

        process = await storage.get_process("readme-test-process")
        assert process is not None
        # No readme key — graceful absence
        assert process.metadata.get("readme") is None
