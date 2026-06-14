"""Tests for roots.packaging.installer — validate_contracts."""

from __future__ import annotations

from typing import Any

from roots.agents.registry import AgentRegistry
from roots.agents.types import AgentRegistration, AgentType
from roots.packaging.installer import (
    validate_contracts,
)
from roots.packaging.manifest import AgentContract, RootManifest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _noop(**kwargs: Any) -> dict[str, Any]:
    return {}


def _make_manifest(contracts: list[AgentContract]) -> RootManifest:
    return RootManifest(
        package_id="test-org/test",
        package_version="1.0.0",
        name="Test",
        description="Test package",
        roots_version=">=0.1.0",
        agent_contracts=contracts,
    )


def _register(
    registry: AgentRegistry,
    name: str,
    input_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
) -> None:
    registry.register(
        AgentRegistration(
            name=name,
            agent_type=AgentType.LOCAL,
            callable=_noop,
            input_schema=input_schema,
            output_schema=output_schema,
        )
    )


# ---------------------------------------------------------------------------
# All matched
# ---------------------------------------------------------------------------


class TestAllMatched:
    def test_all_agents_matched_no_schemas(self):
        """Registered agents matching contract name → satisfied, ready=True."""
        contracts = [
            AgentContract(name="agent_a", description="A"),
            AgentContract(name="agent_b", description="B"),
        ]
        manifest = _make_manifest(contracts)
        registry = AgentRegistry()
        _register(registry, "agent_a")
        _register(registry, "agent_b")

        report = validate_contracts(manifest, registry)

        assert report.ready is True
        assert len(report.satisfied) == 2
        assert report.missing == []
        assert report.optional_missing == []
        assert report.schema_mismatches == []

    def test_matched_agents_with_compatible_schemas(self):
        """Registration schema is superset of contract schema → satisfied."""
        contract_schema = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        }
        reg_schema = {
            "type": "object",
            "properties": {
                "x": {"type": "string"},
                "y": {"type": "integer"},
            },
            "required": ["x"],
        }
        contracts = [
            AgentContract(name="agent_a", input_schema=contract_schema),
        ]
        manifest = _make_manifest(contracts)
        registry = AgentRegistry()
        _register(registry, "agent_a", input_schema=reg_schema)

        report = validate_contracts(manifest, registry)

        assert report.ready is True
        assert len(report.satisfied) == 1
        assert report.satisfied[0].schema_compatible is True
        assert report.schema_mismatches == []


# ---------------------------------------------------------------------------
# Missing agents
# ---------------------------------------------------------------------------


class TestMissingAgents:
    def test_unregistered_required_agents_are_missing(self):
        """Unregistered required agents → missing, ready=False."""
        contracts = [
            AgentContract(name="present", description="Registered"),
            AgentContract(name="absent", description="Not registered"),
        ]
        manifest = _make_manifest(contracts)
        registry = AgentRegistry()
        _register(registry, "present")

        report = validate_contracts(manifest, registry)

        assert report.ready is False
        assert len(report.missing) == 1
        assert report.missing[0].name == "absent"
        assert len(report.satisfied) == 1

    def test_all_required_missing(self):
        contracts = [
            AgentContract(name="a"),
            AgentContract(name="b"),
        ]
        manifest = _make_manifest(contracts)
        registry = AgentRegistry()

        report = validate_contracts(manifest, registry)

        assert report.ready is False
        assert len(report.missing) == 2


# ---------------------------------------------------------------------------
# Optional agents
# ---------------------------------------------------------------------------


class TestOptionalAgents:
    def test_unregistered_optional_agents_not_blocking(self):
        """Unregistered optional agents → optional_missing, ready=True."""
        contracts = [
            AgentContract(name="required_agent", required=True),
            AgentContract(name="optional_agent", required=False),
        ]
        manifest = _make_manifest(contracts)
        registry = AgentRegistry()
        _register(registry, "required_agent")

        report = validate_contracts(manifest, registry)

        assert report.ready is True
        assert len(report.optional_missing) == 1
        assert report.optional_missing[0].name == "optional_agent"
        assert report.missing == []

    def test_optional_agent_registered_is_satisfied(self):
        contracts = [
            AgentContract(name="opt", required=False),
        ]
        manifest = _make_manifest(contracts)
        registry = AgentRegistry()
        _register(registry, "opt")

        report = validate_contracts(manifest, registry)

        assert report.ready is True
        assert len(report.satisfied) == 1
        assert report.optional_missing == []


# ---------------------------------------------------------------------------
# Schema mismatches
# ---------------------------------------------------------------------------


