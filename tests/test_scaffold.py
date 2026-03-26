"""Tests for roots.packaging.scaffold — default agent scaffolding."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from roots.packaging.scaffold import (
    _generate_agents_module,
    _generate_stub_return,
    _safe_function_name,
    scaffold_defaults,
)
from roots.packaging.manifest import AgentContract


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MULTI_AGENT_PROCESS = """\
id: incident-response
name: Incident Response Pipeline
version: "1.0.0"
description: Automated incident triage and response
nodes:
  - id: triage
    type: agent
    label: Triage
    config:
      agent: threat_intel_lookup
      output_key: triage_out
  - id: enrich
    type: agent_pool
    label: Enrichment Pool
    config:
      agents:
        - geo_ip_enricher
        - reputation_checker
      execution_mode: parallel
      output_key: enrich_out
  - id: respond
    type: agent
    label: Response
    config:
      agent: response_action
      output_key: respond_out
  - id: done
    type: end
    label: Done
    config:
      status: completed
edges:
  - from: triage
    to: enrich
  - from: enrich
    to: respond
  - from: respond
    to: done
entry_point: triage
"""

_SINGLE_AGENT_PROCESS = """\
id: simple-proc
name: Simple Process
version: "1.0.0"
nodes:
  - id: step1
    type: agent
    label: Step 1
    config:
      agent: worker_agent
      output_key: step1_out
  - id: done
    type: end
    label: Done
    config:
      status: completed
edges:
  - from: step1
    to: done
entry_point: step1
"""


def _write_process(tmp_path: Path, content: str = _MULTI_AGENT_PROCESS) -> Path:
    p = tmp_path / "process.yaml"
    p.write_text(content)
    return p


# ---------------------------------------------------------------------------
# scaffold_defaults — integration
# ---------------------------------------------------------------------------

class TestScaffoldDefaults:
    def test_creates_defaults_directory(self, tmp_path: Path):
        process_file = _write_process(tmp_path)
        result = scaffold_defaults(process_file)

        assert result.is_dir()
        assert result.name == "defaults"
        assert (result / "__init__.py").exists()
        assert (result / "agents.py").exists()

    def test_custom_output_dir(self, tmp_path: Path):
        process_file = _write_process(tmp_path)
        custom_dir = tmp_path / "my_defaults"

        result = scaffold_defaults(process_file, output_dir=custom_dir)

        assert result == custom_dir
        assert (custom_dir / "__init__.py").exists()
        assert (custom_dir / "agents.py").exists()

    def test_generated_code_is_valid_python(self, tmp_path: Path):
        process_file = _write_process(tmp_path)
        scaffold_defaults(process_file)

        agents_code = (tmp_path / "defaults" / "agents.py").read_text()
        # Should parse without syntax errors
        ast.parse(agents_code)

    def test_stubs_match_extracted_contracts(self, tmp_path: Path):
        process_file = _write_process(tmp_path)
        scaffold_defaults(process_file)

        agents_code = (tmp_path / "defaults" / "agents.py").read_text()

        # All 4 agent names should appear as function definitions
        assert "async def threat_intel_lookup(" in agents_code
        assert "async def geo_ip_enricher(" in agents_code
        assert "async def reputation_checker(" in agents_code
        assert "async def response_action(" in agents_code

    def test_register_agents_function_present(self, tmp_path: Path):
        process_file = _write_process(tmp_path)
        scaffold_defaults(process_file)

        agents_code = (tmp_path / "defaults" / "agents.py").read_text()

        assert "def register_agents(roots):" in agents_code
        # All agents registered
        assert '"threat_intel_lookup"' in agents_code
        assert '"geo_ip_enricher"' in agents_code
        assert '"reputation_checker"' in agents_code
        assert '"response_action"' in agents_code

    def test_stubs_return_dict(self, tmp_path: Path):
        process_file = _write_process(tmp_path)
        scaffold_defaults(process_file)

        agents_code = (tmp_path / "defaults" / "agents.py").read_text()

        # Each stub should have a return statement
        tree = ast.parse(agents_code)
        async_funcs = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
        ]
        assert len(async_funcs) == 4

        for func in async_funcs:
            # Each async function should have at least one return
            returns = [n for n in ast.walk(func) if isinstance(n, ast.Return)]
            assert len(returns) >= 1, f"{func.name} has no return statement"


# ---------------------------------------------------------------------------
# _generate_agents_module — unit tests
# ---------------------------------------------------------------------------

class TestGenerateAgentsModule:
    def test_with_schemas_in_docstring(self):
        contracts = [
            AgentContract(
                name="threat_intel_lookup",
                description="Enriches with threat intelligence data.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "source_ip": {"type": "string"},
                        "event_type": {"type": "string"},
                    },
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "threat_score": {"type": "number"},
                        "known_iocs": {"type": "array"},
                    },
                },
            ),
        ]
        code = _generate_agents_module(contracts, "Test Process")

        assert "Enriches with threat intelligence data." in code
        assert "source_ip: string" in code
        assert "threat_score: number" in code
        ast.parse(code)  # Valid Python

    def test_stub_return_matches_output_schema(self):
        contracts = [
            AgentContract(
                name="scorer",
                output_schema={
                    "type": "object",
                    "properties": {
                        "score": {"type": "number"},
                        "tags": {"type": "array"},
                        "valid": {"type": "boolean"},
                    },
                },
            ),
        ]
        code = _generate_agents_module(contracts, "Test")

        # The return value should contain defaults matching the schema types
        assert "'score': 0.0" in code
        assert "'tags': []" in code
        assert "'valid': False" in code

    def test_no_contracts_produces_empty_register(self):
        code = _generate_agents_module([], "Empty Process")
        assert "def register_agents(roots):" in code
        assert "return []" in code
        ast.parse(code)

    def test_register_passes_schemas_when_available(self):
        contracts = [
            AgentContract(
                name="enricher",
                input_schema={"type": "object"},
                output_schema={"type": "object", "properties": {"x": {"type": "string"}}},
            ),
        ]
        code = _generate_agents_module(contracts, "Test")
        assert "input_schema=" in code
        assert "output_schema=" in code

    def test_register_omits_schemas_when_none(self):
        contracts = [
            AgentContract(name="basic_agent"),
        ]
        code = _generate_agents_module(contracts, "Test")
        assert "input_schema=" not in code
        assert "output_schema=" not in code


# ---------------------------------------------------------------------------
# _safe_function_name
# ---------------------------------------------------------------------------

class TestSafeFunctionName:
    def test_normal_name(self):
        assert _safe_function_name("threat_intel_lookup") == "threat_intel_lookup"

    def test_hyphenated_name(self):
        assert _safe_function_name("my-agent") == "my_agent"

    def test_name_starting_with_digit(self):
        assert _safe_function_name("123agent") == "agent_123agent"


# ---------------------------------------------------------------------------
# _generate_stub_return
# ---------------------------------------------------------------------------

class TestGenerateStubReturn:
    def test_none_schema(self):
        assert _generate_stub_return(None) == "{}"

    def test_empty_schema(self):
        assert _generate_stub_return({}) == "{}"

    def test_schema_with_properties(self):
        result = _generate_stub_return({
            "type": "object",
            "properties": {
                "score": {"type": "number"},
                "label": {"type": "string"},
            },
        })
        # Should be a valid Python repr of a dict
        value = eval(result)  # noqa: S307
        assert value == {"score": 0.0, "label": ""}
