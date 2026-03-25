"""Tests for roots.packaging.installer — validate and load .root packages."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from roots.packaging.installer import load_package, validate_package
from roots.packaging.manifest import RootManifest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SIMPLE_PROCESS = """\
id: test-process
name: Test Process
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


def _minimal_manifest_data() -> dict[str, Any]:
    return {
        "package_id": "test-org/my-process",
        "package_version": "1.0.0",
        "name": "My Process",
        "description": "A test process",
        "roots_version": ">=0.1.0",
        "agent_contracts": [
            {"name": "worker_agent", "description": "Does work"},
        ],
    }


def _build_valid_archive(tmp_path: Path) -> Path:
    """Create a valid .root archive for testing."""
    process_bytes = _SIMPLE_PROCESS.encode()
    manifest_data = _minimal_manifest_data()
    manifest_data["checksum"] = hashlib.sha256(process_bytes).hexdigest()

    archive_path = tmp_path / "test.root"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest_data, indent=2))
        zf.writestr("process.yaml", process_bytes)

    return archive_path


# ---------------------------------------------------------------------------
# validate_package — valid package
# ---------------------------------------------------------------------------


class TestValidPackage:
    def test_valid_package_returns_no_errors(self, tmp_path: Path):
        archive = _build_valid_archive(tmp_path)
        errors = validate_package(archive)
        assert errors == []

    def test_valid_package_without_checksum(self, tmp_path: Path):
        process_bytes = _SIMPLE_PROCESS.encode()
        manifest_data = _minimal_manifest_data()
        # No checksum field

        archive_path = tmp_path / "test.root"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest_data, indent=2))
            zf.writestr("process.yaml", process_bytes)

        errors = validate_package(archive_path)
        assert errors == []


# ---------------------------------------------------------------------------
# validate_package — corrupted zip
# ---------------------------------------------------------------------------


class TestCorruptedZip:
    def test_corrupted_zip_produces_clear_error(self, tmp_path: Path):
        bad_file = tmp_path / "corrupt.root"
        bad_file.write_bytes(b"this is not a zip file at all")

        errors = validate_package(bad_file)

        assert len(errors) == 1
        assert "not a valid zip archive" in errors[0]


# ---------------------------------------------------------------------------
# validate_package — missing manifest.json
# ---------------------------------------------------------------------------


class TestMissingManifest:
    def test_missing_manifest_produces_clear_error(self, tmp_path: Path):
        archive_path = tmp_path / "no-manifest.root"
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr("process.yaml", _SIMPLE_PROCESS)

        errors = validate_package(archive_path)

        assert len(errors) == 1
        assert "manifest.json" in errors[0]
        assert "missing" in errors[0]


# ---------------------------------------------------------------------------
# validate_package — invalid manifest schema
# ---------------------------------------------------------------------------


class TestInvalidManifestSchema:
    def test_invalid_semver_produces_field_error(self, tmp_path: Path):
        manifest_data = _minimal_manifest_data()
        manifest_data["package_version"] = "not-semver"

        archive_path = tmp_path / "bad-manifest.root"
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest_data))
            zf.writestr("process.yaml", _SIMPLE_PROCESS)

        errors = validate_package(archive_path)

        assert len(errors) >= 1
        assert any("package_version" in e for e in errors)

    def test_missing_required_field_produces_error(self, tmp_path: Path):
        manifest_data = _minimal_manifest_data()
        del manifest_data["name"]

        archive_path = tmp_path / "bad-manifest.root"
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest_data))
            zf.writestr("process.yaml", _SIMPLE_PROCESS)

        errors = validate_package(archive_path)

        assert len(errors) >= 1
        assert any("name" in e for e in errors)

    def test_invalid_json_in_manifest(self, tmp_path: Path):
        archive_path = tmp_path / "bad-json.root"
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr("manifest.json", "{not valid json")
            zf.writestr("process.yaml", _SIMPLE_PROCESS)

        errors = validate_package(archive_path)

        assert len(errors) >= 1
        assert any("invalid JSON" in e for e in errors)


# ---------------------------------------------------------------------------
# validate_package — missing process.yaml
# ---------------------------------------------------------------------------


class TestMissingProcess:
    def test_missing_process_yaml_produces_clear_error(self, tmp_path: Path):
        manifest_data = _minimal_manifest_data()
        archive_path = tmp_path / "no-process.root"
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest_data))

        errors = validate_package(archive_path)

        assert len(errors) == 1
        assert "process.yaml" in errors[0]
        assert "missing" in errors[0]


