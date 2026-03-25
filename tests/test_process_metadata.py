"""Tests for ProcessDefinition metadata field."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from roots.core.schema import ProcessDefinition
from roots.core.validator import load_process_yaml, parse_process_dict


def _minimal_process_dict(**overrides: Any) -> dict[str, Any]:
    """Return a minimal valid process dict."""
    base: dict[str, Any] = {
        "id": "test-proc",
        "name": "Test Process",
        "version": "1.0.0",
        "nodes": [
            {
                "id": "start",
                "type": "agent",
                "label": "Start",
                "config": {"agent": "a", "output_key": "out"},
            },
            {
                "id": "done",
                "type": "end",
                "label": "Done",
                "config": {"status": "completed"},
            },
        ],
        "edges": [{"from": "start", "to": "done"}],
        "entry_point": "start",
    }
    base.update(overrides)
    return base


class TestProcessMetadataBackwardCompatibility:
    """Existing YAML files without metadata must parse correctly."""

    def test_dict_without_metadata_defaults_to_empty(self) -> None:
        data = _minimal_process_dict()
        assert "metadata" not in data
        proc = parse_process_dict(data)
        assert proc.metadata == {}

    def test_yaml_without_metadata_loads(self, tmp_path: Path) -> None:
        yaml_content = textwrap.dedent("""\
            id: no-meta
            name: No Metadata Process
            version: "1.0.0"
            entry_point: start
            nodes:
              - id: start
                type: agent
                label: Start
                config:
                  agent: worker
                  output_key: result
              - id: done
                type: end
                label: Done
                config:
                  status: completed
            edges:
              - from: start
                to: done
        """)
        yaml_file = tmp_path / "no_meta.yaml"
        yaml_file.write_text(yaml_content)
        proc = load_process_yaml(yaml_file)
        assert proc.metadata == {}

    def test_existing_example_processes_still_load(self) -> None:
        examples_dir = (
            Path(__file__).resolve().parent.parent / "examples" / "processes"
        )
        for yaml_file in examples_dir.glob("*.yaml"):
            proc = load_process_yaml(yaml_file)
            assert isinstance(proc.metadata, dict)


class TestProcessMetadataRoundTrip:
    """Metadata must survive save → load cycles."""

    SAMPLE_METADATA: dict[str, Any] = {
        "package_id": "com.example.workflow",
        "package_version": "2.1.0",
        "installed_from": "/packages/workflow.root",
        "installed_at": "2026-03-25T10:30:00Z",
    }

    def test_round_trip_through_model_dump_and_validate(self) -> None:
        data = _minimal_process_dict(metadata=self.SAMPLE_METADATA)
        proc = parse_process_dict(data)
        assert proc.metadata == self.SAMPLE_METADATA

        dumped = proc.model_dump(by_alias=True, mode="json")
        for i, node in enumerate(proc.nodes):
            if isinstance(node.config, BaseModel):
                dumped["nodes"][i]["config"] = node.config.model_dump(
                    mode="json"
                )
        reloaded = ProcessDefinition.model_validate(dumped)
        assert reloaded.metadata == self.SAMPLE_METADATA

    def test_round_trip_through_json_serialization(self) -> None:
        """Simulates what _serialize_process does in storage backends."""
        data = _minimal_process_dict(metadata=self.SAMPLE_METADATA)
        proc = parse_process_dict(data)

        dumped = proc.model_dump(by_alias=True, mode="json")
        for i, node in enumerate(proc.nodes):
            if isinstance(node.config, BaseModel):
                dumped["nodes"][i]["config"] = node.config.model_dump(
                    mode="json"
                )
        json_str = json.dumps(dumped)
        reloaded = ProcessDefinition.model_validate(json.loads(json_str))
        assert reloaded.metadata == self.SAMPLE_METADATA

    def test_round_trip_through_yaml(self, tmp_path: Path) -> None:
        yaml_content = textwrap.dedent("""\
            id: meta-test
            name: Metadata Test
            version: "1.0.0"
            metadata:
              package_id: com.example.workflow
              package_version: "2.1.0"
              installed_from: /packages/workflow.root
              installed_at: "2026-03-25T10:30:00Z"
            entry_point: start
            nodes:
              - id: start
                type: agent
                label: Start
                config:
                  agent: worker
                  output_key: result
              - id: done
                type: end
                label: Done
                config:
                  status: completed
            edges:
              - from: start
                to: done
        """)
        yaml_file = tmp_path / "with_meta.yaml"
        yaml_file.write_text(yaml_content)

        proc = load_process_yaml(yaml_file)
        assert proc.metadata["package_id"] == "com.example.workflow"
        assert proc.metadata["package_version"] == "2.1.0"

        # Re-serialize to YAML and reparse
        dumped = proc.model_dump(by_alias=True, mode="json")
        for i, node in enumerate(proc.nodes):
            if isinstance(node.config, BaseModel):
                dumped["nodes"][i]["config"] = node.config.model_dump(
                    mode="json"
                )
        yaml_file2 = tmp_path / "round_tripped.yaml"
        yaml_file2.write_text(yaml.dump(dumped, default_flow_style=False))
        proc2 = load_process_yaml(yaml_file2)
        assert proc2.metadata == proc.metadata

    def test_empty_metadata_round_trips(self) -> None:
        data = _minimal_process_dict(metadata={})
        proc = parse_process_dict(data)
        assert proc.metadata == {}

        dumped = proc.model_dump(by_alias=True, mode="json")
        for i, node in enumerate(proc.nodes):
            if isinstance(node.config, BaseModel):
                dumped["nodes"][i]["config"] = node.config.model_dump(
                    mode="json"
                )
        reloaded = ProcessDefinition.model_validate(dumped)
        assert reloaded.metadata == {}