class TestSchemaMismatch:
    def test_input_schema_missing_required_property(self):
        """Registration missing required property → schema_mismatch, ready=False."""
        contract_schema = {
            "type": "object",
            "properties": {
                "x": {"type": "string"},
                "y": {"type": "integer"},
            },
            "required": ["x", "y"],
        }
        reg_schema = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        }
        contracts = [
            AgentContract(name="agent_a", input_schema=contract_schema),
        ]
        manifest = _make_manifest(contracts)
        registry = AgentRegistry()
        _register(registry, "agent_a", input_schema=reg_schema)

        report = validate_contracts(manifest, registry)

        assert report.ready is False
        assert len(report.schema_mismatches) == 1
        assert report.schema_mismatches[0].direction == "input"
        assert report.schema_mismatches[0].agent_name == "agent_a"
        assert "y" in report.schema_mismatches[0].details
        # Agent is still in satisfied (but schema_compatible=False)
        assert len(report.satisfied) == 1
        assert report.satisfied[0].schema_compatible is False

    def test_output_schema_type_mismatch(self):
        """Registration has wrong type for a property → schema_mismatch."""
        contract_schema = {
            "type": "object",
            "properties": {"result": {"type": "string"}},
            "required": ["result"],
        }
        reg_schema = {
            "type": "object",
            "properties": {"result": {"type": "integer"}},
            "required": ["result"],
        }
        contracts = [
            AgentContract(name="agent_b", output_schema=contract_schema),
        ]
        manifest = _make_manifest(contracts)
        registry = AgentRegistry()
        _register(registry, "agent_b", output_schema=reg_schema)

        report = validate_contracts(manifest, registry)

        assert report.ready is False
        assert len(report.schema_mismatches) == 1
        assert report.schema_mismatches[0].direction == "output"
        assert "type" in report.schema_mismatches[0].details

    def test_both_input_and_output_mismatch(self):
        """Both input and output schemas can mismatch simultaneously."""
        bad_schema = {
            "type": "object",
            "properties": {"z": {"type": "string"}},
            "required": ["z"],
        }
        reg_schema = {
            "type": "object",
            "properties": {},
        }
        contracts = [
            AgentContract(
                name="agent_c",
                input_schema=bad_schema,
                output_schema=bad_schema,
            ),
        ]
        manifest = _make_manifest(contracts)
        registry = AgentRegistry()
        _register(
            registry, "agent_c",
            input_schema=reg_schema,
            output_schema=reg_schema,
        )

        report = validate_contracts(manifest, registry)

        assert report.ready is False
        assert len(report.schema_mismatches) == 2
        directions = {m.direction for m in report.schema_mismatches}
        assert directions == {"input", "output"}


# ---------------------------------------------------------------------------
# No declared schema → soft pass
# ---------------------------------------------------------------------------


class TestNoSchema:
    def test_contract_no_schema_is_compatible(self):
        """Contract with no schema → soft pass (compatible by default)."""
        contracts = [AgentContract(name="agent_x")]
        manifest = _make_manifest(contracts)
        registry = AgentRegistry()
        _register(
            registry, "agent_x",
            input_schema={"type": "object", "properties": {"a": {"type": "string"}}},
        )

        report = validate_contracts(manifest, registry)

        assert report.ready is True
        assert len(report.satisfied) == 1
        assert report.satisfied[0].schema_compatible is True

    def test_registration_no_schema_is_compatible(self):
        """Registration with no schema → soft pass."""
        schema = {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": ["a"],
        }
        contracts = [AgentContract(name="agent_y", input_schema=schema)]
        manifest = _make_manifest(contracts)
        registry = AgentRegistry()
        _register(registry, "agent_y")  # no schemas

        report = validate_contracts(manifest, registry)

        assert report.ready is True
        assert report.satisfied[0].schema_compatible is True

    def test_both_no_schema_is_compatible(self):
        """Neither side has schema → compatible."""
        contracts = [AgentContract(name="agent_z")]
        manifest = _make_manifest(contracts)
        registry = AgentRegistry()
        _register(registry, "agent_z")

        report = validate_contracts(manifest, registry)

        assert report.ready is True
        assert report.satisfied[0].schema_compatible is True


# ---------------------------------------------------------------------------
# ready flag
# ---------------------------------------------------------------------------


class TestReadyFlag:
    def test_ready_true_when_all_required_satisfied_no_mismatches(self):
        contracts = [
            AgentContract(name="a"),
            AgentContract(name="b", required=False),
        ]
        manifest = _make_manifest(contracts)
        registry = AgentRegistry()
        _register(registry, "a")

        report = validate_contracts(manifest, registry)
        assert report.ready is True

    def test_ready_false_when_required_missing(self):
        contracts = [AgentContract(name="a")]
        manifest = _make_manifest(contracts)
        registry = AgentRegistry()

        report = validate_contracts(manifest, registry)
        assert report.ready is False

    def test_ready_false_when_schema_mismatch(self):
        contract_schema = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        }
        reg_schema = {
            "type": "object",
            "properties": {},
        }
        contracts = [AgentContract(name="a", input_schema=contract_schema)]
        manifest = _make_manifest(contracts)
        registry = AgentRegistry()
        _register(registry, "a", input_schema=reg_schema)

        report = validate_contracts(manifest, registry)
        assert report.ready is False

    def test_ready_true_with_optional_missing_only(self):
        """Optional missing does NOT block ready."""
        contracts = [AgentContract(name="opt", required=False)]
        manifest = _make_manifest(contracts)
        registry = AgentRegistry()

        report = validate_contracts(manifest, registry)
        assert report.ready is True


# ---------------------------------------------------------------------------
# ContractMatch registration dict
# ---------------------------------------------------------------------------


class TestContractMatchRegistration:
    def test_registration_dict_contains_agent_fields(self):
        contracts = [AgentContract(name="worker")]
        manifest = _make_manifest(contracts)
        registry = AgentRegistry()
        _register(registry, "worker")

        report = validate_contracts(manifest, registry)

        reg_dict = report.satisfied[0].registration
        assert reg_dict["name"] == "worker"
        assert reg_dict["agent_type"] == "local"