# ---------------------------------------------------------------------------
# validate_package — invalid process YAML
# ---------------------------------------------------------------------------


class TestInvalidProcessYAML:
    def test_unparseable_yaml(self, tmp_path: Path):
        manifest_data = _minimal_manifest_data()
        archive_path = tmp_path / "bad-yaml.root"
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest_data))
            zf.writestr("process.yaml", ":\n  :\n    - [invalid")

        errors = validate_package(archive_path)

        assert len(errors) >= 1
        assert any("process.yaml" in e for e in errors)

    def test_yaml_not_a_mapping(self, tmp_path: Path):
        manifest_data = _minimal_manifest_data()
        archive_path = tmp_path / "list-yaml.root"
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest_data))
            zf.writestr("process.yaml", "- item1\n- item2\n")

        errors = validate_package(archive_path)

        assert len(errors) >= 1
        assert any("mapping" in e for e in errors)

    def test_schema_invalid_process(self, tmp_path: Path):
        """Process YAML that parses but fails ProcessDefinition validation."""
        manifest_data = _minimal_manifest_data()
        bad_process = "id: test\nname: Test\nversion: '1.0.0'\nnodes: []\n"

        archive_path = tmp_path / "bad-process.root"
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest_data))
            zf.writestr("process.yaml", bad_process)

        errors = validate_package(archive_path)

        assert len(errors) >= 1
        assert any("process.yaml" in e for e in errors)


# ---------------------------------------------------------------------------
# validate_package — failed checksum
# ---------------------------------------------------------------------------


class TestFailedChecksum:
    def test_checksum_mismatch_produces_clear_error(self, tmp_path: Path):
        process_bytes = _SIMPLE_PROCESS.encode()
        manifest_data = _minimal_manifest_data()
        manifest_data["checksum"] = "0000000000000000000000000000000000000000000000000000000000000000"

        archive_path = tmp_path / "bad-checksum.root"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest_data))
            zf.writestr("process.yaml", process_bytes)

        errors = validate_package(archive_path)

        assert len(errors) == 1
        assert "checksum" in errors[0].lower()


# ---------------------------------------------------------------------------
# validate_package — defaults validation
# ---------------------------------------------------------------------------


class TestDefaultsValidation:
    def test_missing_defaults_module_produces_error(self, tmp_path: Path):
        process_bytes = _SIMPLE_PROCESS.encode()
        manifest_data = _minimal_manifest_data()
        manifest_data["checksum"] = hashlib.sha256(process_bytes).hexdigest()
        manifest_data["has_defaults"] = True
        manifest_data["defaults_module"] = "defaults"

        archive_path = tmp_path / "no-defaults.root"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest_data))
            zf.writestr("process.yaml", process_bytes)

        errors = validate_package(archive_path)

        assert len(errors) == 1
        assert "defaults" in errors[0]

    def test_defaults_present_passes(self, tmp_path: Path):
        process_bytes = _SIMPLE_PROCESS.encode()
        manifest_data = _minimal_manifest_data()
        manifest_data["checksum"] = hashlib.sha256(process_bytes).hexdigest()
        manifest_data["has_defaults"] = True
        manifest_data["defaults_module"] = "defaults"

        archive_path = tmp_path / "with-defaults.root"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest_data))
            zf.writestr("process.yaml", process_bytes)
            zf.writestr("defaults/__init__.py", b"")

        errors = validate_package(archive_path)
        assert errors == []


# ---------------------------------------------------------------------------
# load_package
# ---------------------------------------------------------------------------


class TestLoadPackage:
    def test_load_valid_package(self, tmp_path: Path):
        archive = _build_valid_archive(tmp_path)
        manifest, process, contents = load_package(archive)

        assert manifest.package_id == "test-org/my-process"
        assert process.id == "test-process"
        assert "manifest.json" in contents
        assert "process.yaml" in contents

    def test_load_invalid_package_raises(self, tmp_path: Path):
        bad_file = tmp_path / "corrupt.root"
        bad_file.write_bytes(b"not a zip")

        with pytest.raises(ValueError, match="Package validation failed"):
            load_package(bad_file)

    def test_load_returns_correct_types(self, tmp_path: Path):
        from roots.core.schema import ProcessDefinition

        archive = _build_valid_archive(tmp_path)
        manifest, process, contents = load_package(archive)

        assert isinstance(manifest, RootManifest)
        assert isinstance(process, ProcessDefinition)
        assert isinstance(contents, dict)
