"""Tests for roots.packaging.inspect — inspect_package function."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roots.packaging.inspect import inspect_package
from roots.packaging.pack import pack_process


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SIMPLE_PROCESS = """\
id: test-process
name: Test Process
version: "2.0.0"
description: A test process for inspection
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

_COMPLEX_PROCESS = """\
id: complex-process
name: Complex Process
version: "1.0.0"
description: A complex process with multiple node types
nodes:
  - id: ingest
    type: agent
    label: Ingest
    config:
      agent: ingest_agent
      output_key: ingest_out
  - id: decide
    type: decision
    label: Decision
    config:
      mode: ai_bounded
      confidence_threshold: 0.75
      model: "gpt-4o-mini"
      edges:
        - condition: "high_confidence"
          target: respond
        - condition: "low_confidence"
          target: done
  - id: respond
    type: agent
    label: Respond
    config:
      agent: respond_agent
      output_key: respond_out
    retry:
      max_attempts: 3
      backoff_seconds: 1.0
  - id: done
    type: end
    label: Complete
    config:
      status: completed
edges:
  - from: ingest
    to: decide
  - from: respond
    to: done
entry_point: ingest
"""


def _create_package(tmp_path: Path, process_yaml: str = _SIMPLE_PROCESS, **kwargs: str | None) -> Path:
    """Helper to create a .root package for testing."""
    process_file = tmp_path / "process.yaml"
    process_file.write_text(process_yaml)
    out = tmp_path / "test.root"
    pack_process(process_file, output_path=out, **kwargs)
    return out


# ---------------------------------------------------------------------------
# inspect_package — formatted output
# ---------------------------------------------------------------------------


class TestInspectPackageFormatted:
    def test_shows_package_name_and_version(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        pkg = _create_package(tmp_path)

        inspect_package(pkg)

        output = capsys.readouterr().out
        assert "Test Process" in output
        assert "2.0.0" in output

    def test_shows_description(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        pkg = _create_package(tmp_path)

        inspect_package(pkg)

        output = capsys.readouterr().out
        assert "A test process for inspection" in output

    def test_shows_author(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        pkg = _create_package(tmp_path, author="Jane Doe")

        inspect_package(pkg)

        output = capsys.readouterr().out
        assert "Jane Doe" in output

    def test_shows_agent_contracts_required(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        pkg = _create_package(tmp_path)

        inspect_package(pkg)

        output = capsys.readouterr().out
        assert "Required Agents" in output
        assert "worker_agent" in output

    def test_shows_process_summary(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        pkg = _create_package(tmp_path)

        inspect_package(pkg)

        output = capsys.readouterr().out
        assert "2 nodes" in output
        assert "1 edges" in output
        assert "Entry point: step1" in output

    def test_shows_node_types(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        pkg = _create_package(tmp_path)

        inspect_package(pkg)

        output = capsys.readouterr().out
        assert "agent" in output
        assert "end" in output

    def test_shows_checksum_verified(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        pkg = _create_package(tmp_path)

        inspect_package(pkg)

        output = capsys.readouterr().out
        assert "SHA-256 verified" in output

    def test_shows_readme_status(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        pkg = _create_package(tmp_path)

        inspect_package(pkg)

        output = capsys.readouterr().out
        assert "README:" in output
        assert "No" in output

    def test_shows_readme_yes_when_present(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        process_file = tmp_path / "process.yaml"
        process_file.write_text(_SIMPLE_PROCESS)
        (tmp_path / "README.md").write_text("# Hello")
        out = tmp_path / "test.root"
        pack_process(process_file, output_path=out)

        inspect_package(out)

        output = capsys.readouterr().out
        assert "README:" in output
        # Check that "Yes" appears after "README:"
        readme_line = [line for line in output.split("\n") if "README:" in line][0]
        assert "Yes" in readme_line

    def test_shows_defaults_status(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        pkg = _create_package(tmp_path)

        inspect_package(pkg)

        output = capsys.readouterr().out
        assert "Default Implementations:" in output

    def test_shows_defaults_yes_when_bundled(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        defaults_dir = tmp_path / "my_defaults"
        defaults_dir.mkdir()
        (defaults_dir / "agents.py").write_text("# defaults")
        pkg = _create_package(tmp_path, include_defaults=str(defaults_dir))

        inspect_package(pkg)

        output = capsys.readouterr().out
        defaults_line = [line for line in output.split("\n") if "Default Implementations:" in line][0]
        assert "Yes" in defaults_line

    def test_shows_config_overrides(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        pkg = _create_package(tmp_path, process_yaml=_COMPLEX_PROCESS)

        inspect_package(pkg)

        output = capsys.readouterr().out
        assert "Configurable Parameters" in output
        assert "decide.config.confidence" in output
        assert "gpt-4o-mini" in output
        assert "max_attempts" in output

    def test_shows_optional_agents(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        pool_process = """\
id: pool-test
name: Pool Test
version: "1.0.0"
description: Test with agent pool
nodes:
  - id: pool1
    type: agent_pool
    label: Pool
    config:
      agents: [primary_agent, backup_agent]
      execution_mode: first_pass
      output_key: pool_out
  - id: done
    type: end
    label: Done
    config:
      status: completed
edges:
  - from: pool1
    to: done
entry_point: pool1
"""
        pkg = _create_package(tmp_path, process_yaml=pool_process)

        inspect_package(pkg)

        output = capsys.readouterr().out
        assert "Required Agents" in output
        assert "primary_agent" in output
        assert "Optional Agents" in output
        assert "backup_agent" in output


# ---------------------------------------------------------------------------
# inspect_package — JSON output
# ---------------------------------------------------------------------------


class TestInspectPackageJson:
    def test_json_flag_outputs_valid_json(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        pkg = _create_package(tmp_path)

        inspect_package(pkg, output_json=True)

        output = capsys.readouterr().out
        data = json.loads(output)
        assert data["name"] == "Test Process"
        assert data["package_version"] == "2.0.0"

    def test_json_contains_agent_contracts(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        pkg = _create_package(tmp_path)

        inspect_package(pkg, output_json=True)

        output = capsys.readouterr().out
        data = json.loads(output)
        assert len(data["agent_contracts"]) == 1
        assert data["agent_contracts"][0]["name"] == "worker_agent"

    def test_json_contains_checksum(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        pkg = _create_package(tmp_path)

        inspect_package(pkg, output_json=True)

        output = capsys.readouterr().out
        data = json.loads(output)
        assert data["checksum"] is not None
        assert len(data["checksum"]) == 64


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestInspectPackageErrors:
    def test_file_not_found_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="Package not found"):
            inspect_package(tmp_path / "nonexistent.root")
