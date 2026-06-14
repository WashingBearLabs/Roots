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
    validate_subprocess_references,
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
    "subprocess": {"process_id": "other-proc", "output_key": "result"},
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


class TestForkJoinPairing:
    def test_valid_two_branch_fork_join(self) -> None:
        process = _build_process(
            nodes=[
                _make_node("start"),
                _make_node("split", "fork"),
                _make_node("a", "agent"),
                _make_node("b", "agent"),
                _make_node("merge", "join"),
                _make_node("done", "end"),
            ],
            edges=[
                {"from": "start", "to": "split"},
                {"from": "split", "to": "a"},
                {"from": "split", "to": "b"},
                {"from": "a", "to": "merge"},
                {"from": "b", "to": "merge"},
                {"from": "merge", "to": "done"},
            ],
            entry_point="start",
        )
        errors = validate_structure(process)
        assert not any("fork" in e.lower() or "join" in e.lower() for e in errors)
        assert process.fork_join_map == {"split": "merge"}

    def test_valid_three_branch_fork_join(self) -> None:
        process = _build_process(
            nodes=[
                _make_node("start"),
                _make_node("split", "fork"),
                _make_node("a", "agent"),
                _make_node("b", "agent"),
                _make_node("c", "agent"),
                _make_node("merge", "join"),
                _make_node("done", "end"),
            ],
            edges=[
                {"from": "start", "to": "split"},
                {"from": "split", "to": "a"},
                {"from": "split", "to": "b"},
                {"from": "split", "to": "c"},
                {"from": "a", "to": "merge"},
                {"from": "b", "to": "merge"},
                {"from": "c", "to": "merge"},
                {"from": "merge", "to": "done"},
            ],
            entry_point="start",
        )
        errors = validate_structure(process)
        assert not any("fork" in e.lower() or "join" in e.lower() for e in errors)
        assert process.fork_join_map == {"split": "merge"}

    def test_fork_with_no_outbound_edges(self) -> None:
        process = _build_process(
            nodes=[
                _make_node("start"),
                _make_node("split", "fork"),
                _make_node("done", "end"),
            ],
            edges=[
                {"from": "start", "to": "split"},
            ],
            entry_point="start",
        )
        errors = validate_structure(process)
        assert any(
            "Fork node 'split' has no outbound edges" in e
            for e in errors
        )

    def test_fork_with_one_branch_rejected(self) -> None:
        process = _build_process(
            nodes=[
                _make_node("start"),
                _make_node("split", "fork"),
                _make_node("a", "agent"),
                _make_node("merge", "join"),
                _make_node("done", "end"),
            ],
            edges=[
                {"from": "start", "to": "split"},
                {"from": "split", "to": "a"},
                {"from": "a", "to": "merge"},
                {"from": "merge", "to": "done"},
            ],
            entry_point="start",
        )
        errors = validate_structure(process)
        assert any(
            "Fork node 'split' has no outbound edges — need at least 2 branches" in e
            for e in errors
        )

    def test_branch_escaping_to_end_without_join(self) -> None:
        process = _build_process(
            nodes=[
                _make_node("start"),
                _make_node("split", "fork"),
                _make_node("a", "agent"),
                _make_node("b", "agent"),
                _make_node("merge", "join"),
                _make_node("done", "end"),
            ],
            edges=[
                {"from": "start", "to": "split"},
                {"from": "split", "to": "a"},
                {"from": "split", "to": "b"},
                {"from": "a", "to": "merge"},
                {"from": "b", "to": "done"},
                {"from": "merge", "to": "done"},
            ],
            entry_point="start",
        )
        errors = validate_structure(process)
        assert any(
            "Fork node 'split': branch starting at 'b' reaches end node "
            "without passing through a join" in e
            for e in errors
        )

    def test_branches_converging_at_different_joins(self) -> None:
        process = _build_process(
            nodes=[
                _make_node("start"),
                _make_node("split", "fork"),
                _make_node("a", "agent"),
                _make_node("b", "agent"),
                _make_node("join1", "join"),
                _make_node("join2", "join"),
                _make_node("done", "end"),
            ],
            edges=[
                {"from": "start", "to": "split"},
                {"from": "split", "to": "a"},
                {"from": "split", "to": "b"},
                {"from": "a", "to": "join1"},
                {"from": "b", "to": "join2"},
                {"from": "join1", "to": "done"},
                {"from": "join2", "to": "done"},
            ],
            entry_point="start",
        )
        errors = validate_structure(process)
        assert any(
            "branches converge at different join nodes" in e
            for e in errors
        )

    def test_unpaired_join_node(self) -> None:
        process = _build_process(
            nodes=[
                _make_node("start"),
                _make_node("stray_join", "join"),
                _make_node("done", "end"),
            ],
            edges=[
                {"from": "start", "to": "stray_join"},
                {"from": "stray_join", "to": "done"},
            ],
            entry_point="start",
        )
        errors = validate_structure(process)
        assert any(
            "Join node 'stray_join' is not paired with any fork node" in e
            for e in errors
        )

    def test_branch_with_no_path_to_join(self) -> None:
        process = _build_process(
            nodes=[
                _make_node("start"),
                _make_node("split", "fork"),
                _make_node("a", "agent"),
                _make_node("b", "agent"),
                _make_node("done", "end"),
            ],
            edges=[
                {"from": "start", "to": "split"},
                {"from": "split", "to": "a"},
                {"from": "split", "to": "b"},
                {"from": "a", "to": "b"},
            ],
            entry_point="start",
        )
        errors = validate_structure(process)
        assert any(
            "has no path to a join node" in e
            for e in errors
        )

    def test_fork_join_map_stored_on_process_definition(self) -> None:
        process = _build_process(
            nodes=[
                _make_node("start"),
                _make_node("split", "fork"),
                _make_node("a", "agent"),
                _make_node("b", "agent"),
                _make_node("merge", "join"),
                _make_node("done", "end"),
            ],
            edges=[
                {"from": "start", "to": "split"},
                {"from": "split", "to": "a"},
                {"from": "split", "to": "b"},
                {"from": "a", "to": "merge"},
                {"from": "b", "to": "merge"},
                {"from": "merge", "to": "done"},
            ],
            entry_point="start",
        )
        errors = validate_structure(process)
        assert errors == []
        assert isinstance(process.fork_join_map, dict)
        assert process.fork_join_map["split"] == "merge"


