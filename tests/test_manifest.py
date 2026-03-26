"""Tests for roots.packaging.manifest models."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from roots.packaging.manifest import AgentContract, ConfigOverride, RootManifest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _minimal_manifest_data() -> dict[str, Any]:
    return {
        "package_id": "washingbearlabs/incident-response",
        "package_version": "1.0.0",
        "name": "Incident Response",
        "description": "Automated incident triage and response",
        "roots_version": ">=0.1.0",
        "agent_contracts": [
            {"name": "triage", "description": "Triages incoming alerts"},
        ],
    }


# ---------------------------------------------------------------------------
# AgentContract
# ---------------------------------------------------------------------------

class TestAgentContract:
    def test_minimal(self):
        ac = AgentContract(name="triage")
        assert ac.name == "triage"
        assert ac.required is True
        assert ac.timeout_seconds == 300
        assert ac.tags == []
        assert ac.description is None
        assert ac.input_schema is None
        assert ac.output_schema is None

    def test_all_fields(self):
        ac = AgentContract(
            name="enricher",
            description="Enriches data",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            required=False,
            timeout_seconds=60,
            tags=["enrichment", "security"],
        )
        assert ac.name == "enricher"
        assert ac.required is False
        assert ac.timeout_seconds == 60
        assert ac.tags == ["enrichment", "security"]

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError, match="name must not be empty"):
            AgentContract(name="  ")


# ---------------------------------------------------------------------------
# ConfigOverride
# ---------------------------------------------------------------------------

class TestConfigOverride:
    def test_creation(self):
        co = ConfigOverride(
            path="nodes.triage.config.confidence_threshold",
            description="Minimum confidence for auto-triage",
            default_value=0.8,
            value_type="float",
            constraints={"min": 0.0, "max": 1.0},
        )
        assert co.path == "nodes.triage.config.confidence_threshold"
        assert co.default_value == 0.8
        assert co.value_type == "float"
        assert co.constraints == {"min": 0.0, "max": 1.0}

    def test_no_constraints(self):
        co = ConfigOverride(
            path="some.path",
            description="A setting",
            default_value="hello",
            value_type="string",
        )
        assert co.constraints is None


# ---------------------------------------------------------------------------
# RootManifest
# ---------------------------------------------------------------------------

class TestRootManifest:
    def test_minimal(self):
        m = RootManifest(**_minimal_manifest_data())
        assert m.format_version == "1.0"
        assert m.package_id == "washingbearlabs/incident-response"
        assert m.package_version == "1.0.0"
        assert m.process_file == "process.yaml"
        assert m.has_defaults is False
        assert m.defaults_module is None
        assert m.readme_file == "README.md"
        assert m.checksum is None
        assert m.config_overrides == []
        assert m.tags == []
        assert len(m.agent_contracts) == 1

    def test_all_fields(self):
        data = _minimal_manifest_data()
        data.update(
            author="WashingBear Labs",
            license="MIT",
            tags=["security", "soc"],
            has_defaults=True,
            defaults_module="defaults.agents",
            checksum="abc123",
            config_overrides=[
                {
                    "path": "nodes.triage.config.threshold",
                    "description": "Confidence threshold",
                    "default_value": 0.8,
                    "value_type": "float",
                },
            ],
        )
        m = RootManifest(**data)
        assert m.author == "WashingBear Labs"
        assert m.license == "MIT"
        assert m.has_defaults is True
        assert len(m.config_overrides) == 1

    # --- package_id validation ---

    @pytest.mark.parametrize(
        "pid",
        [
            "simple-name",
            "org/name",
            "my_org/my-package",
            "UPPER",
            "org123/pkg456",
        ],
    )
    def test_valid_package_ids(self, pid: str):
        data = _minimal_manifest_data()
        data["package_id"] = pid
        m = RootManifest(**data)
        assert m.package_id == pid

    @pytest.mark.parametrize(
        "pid",
        [
            "",
            "org/name/extra",
            "has spaces",
            "org//name",
            "/leading",
            "trailing/",
        ],
    )
    def test_invalid_package_ids(self, pid: str):
        data = _minimal_manifest_data()
        data["package_id"] = pid
        with pytest.raises(ValidationError, match="package_id"):
            RootManifest(**data)

    # --- package_version validation ---

    @pytest.mark.parametrize(
        "version",
        ["0.0.1", "1.0.0", "2.3.4", "1.0.0-alpha", "1.0.0-beta.1", "1.0.0+build.123"],
    )
    def test_valid_semver(self, version: str):
        data = _minimal_manifest_data()
        data["package_version"] = version
        m = RootManifest(**data)
        assert m.package_version == version

    @pytest.mark.parametrize(
        "version",
        ["1.0", "v1.0.0", "not-a-version", "1.0.0.0", ""],
    )
    def test_invalid_semver(self, version: str):
        data = _minimal_manifest_data()
        data["package_version"] = version
        with pytest.raises(ValidationError, match="package_version"):
            RootManifest(**data)

    # --- JSON round-trip ---

    def test_json_round_trip(self):
        data = _minimal_manifest_data()
        data.update(
            author="Test Author",
            config_overrides=[
                {
                    "path": "nodes.x.config.y",
                    "description": "A setting",
                    "default_value": 42,
                    "value_type": "int",
                    "constraints": {"min": 0, "max": 100},
                },
            ],
        )
        original = RootManifest(**data)
        json_str = original.model_dump_json()

        # Parse JSON string back and reconstruct
        parsed = json.loads(json_str)
        restored = RootManifest(**parsed)

        assert restored == original
        assert restored.model_dump() == original.model_dump()

    def test_model_dump_dict_round_trip(self):
        original = RootManifest(**_minimal_manifest_data())
        dumped = original.model_dump()
        restored = RootManifest(**dumped)
        assert restored == original
