"""Tests for roots.packaging.archive — create, read, list .root archives."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from roots.packaging.archive import create_archive, list_archive_contents, read_archive
from roots.packaging.manifest import RootManifest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_manifest_data() -> dict[str, Any]:
    return {
        "package_id": "test-org/my-process",
        "package_version": "1.0.0",
        "name": "My Process",
        "description": "A test process",
        "roots_version": ">=0.1.0",
        "agent_contracts": [
            {"name": "worker", "description": "Does work"},
        ],
    }


def _setup_process_dir(tmp_path: Path) -> Path:
    """Create a minimal process directory with process.yaml."""
    process_file = tmp_path / "process.yaml"
    process_file.write_text("nodes:\n  - id: start\n")
    return process_file


# ---------------------------------------------------------------------------
# create_archive
# ---------------------------------------------------------------------------

class TestCreateArchive:
    def test_creates_valid_zip(self, tmp_path: Path):
        process_file = _setup_process_dir(tmp_path)
        manifest = RootManifest(**_minimal_manifest_data())
        out = tmp_path / "my-process.root"

        result = create_archive(manifest, process_file, out)

        assert result == out
        assert out.exists()
        assert zipfile.is_zipfile(out)

    def test_contains_manifest_and_process(self, tmp_path: Path):
        process_file = _setup_process_dir(tmp_path)
        manifest = RootManifest(**_minimal_manifest_data())
        out = tmp_path / "my-process.root"

        create_archive(manifest, process_file, out)

        with zipfile.ZipFile(out, "r") as zf:
            names = zf.namelist()
            assert "manifest.json" in names
            assert "process.yaml" in names

    def test_manifest_has_checksum(self, tmp_path: Path):
        process_file = _setup_process_dir(tmp_path)
        manifest = RootManifest(**_minimal_manifest_data())
        out = tmp_path / "my-process.root"

        create_archive(manifest, process_file, out)

        with zipfile.ZipFile(out, "r") as zf:
            stored = json.loads(zf.read("manifest.json"))

        expected_checksum = hashlib.sha256(process_file.read_bytes()).hexdigest()
        assert stored["checksum"] == expected_checksum

    def test_includes_readme_when_present(self, tmp_path: Path):
        process_file = _setup_process_dir(tmp_path)
        (tmp_path / "README.md").write_text("# Hello")
        manifest = RootManifest(**_minimal_manifest_data())
        out = tmp_path / "my-process.root"

        create_archive(manifest, process_file, out)

        with zipfile.ZipFile(out, "r") as zf:
            assert "README.md" in zf.namelist()
            assert zf.read("README.md") == b"# Hello"

    def test_excludes_readme_when_absent(self, tmp_path: Path):
        process_file = _setup_process_dir(tmp_path)
        manifest = RootManifest(**_minimal_manifest_data())
        out = tmp_path / "my-process.root"

        create_archive(manifest, process_file, out)

        with zipfile.ZipFile(out, "r") as zf:
            assert "README.md" not in zf.namelist()

    def test_includes_defaults_directory(self, tmp_path: Path):
        process_file = _setup_process_dir(tmp_path)
        defaults = tmp_path / "defaults"
        defaults.mkdir()
        (defaults / "__init__.py").write_text("")
        (defaults / "agents.py").write_text("def handler(): pass")
        manifest = RootManifest(**_minimal_manifest_data())
        out = tmp_path / "my-process.root"

        create_archive(manifest, process_file, out)

        with zipfile.ZipFile(out, "r") as zf:
            names = zf.namelist()
            assert "defaults/__init__.py" in names
            assert "defaults/agents.py" in names

    def test_includes_config_directory(self, tmp_path: Path):
        process_file = _setup_process_dir(tmp_path)
        config = tmp_path / "config"
        config.mkdir()
        (config / "overrides.yaml").write_text("key: value")
        manifest = RootManifest(**_minimal_manifest_data())
        out = tmp_path / "my-process.root"

        create_archive(manifest, process_file, out)

        with zipfile.ZipFile(out, "r") as zf:
            assert "config/overrides.yaml" in zf.namelist()

    def test_includes_extra_files(self, tmp_path: Path):
        process_file = _setup_process_dir(tmp_path)
        extra = tmp_path / "extra.txt"
        extra.write_text("extra content")
        manifest = RootManifest(**_minimal_manifest_data())
        out = tmp_path / "my-process.root"

        create_archive(
            manifest, process_file, out, extra_files={"custom/extra.txt": extra}
        )

        with zipfile.ZipFile(out, "r") as zf:
            assert "custom/extra.txt" in zf.namelist()
            assert zf.read("custom/extra.txt") == b"extra content"


# ---------------------------------------------------------------------------
# read_archive
# ---------------------------------------------------------------------------

class TestReadArchive:
    def test_reads_manifest_and_contents(self, tmp_path: Path):
        process_file = _setup_process_dir(tmp_path)
        manifest = RootManifest(**_minimal_manifest_data())
        out = tmp_path / "my-process.root"
        create_archive(manifest, process_file, out)

        result_manifest, contents = read_archive(out)

        assert result_manifest.package_id == "test-org/my-process"
        assert "manifest.json" in contents
        assert "process.yaml" in contents

    def test_validates_checksum_success(self, tmp_path: Path):
        process_file = _setup_process_dir(tmp_path)
        manifest = RootManifest(**_minimal_manifest_data())
        out = tmp_path / "my-process.root"
        create_archive(manifest, process_file, out)

        # Should not raise
        result_manifest, _contents = read_archive(out)
        assert result_manifest.checksum is not None

    def test_validates_checksum_failure(self, tmp_path: Path):
        process_file = _setup_process_dir(tmp_path)
        manifest = RootManifest(**_minimal_manifest_data())
        out = tmp_path / "my-process.root"
        create_archive(manifest, process_file, out)

        # Tamper with process.yaml inside the archive
        tampered = tmp_path / "tampered.root"
        with zipfile.ZipFile(out, "r") as src, zipfile.ZipFile(
            tampered, "w"
        ) as dst:
            for name in src.namelist():
                data = src.read(name)
                if name == "process.yaml":
                    data = b"tampered content"
                dst.writestr(name, data)

        with pytest.raises(ValueError, match="Checksum mismatch"):
            read_archive(tampered)

    def test_preserves_file_contents(self, tmp_path: Path):
        process_file = _setup_process_dir(tmp_path)
        (tmp_path / "README.md").write_text("# Docs")
        manifest = RootManifest(**_minimal_manifest_data())
        out = tmp_path / "my-process.root"
        create_archive(manifest, process_file, out)

        _, contents = read_archive(out)
        assert contents["README.md"] == b"# Docs"
        assert contents["process.yaml"] == process_file.read_bytes()


# ---------------------------------------------------------------------------
# list_archive_contents
# ---------------------------------------------------------------------------

class TestListArchiveContents:
    def test_lists_files(self, tmp_path: Path):
        process_file = _setup_process_dir(tmp_path)
        (tmp_path / "README.md").write_text("# Hello")
        manifest = RootManifest(**_minimal_manifest_data())
        out = tmp_path / "my-process.root"
        create_archive(manifest, process_file, out)

        names = list_archive_contents(out)

        assert "manifest.json" in names
        assert "process.yaml" in names
        assert "README.md" in names


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_create_read_compare(self, tmp_path: Path):
        """Full round-trip: create archive, read it back, compare."""
        process_file = _setup_process_dir(tmp_path)
        (tmp_path / "README.md").write_text("# My Process")
        defaults = tmp_path / "defaults"
        defaults.mkdir()
        (defaults / "__init__.py").write_text("")
        (defaults / "agents.py").write_text("def run(): pass")

        original_data = _minimal_manifest_data()
        original_data["has_defaults"] = True
        original_data["defaults_module"] = "defaults.agents"
        manifest = RootManifest(**original_data)
        out = tmp_path / "my-process.root"

        create_archive(manifest, process_file, out)
        restored_manifest, contents = read_archive(out)

        # Manifest fields preserved (checksum gets set during create)
        assert restored_manifest.package_id == manifest.package_id
        assert restored_manifest.package_version == manifest.package_version
        assert restored_manifest.name == manifest.name
        assert restored_manifest.description == manifest.description
        assert restored_manifest.has_defaults is True
        assert restored_manifest.defaults_module == "defaults.agents"
        assert restored_manifest.checksum is not None

        # File contents preserved
        assert contents["process.yaml"] == process_file.read_bytes()
        assert contents["README.md"] == b"# My Process"
        assert contents["defaults/__init__.py"] == b""
        assert contents["defaults/agents.py"] == b"def run(): pass"

    def test_round_trip_with_extra_files(self, tmp_path: Path):
        process_file = _setup_process_dir(tmp_path)
        extra = tmp_path / "notes.txt"
        extra.write_text("some notes")
        manifest = RootManifest(**_minimal_manifest_data())
        out = tmp_path / "my-process.root"

        create_archive(manifest, process_file, out, extra_files={"notes.txt": extra})
        _, contents = read_archive(out)

        assert contents["notes.txt"] == b"some notes"
