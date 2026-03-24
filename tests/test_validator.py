"""Tests for YAML parsing pipeline."""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

import pytest
import yaml

from roots.core.validator import (
    ProcessValidationError,
    load_process_yaml,
    parse_process_dict,
    validate_process_yaml,
    validate_structure,
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


# --- Helpers for structural validation tests ---

VALID_CONFIGS: dict[str, dict[str, Any]] = {
    "agent": {"agent": "summarizer", "output_key": "summary"},
    "end": {"status": "completed"},
    "decision": {
        "mode": "deterministic",
        "edges": [{"target": "done", "condition": "x > 0"}],
    },
    "fork": {},
    "join": {},
    "emit": {"event_type": "process.done"},
    "checkpoint": {"prompt": "Review"},
}


def _make_node(
    node_id: str,
    node_type: str = "agent",
    config: dict[str, Any] | None = None,
    retry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "id": node_id,
        "type": node_type,
        "label": f"Node {node_id}",
        "config": config if config is not None else VALID_CONFIGS[node_type],
    }
    if retry is not None:
        node["retry"] = retry
    return node


def _build_process(**overrides: Any) -> ProcessDefinition:
    """Build a ProcessDefinition from a dict, going through parse_process_dict."""
    defaults: dict[str, Any] = {
        "id": "proc-1",
        "name": "Test Process",
        "version": "1.0.0",
        "entry_point": "start",
        "nodes": [
            _make_node("start"),
            _make_node("done", "end"),
        ],
        "edges": [{"from": "start", "to": "done"}],
    }
    defaults.update(overrides)
    return ProcessDefinition.model_validate(defaults)


class TestDecisionEdgeExclusivity:
    def test_decision_with_top_level_edge_rejected(self) -> None:
        process = _build_process(
            nodes=[
                _make_node("decide", "decision", {
                    "mode": "deterministic",
                    "edges": [{"target": "done", "condition": "x > 0"}],
                }),
                _make_node("other"),
                _make_node("done", "end"),
            ],
            edges=[
                {"from": "decide", "to": "other"},
                {"from": "other", "to": "done"},
            ],
            entry_point="decide",
        )
        errors = validate_structure(process)
        assert any(
            "Decision node 'decide' must not have top-level outbound edges"
            in e
            for e in errors
        )

    def test_decision_without_top_level_edge_ok(self) -> None:
        process = _build_process(
            nodes=[
                _make_node("start"),
                _make_node("decide", "decision", {
                    "mode": "deterministic",
                    "edges": [{"target": "done", "condition": "x > 0"}],
                }),
                _make_node("done", "end"),
            ],
            edges=[{"from": "start", "to": "decide"}],
            entry_point="start",
        )
        errors = validate_structure(process)
        assert not any("Decision node" in e for e in errors)


class TestEdgeCompleteness:
    def test_non_terminal_node_without_outbound_edge_flagged(self) -> None:
        process = _build_process(
            nodes=[
                _make_node("start"),
                _make_node("orphan"),
                _make_node("done", "end"),
            ],
            edges=[{"from": "start", "to": "done"}],
            entry_point="start",
        )
        errors = validate_structure(process)
        assert any(
            "Node 'orphan' (agent) has no outbound edges" in e
            for e in errors
        )

    def test_end_node_without_outbound_edge_ok(self) -> None:
        process = _build_process()
        errors = validate_structure(process)
        assert not any("done" in e and "no outbound edges" in e for e in errors)

    def test_decision_node_without_top_level_edge_ok(self) -> None:
        process = _build_process(
            nodes=[
                _make_node("start"),
                _make_node("decide", "decision", {
                    "mode": "deterministic",
                    "edges": [{"target": "done", "condition": "x > 0"}],
                }),
                _make_node("done", "end"),
            ],
            edges=[{"from": "start", "to": "decide"}],
            entry_point="start",
        )
        errors = validate_structure(process)
        assert not any("decide" in e and "no outbound edges" in e for e in errors)

    def test_join_node_without_outbound_edge_ok(self) -> None:
        process = _build_process(
            nodes=[
                _make_node("start"),
                _make_node("joiner", "join"),
                _make_node("done", "end"),
            ],
            edges=[{"from": "start", "to": "joiner"}],
            entry_point="start",
        )
        errors = validate_structure(process)
        assert not any("joiner" in e and "no outbound edges" in e for e in errors)


class TestEndNodeExistence:
    def test_no_end_node_flagged(self) -> None:
        process = _build_process(
            nodes=[
                _make_node("start"),
                _make_node("middle"),
            ],
            edges=[{"from": "start", "to": "middle"}],
            entry_point="start",
        )
        errors = validate_structure(process)
        assert "Process has no end node" in errors

    def test_with_end_node_ok(self) -> None:
        errors = validate_structure(_build_process())
        assert "Process has no end node" not in errors


class TestReachability:
    def test_unreachable_node_produces_warning(self) -> None:
        process = _build_process(
            nodes=[
                _make_node("start"),
                _make_node("done", "end"),
                _make_node("island"),
            ],
            edges=[{"from": "start", "to": "done"}],
            entry_point="start",
        )
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            errors = validate_structure(process)
            assert any(
                "Node 'island' is unreachable from entry point" in str(warning.message)
                for warning in w
            )
        # Unreachable is a warning, not an error
        assert not any("unreachable" in e.lower() for e in errors)

    def test_all_reachable_no_warning(self) -> None:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            validate_structure(_build_process())
            assert not any("unreachable" in str(warning.message) for warning in w)

    def test_reachability_follows_decision_config_edges(self) -> None:
        process = _build_process(
            nodes=[
                _make_node("start"),
                _make_node("decide", "decision", {
                    "mode": "deterministic",
                    "edges": [{"target": "done", "condition": "x > 0"}],
                }),
                _make_node("done", "end"),
            ],
            edges=[{"from": "start", "to": "decide"}],
            entry_point="start",
        )
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            validate_structure(process)
            assert not any("unreachable" in str(warning.message) for warning in w)


class TestFallbackEdgeValidity:
    def test_invalid_fallback_edge_caught(self) -> None:
        process = _build_process(
            nodes=[
                _make_node("start", retry={
                    "max_attempts": 3,
                    "on_exhaustion": "route",
                    "fallback_edge": "nonexistent",
                }),
                _make_node("done", "end"),
            ],
            edges=[{"from": "start", "to": "done"}],
            entry_point="start",
        )
        errors = validate_structure(process)
        assert any(
            "Node 'start': fallback_edge 'nonexistent' does not reference a valid node"
            in e
            for e in errors
        )

    def test_valid_fallback_edge_ok(self) -> None:
        process = _build_process(
            nodes=[
                _make_node("start", retry={
                    "max_attempts": 3,
                    "on_exhaustion": "route",
                    "fallback_edge": "done",
                }),
                _make_node("done", "end"),
            ],
            edges=[{"from": "start", "to": "done"}],
            entry_point="start",
        )
        errors = validate_structure(process)
        assert not any("fallback_edge" in e for e in errors)


class TestAllErrorsReturnedTogether:
    def test_multiple_errors_returned(self) -> None:
        """Multiple structural issues should all be reported at once."""
        process = _build_process(
            nodes=[
                _make_node("start"),
                _make_node("orphan"),
                # No end node
            ],
            edges=[{"from": "start", "to": "orphan"}],
            entry_point="start",
        )
        errors = validate_structure(process)
        # Should have at least: no end node + orphan has no outbound edges
        assert len(errors) >= 2
        assert any("no end node" in e for e in errors)
        assert any("no outbound edges" in e for e in errors)


class TestProcessValidationErrorIntegration:
    def test_load_process_yaml_raises_on_structural_error(
        self, tmp_path: Path
    ) -> None:
        data = _valid_process_dict()
        # Add a decision node with a top-level edge (structural error)
        data["nodes"].insert(0, {
            "id": "decide",
            "type": "decision",
            "label": "Decision",
            "config": {
                "mode": "deterministic",
                "edges": [{"target": "done", "condition": "x > 0"}],
            },
        })
        data["edges"].append({"from": "decide", "to": "start"})
        data["entry_point"] = "decide"
        yaml_file = tmp_path / "structural_error.yaml"
        yaml_file.write_text(yaml.dump(data))
        with pytest.raises(ProcessValidationError) as exc_info:
            load_process_yaml(yaml_file)
        assert len(exc_info.value.errors) >= 1

    def test_validate_process_yaml_returns_structural_errors(
        self, tmp_path: Path
    ) -> None:
        data = _valid_process_dict()
        # Remove end node
        data["nodes"] = [data["nodes"][0]]
        data["edges"] = []
        yaml_file = tmp_path / "no_end.yaml"
        yaml_file.write_text(yaml.dump(data))
        errors = validate_process_yaml(yaml_file)
        assert any("no end node" in e.lower() for e in errors)
