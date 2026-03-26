"""Tests for roots.packaging.pack — high-level pack_process function."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from roots.packaging.pack import pack_process


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SIMPLE_PROCESS = """\
id: test-process
name: Test Process
version: "2.0.0"
description: A test process for packing
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


def _write_process(tmp_path: Path) -> Path:
    p = tmp_path / "process.yaml"
    p.write_text(_SIMPLE_PROCESS)
    return p


# ---------------------------------------------------------------------------
# pack_process
# ---------------------------------------------------------------------------

class TestPackProcess:
    def test_creates_root_file(self, tmp_path: Path):
        process_file = _write_process(tmp_path)
        out = tmp_path / "out.root"

        result = pack_process(process_file, output_path=out)

        assert result == out
        assert out.exists()
        assert zipfile.is_zipfile(out)

    def test_default_output_name(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        process_file = _write_process(tmp_path)
        monkeypatch.chdir(tmp_path)

        result = pack_process(process_file)

        assert result.name == "test-process-2.0.0.root"
        assert result.exists()

    def test_archive_has_correct_structure(self, tmp_path: Path):
        process_file = _write_process(tmp_path)
        out = tmp_path / "out.root"

        pack_process(process_file, output_path=out)

        with zipfile.ZipFile(out, "r") as zf:
            names = zf.namelist()
            assert "manifest.json" in names
            assert "process.yaml" in names

    def test_agent_contracts_extracted(self, tmp_path: Path):
        process_file = _write_process(tmp_path)
        out = tmp_path / "out.root"

        pack_process(process_file, output_path=out)

        with zipfile.ZipFile(out, "r") as zf:
            manifest = json.loads(zf.read("manifest.json"))

        contracts = manifest["agent_contracts"]
        assert len(contracts) == 1
        assert contracts[0]["name"] == "worker_agent"

    def test_version_flag_overrides(self, tmp_path: Path):
        process_file = _write_process(tmp_path)
        out = tmp_path / "out.root"

        pack_process(process_file, output_path=out, version="3.0.0")

        with zipfile.ZipFile(out, "r") as zf:
            manifest = json.loads(zf.read("manifest.json"))

        assert manifest["package_version"] == "3.0.0"

    def test_author_flag_populates_manifest(self, tmp_path: Path):
        process_file = _write_process(tmp_path)
        out = tmp_path / "out.root"

        pack_process(process_file, output_path=out, author="Jane Doe")

        with zipfile.ZipFile(out, "r") as zf:
            manifest = json.loads(zf.read("manifest.json"))

        assert manifest["author"] == "Jane Doe"

    def test_description_flag_overrides(self, tmp_path: Path):
        process_file = _write_process(tmp_path)
        out = tmp_path / "out.root"

        pack_process(process_file, output_path=out, description="Custom desc")

        with zipfile.ZipFile(out, "r") as zf:
            manifest = json.loads(zf.read("manifest.json"))

        assert manifest["description"] == "Custom desc"

    def test_description_defaults_to_process(self, tmp_path: Path):
        process_file = _write_process(tmp_path)
        out = tmp_path / "out.root"

        pack_process(process_file, output_path=out)

        with zipfile.ZipFile(out, "r") as zf:
            manifest = json.loads(zf.read("manifest.json"))

        assert manifest["description"] == "A test process for packing"

    def test_include_defaults_bundles_directory(self, tmp_path: Path):
        process_file = _write_process(tmp_path)
        defaults_dir = tmp_path / "my_defaults"
        defaults_dir.mkdir()
        (defaults_dir / "config.yaml").write_text("key: value")
        (defaults_dir / "data.json").write_text('{"a": 1}')
        out = tmp_path / "out.root"

        pack_process(process_file, output_path=out, include_defaults=str(defaults_dir))

        with zipfile.ZipFile(out, "r") as zf:
            names = zf.namelist()
            assert "defaults/config.yaml" in names
            assert "defaults/data.json" in names
            manifest = json.loads(zf.read("manifest.json"))

        assert manifest["has_defaults"] is True

    def test_manifest_has_checksum(self, tmp_path: Path):
        process_file = _write_process(tmp_path)
        out = tmp_path / "out.root"

        pack_process(process_file, output_path=out)

        with zipfile.ZipFile(out, "r") as zf:
            manifest = json.loads(zf.read("manifest.json"))

        assert manifest["checksum"] is not None
        assert len(manifest["checksum"]) == 64  # SHA-256 hex

    def test_manifest_package_id_from_process(self, tmp_path: Path):
        process_file = _write_process(tmp_path)
        out = tmp_path / "out.root"

        pack_process(process_file, output_path=out)

        with zipfile.ZipFile(out, "r") as zf:
            manifest = json.loads(zf.read("manifest.json"))

        assert manifest["package_id"] == "test-process"
        assert manifest["name"] == "Test Process"

    def test_version_defaults_to_process_version(self, tmp_path: Path):
        process_file = _write_process(tmp_path)
        out = tmp_path / "out.root"

        pack_process(process_file, output_path=out)

        with zipfile.ZipFile(out, "r") as zf:
            manifest = json.loads(zf.read("manifest.json"))

        assert manifest["package_version"] == "2.0.0"


# ---------------------------------------------------------------------------
# Programmatic API via Roots class
# ---------------------------------------------------------------------------

class TestRootsPackProcess:
    def test_roots_pack_process_method(self, tmp_path: Path):
        """Roots.pack_process delegates to packaging.pack.pack_process."""
        process_file = _write_process(tmp_path)
        out = tmp_path / "out.root"

        from roots import Roots
        from roots.storage.sqlite import SqliteBackend

        import asyncio

        async def _setup() -> Roots:
            backend = SqliteBackend(str(tmp_path / "test.db"))
            await backend.initialize()
            return Roots(storage=backend)

        roots = asyncio.run(_setup())
        result = roots.pack_process(str(process_file), output_path=str(out))

        assert Path(result).exists()
        assert zipfile.is_zipfile(result)