# --- Subprocess reference validation tests ---


class TestSubprocessSelfReference:
    def test_self_reference_flagged(self) -> None:
        process = _build_process(
            id="proc-1",
            nodes=[
                _make_node("sub", "subprocess", {
                    "process_id": "proc-1",
                    "output_key": "result",
                }),
                _make_node("done", "end"),
            ],
            edges=[{"from": "sub", "to": "done"}],
            entry_point="sub",
        )
        errors = validate_structure(process)
        assert any(
            "Subprocess node 'sub' references its own process 'proc-1'" in e
            for e in errors
        )

    def test_reference_to_other_process_ok(self) -> None:
        process = _build_process(
            nodes=[
                _make_node("sub", "subprocess"),
                _make_node("done", "end"),
            ],
            edges=[{"from": "sub", "to": "done"}],
            entry_point="sub",
        )
        errors = validate_structure(process)
        assert not any("references its own process" in e for e in errors)


class TestValidateSubprocessReferences:
    async def test_no_subprocess_nodes_ok(
        self, sqlite_storage: Any
    ) -> None:
        process = _build_process()
        errors = await validate_subprocess_references(process, sqlite_storage)
        assert errors == []

    async def test_missing_referenced_process_returns_error(
        self, sqlite_storage: Any
    ) -> None:
        process = _build_process(
            nodes=[
                _make_node("sub", "subprocess", {
                    "process_id": "nonexistent",
                    "output_key": "result",
                }),
                _make_node("done", "end"),
            ],
            edges=[{"from": "sub", "to": "done"}],
            entry_point="sub",
        )
        await sqlite_storage.save_process(process)
        errors = await validate_subprocess_references(process, sqlite_storage)
        assert len(errors) == 1
        assert "nonexistent" in errors[0]

    async def test_valid_subprocess_reference_ok(
        self, sqlite_storage: Any
    ) -> None:
        child = _build_process(id="child-proc")
        await sqlite_storage.save_process(child)

        process = _build_process(
            nodes=[
                _make_node("sub", "subprocess", {
                    "process_id": "child-proc",
                    "output_key": "result",
                }),
                _make_node("done", "end"),
            ],
            edges=[{"from": "sub", "to": "done"}],
            entry_point="sub",
        )
        await sqlite_storage.save_process(process)
        errors = await validate_subprocess_references(process, sqlite_storage)
        assert errors == []

    async def test_circular_ref_depth_2(
        self, sqlite_storage: Any
    ) -> None:
        """A→B→A circular reference at depth 2."""
        proc_a = ProcessDefinition.model_validate({
            "id": "proc-a",
            "name": "Process A",
            "version": "1.0.0",
            "entry_point": "sub",
            "nodes": [
                {"id": "sub", "type": "subprocess", "label": "Sub",
                 "config": {"process_id": "proc-b", "output_key": "result"}},
                {"id": "done", "type": "end", "label": "Done",
                 "config": {"status": "completed"}},
            ],
            "edges": [{"from": "sub", "to": "done"}],
        })
        proc_b = ProcessDefinition.model_validate({
            "id": "proc-b",
            "name": "Process B",
            "version": "1.0.0",
            "entry_point": "sub",
            "nodes": [
                {"id": "sub", "type": "subprocess", "label": "Sub",
                 "config": {"process_id": "proc-a", "output_key": "result"}},
                {"id": "done", "type": "end", "label": "Done",
                 "config": {"status": "completed"}},
            ],
            "edges": [{"from": "sub", "to": "done"}],
        })
        await sqlite_storage.save_process(proc_a)
        await sqlite_storage.save_process(proc_b)

        errors = await validate_subprocess_references(proc_a, sqlite_storage)
        assert len(errors) >= 1
        assert any("Circular" in e for e in errors)
        assert any("proc-a" in e and "proc-b" in e for e in errors)

    async def test_circular_ref_depth_3(
        self, sqlite_storage: Any
    ) -> None:
        """A→B→C→A circular reference at depth 3."""
        proc_a = ProcessDefinition.model_validate({
            "id": "proc-a",
            "name": "Process A",
            "version": "1.0.0",
            "entry_point": "sub",
            "nodes": [
                {"id": "sub", "type": "subprocess", "label": "Sub",
                 "config": {"process_id": "proc-b", "output_key": "result"}},
                {"id": "done", "type": "end", "label": "Done",
                 "config": {"status": "completed"}},
            ],
            "edges": [{"from": "sub", "to": "done"}],
        })
        proc_b = ProcessDefinition.model_validate({
            "id": "proc-b",
            "name": "Process B",
            "version": "1.0.0",
            "entry_point": "sub",
            "nodes": [
                {"id": "sub", "type": "subprocess", "label": "Sub",
                 "config": {"process_id": "proc-c", "output_key": "result"}},
                {"id": "done", "type": "end", "label": "Done",
                 "config": {"status": "completed"}},
            ],
            "edges": [{"from": "sub", "to": "done"}],
        })
        proc_c = ProcessDefinition.model_validate({
            "id": "proc-c",
            "name": "Process C",
            "version": "1.0.0",
            "entry_point": "sub",
            "nodes": [
                {"id": "sub", "type": "subprocess", "label": "Sub",
                 "config": {"process_id": "proc-a", "output_key": "result"}},
                {"id": "done", "type": "end", "label": "Done",
                 "config": {"status": "completed"}},
            ],
            "edges": [{"from": "sub", "to": "done"}],
        })
        await sqlite_storage.save_process(proc_a)
        await sqlite_storage.save_process(proc_b)
        await sqlite_storage.save_process(proc_c)

        errors = await validate_subprocess_references(proc_a, sqlite_storage)
        assert len(errors) >= 1
        assert any("Circular" in e for e in errors)
        assert any("proc-a" in e and "proc-b" in e and "proc-c" in e for e in errors)

    async def test_circular_error_message_includes_cycle_path(
        self, sqlite_storage: Any
    ) -> None:
        """Error message names the full cycle path."""
        proc_a = ProcessDefinition.model_validate({
            "id": "alpha",
            "name": "Alpha",
            "version": "1.0.0",
            "entry_point": "sub",
            "nodes": [
                {"id": "sub", "type": "subprocess", "label": "Sub",
                 "config": {"process_id": "beta", "output_key": "result"}},
                {"id": "done", "type": "end", "label": "Done",
                 "config": {"status": "completed"}},
            ],
            "edges": [{"from": "sub", "to": "done"}],
        })
        proc_b = ProcessDefinition.model_validate({
            "id": "beta",
            "name": "Beta",
            "version": "1.0.0",
            "entry_point": "sub",
            "nodes": [
                {"id": "sub", "type": "subprocess", "label": "Sub",
                 "config": {"process_id": "alpha", "output_key": "result"}},
                {"id": "done", "type": "end", "label": "Done",
                 "config": {"status": "completed"}},
            ],
            "edges": [{"from": "sub", "to": "done"}],
        })
        await sqlite_storage.save_process(proc_a)
        await sqlite_storage.save_process(proc_b)

        errors = await validate_subprocess_references(proc_a, sqlite_storage)
        assert len(errors) == 1
        # Error should name both processes in the cycle
        assert "alpha" in errors[0]
        assert "beta" in errors[0]
        # Should show the path with arrow separator
        assert "→" in errors[0]
