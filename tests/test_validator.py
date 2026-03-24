"""Tests for YAML parsing pipeline."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from roots.core.validator import (
    load_process_yaml,
    parse_process_dict,
    validate_process_yaml,
)
from roots.core.schema import ProcessDefinition


def _valid_process_dict() -> dict[str, Any]:
    """Return a minimal valid process definition as a dict."""
    return {
        "id": "proc-1",
        "name": "Test Process",
        "version": "1.0.0",
        "entry_point": "start",
        "nodes": [
            {
                "id": "start",
                "type": "agent",
                "label": "Start Agent",
                "config": {"agent": "summarizer", "output_key": "summary"},
            },
            {
                "id": "done",
                "type": "end",
                "label": "End",
                "config": {"status": "completed"},
            },
        ],
        "edges": [{"from": "start", "to": "done"}],
    }


def _valid_yaml() -> str:
    """Return valid YAML content for a process definition."""
    return yaml.dump(_valid_process_dict(), default_flow_style=False)


class TestParseProcessDict:
    def test_valid_dict_returns_process_definition(self) -> None:
        result = parse_process_dict(_valid_process_dict())
        assert isinstance(result, ProcessDefinition)
        assert result.id == "proc-1"
        assert result.name == "Test Process"
        assert len(result.nodes) == 2
        assert len(result.edges) == 1

    def test_invalid_dict_raises_validation_error(self) -> None:
        with pytest.raises(Exception):
            parse_process_dict({"id": "proc-1"})

    def test_missing_required_field(self) -> None:
        data = _valid_process_dict()
        del data["name"]
        with pytest.raises(Exception):
            parse_process_dict(data)


class TestLoadProcessYaml:
    def test_valid_yaml_file(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "process.yaml"
        yaml_file.write_text(_valid_yaml())
        result = load_process_yaml(yaml_file)
        assert isinstance(result, ProcessDefinition)
        assert result.id == "proc-1"

    def test_valid_yaml_with_string_path(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "process.yaml"
        yaml_file.write_text(_valid_yaml())
        result = load_process_yaml(str(yaml_file))
        assert isinstance(result, ProcessDefinition)

    def test_file_not_found_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_process_yaml("/nonexistent/path/process.yaml")

    def test_invalid_yaml_syntax_raises(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "bad.yaml"
        yaml_file.write_text(":\n  - :\n    bad: [unclosed")
        with pytest.raises(yaml.YAMLError):
            load_process_yaml(yaml_file)

    def test_non_dict_yaml_raises(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "list.yaml"
        yaml_file.write_text("- item1\n- item2\n")
        with pytest.raises(yaml.YAMLError, match="Expected a YAML mapping"):
            load_process_yaml(yaml_file)

    def test_validation_error_on_bad_schema(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "bad_schema.yaml"
        yaml_file.write_text(yaml.dump({"id": "proc-1"}))
        with pytest.raises(Exception):
            load_process_yaml(yaml_file)


class TestValidateProcessYaml:
    def test_valid_yaml_returns_empty_list(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "process.yaml"
        yaml_file.write_text(_valid_yaml())
        errors = validate_process_yaml(yaml_file)
        assert errors == []

    def test_file_not_found_returns_error(self) -> None:
        errors = validate_process_yaml("/nonexistent/path/process.yaml")
        assert len(errors) == 1
        assert "File not found" in errors[0]

    def test_yaml_syntax_error_returns_error(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "bad.yaml"
        yaml_file.write_text(":\n  bad: [unclosed")
        errors = validate_process_yaml(yaml_file)
        assert len(errors) >= 1
        assert "YAML syntax error" in errors[0]

    def test_missing_required_field_returns_error(self, tmp_path: Path) -> None:
        data = _valid_process_dict()
        del data["name"]
        yaml_file = tmp_path / "missing_field.yaml"
        yaml_file.write_text(yaml.dump(data))
        errors = validate_process_yaml(yaml_file)
        assert len(errors) >= 1
        assert any("name" in e.lower() for e in errors)

    def test_node_level_validation_error_includes_context(
        self, tmp_path: Path
    ) -> None:
        data = _valid_process_dict()
        # Remove a required field from a node's config
        data["nodes"][0]["config"] = {}
        yaml_file = tmp_path / "bad_node.yaml"
        yaml_file.write_text(yaml.dump(data))
        errors = validate_process_yaml(yaml_file)
        assert len(errors) >= 1
        # Should include the node ID and type
        assert any("start" in e and "agent" in e for e in errors)

    def test_node_missing_required_config_field(
        self, tmp_path: Path
    ) -> None:
        data = _valid_process_dict()
        # Agent config missing output_key
        data["nodes"][0]["config"] = {"agent": "summarizer"}
        yaml_file = tmp_path / "missing_config_field.yaml"
        yaml_file.write_text(yaml.dump(data))
        errors = validate_process_yaml(yaml_file)
        assert len(errors) >= 1
        assert any("start" in e for e in errors)

    def test_non_dict_yaml_returns_error(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "list.yaml"
        yaml_file.write_text("- item1\n- item2\n")
        errors = validate_process_yaml(yaml_file)
        assert len(errors) == 1
        assert "mapping" in errors[0].lower()

    def test_decision_node_validation_error_includes_context(
        self, tmp_path: Path
    ) -> None:
        data = _valid_process_dict()
        data["nodes"][0] = {
            "id": "validation_gate",
            "type": "decision",
            "label": "Validation Gate",
            "config": {
                "mode": "ai_bounded",
                # Missing confidence_threshold
                "edges": [{"target": "done"}],
            },
        }
        yaml_file = tmp_path / "bad_decision.yaml"
        yaml_file.write_text(yaml.dump(data))
        errors = validate_process_yaml(yaml_file)
        assert len(errors) >= 1
        assert any("validation_gate" in e and "decision" in e for e in errors)
