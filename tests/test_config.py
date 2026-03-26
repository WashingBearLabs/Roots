"""Tests for configuration override application (US-004)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from roots.core.schema import ProcessDefinition
from roots.core.validator import parse_process_dict
from roots.packaging.config import (
    ConfigError,
    apply_override,
    apply_overrides_from_file,
    list_overrides,
)
from roots.packaging.extractor import extract_config_overrides
from roots.packaging.manifest import ConfigOverride, RootManifest


# ---------------------------------------------------------------------------
# Fixtures — process with decision thresholds and retry settings
# ---------------------------------------------------------------------------

_PROCESS_WITH_OVERRIDES = {
    "id": "override-test",
    "name": "Override Test Process",
    "version": "1.0.0",
    "description": "Process for testing config overrides",
    "nodes": [
        {
            "id": "triage",
            "type": "decision",
            "label": "Triage Decision",
            "config": {
                "mode": "ai_bounded",
                "confidence_threshold": 0.7,
                "model": "gpt-4o-mini",
                "context_prompt": "Decide how to route",
                "edges": [
                    {"target": "respond", "label": "respond"},
                    {"target": "done", "label": "done"},
                ],
            },
        },
        {
            "id": "respond",
            "type": "agent",
            "label": "Respond",
            "config": {"agent": "responder", "output_key": "response"},
            "retry": {
                "max_attempts": 3,
                "backoff": "fixed",
                "backoff_seconds": 5.0,
                "on_exhaustion": "fail",
            },
        },
        {
            "id": "done",
            "type": "end",
            "label": "Done",
            "config": {"status": "completed"},
        },
    ],
    "edges": [
        {"from": "respond", "to": "done"},
    ],
    "entry_point": "triage",
}


@pytest.fixture
def process() -> ProcessDefinition:
    return parse_process_dict(_PROCESS_WITH_OVERRIDES)


@pytest.fixture
def overrides(process: ProcessDefinition) -> list[ConfigOverride]:
    return extract_config_overrides(process)


# ---------------------------------------------------------------------------
# apply_override — modifies correct field
# ---------------------------------------------------------------------------


class TestApplyOverride:
    def test_override_decision_threshold(self, process: ProcessDefinition):
        updated = apply_override(
            process, "nodes.triage.config.confidence_threshold", 0.9
        )
        node = updated.get_node("triage")
        assert node is not None
        assert node.config.confidence_threshold == 0.9

    def test_override_decision_model(self, process: ProcessDefinition):
        updated = apply_override(
            process, "nodes.triage.config.model", "gpt-4o"
        )
        node = updated.get_node("triage")
        assert node is not None
        assert node.config.model == "gpt-4o"

    def test_override_retry_max_attempts(self, process: ProcessDefinition):
        updated = apply_override(
            process, "nodes.respond.config.retry.max_attempts", 5
        )
        node = updated.get_node("respond")
        assert node is not None
        assert node.retry.max_attempts == 5

    def test_override_retry_backoff_seconds(self, process: ProcessDefinition):
        updated = apply_override(
            process, "nodes.respond.config.retry.backoff_seconds", 10.0
        )
        node = updated.get_node("respond")
        assert node is not None
        assert node.retry.backoff_seconds == 10.0

    def test_override_does_not_mutate_original(self, process: ProcessDefinition):
        original_threshold = process.get_node("triage").config.confidence_threshold
        apply_override(
            process, "nodes.triage.config.confidence_threshold", 0.95
        )
        assert process.get_node("triage").config.confidence_threshold == original_threshold


# ---------------------------------------------------------------------------
# Invalid paths raise ConfigError with helpful message
# ---------------------------------------------------------------------------


class TestInvalidPaths:
    def test_invalid_prefix(self, process: ProcessDefinition):
        with pytest.raises(ConfigError, match="Invalid override path"):
            apply_override(process, "bad.path", 42)

    def test_unknown_node(self, process: ProcessDefinition):
        with pytest.raises(ConfigError, match="not found in process"):
            apply_override(
                process, "nodes.nonexistent.config.threshold", 0.5
            )

    def test_unknown_config_field(self, process: ProcessDefinition):
        with pytest.raises(ConfigError, match="Config field.*not found"):
            apply_override(
                process, "nodes.triage.config.nonexistent_field", "value"
            )

    def test_unknown_retry_field(self, process: ProcessDefinition):
        with pytest.raises(ConfigError, match="Retry field.*not found"):
            apply_override(
                process, "nodes.respond.config.retry.nonexistent", 1
            )

    def test_retry_on_node_without_retry(self, process: ProcessDefinition):
        with pytest.raises(ConfigError, match="does not have retry"):
            apply_override(
                process, "nodes.triage.config.retry.max_attempts", 5
            )


# ---------------------------------------------------------------------------
# Value constraint validation
# ---------------------------------------------------------------------------


class TestConstraintValidation:
    def test_min_constraint_rejects_below(
        self, process: ProcessDefinition, overrides: list[ConfigOverride]
    ):
        with pytest.raises(ConfigError, match="below minimum"):
            apply_override(
                process,
                "nodes.triage.config.confidence_threshold",
                -0.1,
                config_overrides=overrides,
            )

    def test_max_constraint_rejects_above(
        self, process: ProcessDefinition, overrides: list[ConfigOverride]
    ):
        with pytest.raises(ConfigError, match="above maximum"):
            apply_override(
                process,
                "nodes.triage.config.confidence_threshold",
                1.5,
                config_overrides=overrides,
            )

    def test_valid_constrained_value_passes(
        self, process: ProcessDefinition, overrides: list[ConfigOverride]
    ):
        updated = apply_override(
            process,
            "nodes.triage.config.confidence_threshold",
            0.85,
            config_overrides=overrides,
        )
        node = updated.get_node("triage")
        assert node.config.confidence_threshold == 0.85

    def test_enum_constraint_rejects_invalid(self, process: ProcessDefinition):
        override = ConfigOverride(
            path="nodes.triage.config.model",
            description="Model",
            default_value="gpt-4o-mini",
            value_type="string",
            constraints={"enum": ["gpt-4o", "gpt-4o-mini"]},
        )
        with pytest.raises(ConfigError, match="not in allowed values"):
            apply_override(
                process,
                "nodes.triage.config.model",
                "invalid-model",
                config_overrides=[override],
            )


# ---------------------------------------------------------------------------
# apply_overrides_from_file
# ---------------------------------------------------------------------------


class TestApplyOverridesFromFile:
    def test_applies_all_overrides_from_yaml(
        self, process: ProcessDefinition, tmp_path: Path
    ):
        overrides_data = {
            "overrides": {
                "nodes.triage.config.confidence_threshold": 0.9,
                "nodes.triage.config.model": "gpt-4o",
                "nodes.respond.config.retry.max_attempts": 5,
            }
        }
        overrides_file = tmp_path / "overrides.yaml"
        overrides_file.write_text(yaml.dump(overrides_data), encoding="utf-8")

        updated = apply_overrides_from_file(process, overrides_file)

        triage = updated.get_node("triage")
        assert triage.config.confidence_threshold == 0.9
        assert triage.config.model == "gpt-4o"

        respond = updated.get_node("respond")
        assert respond.retry.max_attempts == 5

    def test_invalid_yaml_raises_config_error(self, tmp_path: Path, process: ProcessDefinition):
        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text("not: a: valid: yaml: [", encoding="utf-8")

        with pytest.raises(ConfigError, match="Failed to read"):
            apply_overrides_from_file(process, bad_file)

    def test_missing_overrides_key_raises(self, tmp_path: Path, process: ProcessDefinition):
        no_key_file = tmp_path / "nokey.yaml"
        no_key_file.write_text(yaml.dump({"foo": "bar"}), encoding="utf-8")

        with pytest.raises(ConfigError, match="top-level 'overrides' mapping"):
            apply_overrides_from_file(process, no_key_file)

    def test_with_constraints(
        self, process: ProcessDefinition, tmp_path: Path,
        overrides: list[ConfigOverride],
    ):
        overrides_data = {
            "overrides": {
                "nodes.triage.config.confidence_threshold": 0.85,
            }
        }
        overrides_file = tmp_path / "overrides.yaml"
        overrides_file.write_text(yaml.dump(overrides_data), encoding="utf-8")

        updated = apply_overrides_from_file(
            process, overrides_file, config_overrides=overrides
        )
        triage = updated.get_node("triage")
        assert triage.config.confidence_threshold == 0.85


# ---------------------------------------------------------------------------
# list_overrides from manifest
# ---------------------------------------------------------------------------


class TestListOverrides:
    def test_list_overrides_returns_manifest_overrides(self):
        manifest = RootManifest(
            package_id="test/pkg",
            package_version="1.0.0",
            name="Test",
            description="Test package",
            roots_version=">=0.1.0",
            agent_contracts=[],
            config_overrides=[
                ConfigOverride(
                    path="nodes.triage.config.confidence_threshold",
                    description="Confidence threshold",
                    default_value=0.7,
                    value_type="float",
                    constraints={"min": 0.0, "max": 1.0},
                ),
                ConfigOverride(
                    path="nodes.triage.config.model",
                    description="Model",
                    default_value="gpt-4o-mini",
                    value_type="string",
                ),
            ],
        )

        result = list_overrides(manifest)
        assert len(result) == 2
        assert result[0].path == "nodes.triage.config.confidence_threshold"
        assert result[1].path == "nodes.triage.config.model"

    def test_list_overrides_empty(self):
        manifest = RootManifest(
            package_id="test/pkg",
            package_version="1.0.0",
            name="Test",
            description="Test",
            roots_version=">=0.1.0",
            agent_contracts=[],
        )
        assert list_overrides(manifest) == []


# ---------------------------------------------------------------------------
# Type coercion
# ---------------------------------------------------------------------------


class TestTypeCoercion:
    def test_string_to_int_coercion(
        self, process: ProcessDefinition, overrides: list[ConfigOverride]
    ):
        updated = apply_override(
            process,
            "nodes.respond.config.retry.max_attempts",
            "7",
            config_overrides=overrides,
        )
        node = updated.get_node("respond")
        assert node.retry.max_attempts == 7

    def test_string_to_float_coercion(
        self, process: ProcessDefinition, overrides: list[ConfigOverride]
    ):
        updated = apply_override(
            process,
            "nodes.triage.config.confidence_threshold",
            "0.88",
            config_overrides=overrides,
        )
        node = updated.get_node("triage")
        assert node.config.confidence_threshold == 0.88
