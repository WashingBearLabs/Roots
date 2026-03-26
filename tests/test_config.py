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
    apply_template,
    list_overrides,
    list_templates,
)
from roots.packaging.extractor import extract_config_overrides
from roots.packaging.manifest import ConfigOverride, ConfigTemplate, RootManifest


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


# ---------------------------------------------------------------------------
# ConfigTemplate model
# ---------------------------------------------------------------------------


class TestConfigTemplate:
    def test_template_creation(self):
        template = ConfigTemplate(
            name="high-security",
            description="Strict thresholds for production",
            overrides={
                "nodes.triage.config.confidence_threshold": 0.95,
                "nodes.respond.config.retry.max_attempts": 5,
            },
        )
        assert template.name == "high-security"
        assert template.description == "Strict thresholds for production"
        assert len(template.overrides) == 2

    def test_template_empty_name_rejected(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            ConfigTemplate(
                name="  ",
                description="Bad template",
                overrides={},
            )

    def test_template_serializes_to_json(self):
        template = ConfigTemplate(
            name="permissive",
            description="Lower thresholds",
            overrides={"nodes.triage.config.confidence_threshold": 0.5},
        )
        data = template.model_dump(mode="json")
        assert data["name"] == "permissive"
        assert data["overrides"]["nodes.triage.config.confidence_threshold"] == 0.5

    def test_manifest_with_templates(self):
        manifest = RootManifest(
            package_id="test/pkg",
            package_version="1.0.0",
            name="Test",
            description="Test package",
            roots_version=">=0.1.0",
            agent_contracts=[],
            config_templates=[
                ConfigTemplate(
                    name="high-security",
                    description="Strict thresholds",
                    overrides={"nodes.triage.config.confidence_threshold": 0.95},
                ),
                ConfigTemplate(
                    name="permissive",
                    description="Lower thresholds",
                    overrides={"nodes.triage.config.confidence_threshold": 0.5},
                ),
            ],
        )
        assert len(manifest.config_templates) == 2
        assert manifest.config_templates[0].name == "high-security"

    def test_manifest_templates_default_empty(self):
        manifest = RootManifest(
            package_id="test/pkg",
            package_version="1.0.0",
            name="Test",
            description="Test",
            roots_version=">=0.1.0",
            agent_contracts=[],
        )
        assert manifest.config_templates == []

    def test_manifest_templates_serialize_in_json(self):
        manifest = RootManifest(
            package_id="test/pkg",
            package_version="1.0.0",
            name="Test",
            description="Test",
            roots_version=">=0.1.0",
            agent_contracts=[],
            config_templates=[
                ConfigTemplate(
                    name="prod",
                    description="Production settings",
                    overrides={"nodes.triage.config.confidence_threshold": 0.9},
                ),
            ],
        )
        data = manifest.model_dump(mode="json")
        assert len(data["config_templates"]) == 1
        assert data["config_templates"][0]["name"] == "prod"


# ---------------------------------------------------------------------------
# list_templates
# ---------------------------------------------------------------------------


class TestListTemplates:
    def test_list_templates_returns_manifest_templates(self):
        manifest = RootManifest(
            package_id="test/pkg",
            package_version="1.0.0",
            name="Test",
            description="Test",
            roots_version=">=0.1.0",
            agent_contracts=[],
            config_templates=[
                ConfigTemplate(
                    name="high-security",
                    description="Strict thresholds",
                    overrides={"nodes.triage.config.confidence_threshold": 0.95},
                ),
            ],
        )
        result = list_templates(manifest)
        assert len(result) == 1
        assert result[0].name == "high-security"

    def test_list_templates_empty(self):
        manifest = RootManifest(
            package_id="test/pkg",
            package_version="1.0.0",
            name="Test",
            description="Test",
            roots_version=">=0.1.0",
            agent_contracts=[],
        )
        assert list_templates(manifest) == []


# ---------------------------------------------------------------------------
# apply_template
# ---------------------------------------------------------------------------


class TestApplyTemplate:
    def test_apply_template_modifies_all_overrides(
        self, process: ProcessDefinition
    ):
        template = ConfigTemplate(
            name="high-security",
            description="Strict thresholds",
            overrides={
                "nodes.triage.config.confidence_threshold": 0.95,
                "nodes.respond.config.retry.max_attempts": 5,
            },
        )
        updated = apply_template(process, template)

        triage = updated.get_node("triage")
        assert triage.config.confidence_threshold == 0.95

        respond = updated.get_node("respond")
        assert respond.retry.max_attempts == 5

    def test_apply_template_does_not_mutate_original(
        self, process: ProcessDefinition
    ):
        original_threshold = process.get_node("triage").config.confidence_threshold
        template = ConfigTemplate(
            name="test",
            description="Test",
            overrides={"nodes.triage.config.confidence_threshold": 0.99},
        )
        apply_template(process, template)
        assert process.get_node("triage").config.confidence_threshold == original_threshold

    def test_apply_template_with_constraints(
        self, process: ProcessDefinition, overrides: list[ConfigOverride]
    ):
        template = ConfigTemplate(
            name="valid",
            description="Valid template",
            overrides={"nodes.triage.config.confidence_threshold": 0.85},
        )
        updated = apply_template(process, template, config_overrides=overrides)
        assert updated.get_node("triage").config.confidence_threshold == 0.85

    def test_apply_template_invalid_path_raises(
        self, process: ProcessDefinition
    ):
        template = ConfigTemplate(
            name="bad",
            description="Bad template",
            overrides={"bad.path": 42},
        )
        with pytest.raises(ConfigError, match="Invalid override path"):
            apply_template(process, template)

    def test_apply_template_constraint_violation_raises(
        self, process: ProcessDefinition, overrides: list[ConfigOverride]
    ):
        template = ConfigTemplate(
            name="invalid",
            description="Invalid values",
            overrides={"nodes.triage.config.confidence_threshold": 1.5},
        )
        with pytest.raises(ConfigError, match="above maximum"):
            apply_template(process, template, config_overrides=overrides)
