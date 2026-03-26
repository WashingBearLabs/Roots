"""Tests for roots/packaging/defaults.py — default agent loading."""

from __future__ import annotations

from typing import Any

import pytest

from roots.agents.registry import AgentRegistry
from roots.packaging.defaults import load_defaults
from roots.packaging.manifest import RootManifest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SIMPLE_PROCESS = """\
id: test-process
name: Test Process
version: "1.0.0"
description: A test process
nodes:
  - id: step1
    type: agent
    label: First Step
    config:
      agent: ingest_incident
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

_DEFAULTS_AGENTS_PY = '''\
"""Default agents for testing."""


async def ingest_incident(input: dict) -> dict:
    return {"normalized": True}


async def threat_intel_lookup(input: dict) -> dict:
    return {"threat_score": 0.5}


def register_agents(roots):
    agents = [
        (
            "ingest_incident",
            ingest_incident,
            {"type": "object", "properties": {"source_ip": {"type": "string"}}},
            {"type": "object", "properties": {"normalized": {"type": "boolean"}}},
        ),
        (
            "threat_intel_lookup",
            threat_intel_lookup,
            {"type": "object", "properties": {}},
            {"type": "object", "properties": {"threat_score": {"type": "number"}}},
        ),
    ]
    registered = []
    for name, fn, in_schema, out_schema in agents:
        roots.register_agent(name, fn, input_schema=in_schema, output_schema=out_schema)
        registered.append(name)
    return registered
'''

_NO_REGISTER_PY = '''\
"""Module without register_agents."""


def hello():
    return "world"
'''


def _make_manifest(
    has_defaults: bool = False,
    defaults_module: str | None = None,
) -> RootManifest:
    return RootManifest(
        package_id="test-org/test-pkg",
        package_version="1.0.0",
        name="Test Package",
        description="A test package",
        roots_version=">=0.1.0",
        agent_contracts=[
            {"name": "ingest_incident", "description": "Normalizes incidents"},
        ],
        has_defaults=has_defaults,
        defaults_module=defaults_module,
    )


def _make_archive_contents(
    defaults_files: dict[str, str] | None = None,
) -> dict[str, bytes]:
    """Build a minimal archive_contents dict with optional defaults files."""
    contents: dict[str, bytes] = {
        "manifest.json": b"{}",
        "process.yaml": _SIMPLE_PROCESS.encode(),
    }
    if defaults_files:
        for name, code in defaults_files.items():
            contents[name] = code.encode()
    return contents


class FakeRoots:
    """Minimal stand-in for the Roots class, tracking register_agent calls."""

    def __init__(self) -> None:
        self._agent_registry = AgentRegistry()
        self.registered: list[str] = []

    def register_agent(
        self,
        name: str,
        callable: Any,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> None:
        self._agent_registry.register_local(
            name, callable, input_schema=input_schema, output_schema=output_schema
        )
        self.registered.append(name)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLoadDefaults:
    def test_skips_when_has_defaults_false(self) -> None:
        manifest = _make_manifest(has_defaults=False)
        roots = FakeRoots()
        result = load_defaults({}, manifest, roots)  # type: ignore[arg-type]
        assert result == []
        assert roots._agent_registry.list() == []

    def test_skips_when_defaults_module_is_none(self) -> None:
        manifest = _make_manifest(has_defaults=True, defaults_module=None)
        roots = FakeRoots()
        result = load_defaults({}, manifest, roots)  # type: ignore[arg-type]
        assert result == []

    def test_loads_and_registers_default_agents(self) -> None:
        manifest = _make_manifest(
            has_defaults=True, defaults_module="defaults.agents"
        )
        contents = _make_archive_contents(
            {"defaults/agents.py": _DEFAULTS_AGENTS_PY}
        )
        roots = FakeRoots()
        result = load_defaults(contents, manifest, roots)  # type: ignore[arg-type]

        assert result == ["ingest_incident", "threat_intel_lookup"]

        # Verify agents are actually in the registry
        assert roots._agent_registry.get("ingest_incident") is not None
        assert roots._agent_registry.get("threat_intel_lookup") is not None

    def test_returns_registered_agent_names(self) -> None:
        manifest = _make_manifest(
            has_defaults=True, defaults_module="defaults.agents"
        )
        contents = _make_archive_contents(
            {"defaults/agents.py": _DEFAULTS_AGENTS_PY}
        )
        roots = FakeRoots()
        result = load_defaults(contents, manifest, roots)  # type: ignore[arg-type]

        assert isinstance(result, list)
        assert len(result) == 2
        assert "ingest_incident" in result
        assert "threat_intel_lookup" in result

    def test_prints_security_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        manifest = _make_manifest(
            has_defaults=True, defaults_module="defaults.agents"
        )
        contents = _make_archive_contents(
            {"defaults/agents.py": _DEFAULTS_AGENTS_PY}
        )
        roots = FakeRoots()
        load_defaults(contents, manifest, roots)  # type: ignore[arg-type]

        captured = capsys.readouterr()
        assert "Loading default agents from package" in captured.out
        assert "trusted sources" in captured.out

    def test_raises_on_missing_register_agents(self) -> None:
        manifest = _make_manifest(
            has_defaults=True, defaults_module="defaults.agents"
        )
        contents = _make_archive_contents(
            {"defaults/agents.py": _NO_REGISTER_PY}
        )
        roots = FakeRoots()
        with pytest.raises(AttributeError, match="register_agents"):
            load_defaults(contents, manifest, roots)  # type: ignore[arg-type]

    def test_raises_on_missing_module(self) -> None:
        manifest = _make_manifest(
            has_defaults=True, defaults_module="defaults.agents"
        )
        # No defaults files in archive
        contents = _make_archive_contents()
        roots = FakeRoots()
        with pytest.raises(ImportError, match="Could not find module"):
            load_defaults(contents, manifest, roots)  # type: ignore[arg-type]

    def test_cleans_up_sys_modules(self) -> None:
        import sys

        manifest = _make_manifest(
            has_defaults=True, defaults_module="defaults.agents"
        )
        contents = _make_archive_contents(
            {"defaults/agents.py": _DEFAULTS_AGENTS_PY}
        )
        roots = FakeRoots()
        load_defaults(contents, manifest, roots)  # type: ignore[arg-type]

        # Module should be cleaned up after load
        assert "defaults.agents" not in sys.modules
        assert "defaults" not in sys.modules

    def test_single_module_defaults(self) -> None:
        """Test with defaults_module pointing to a top-level module file."""
        defaults_code = '''\
def register_agents(roots):
    async def simple_agent(input):
        return {}
    roots.register_agent("simple_agent", simple_agent)
    return ["simple_agent"]
'''
        manifest = _make_manifest(
            has_defaults=True, defaults_module="defaults.agents"
        )
        contents = _make_archive_contents(
            {"defaults/agents.py": defaults_code}
        )
        roots = FakeRoots()
        result = load_defaults(contents, manifest, roots)  # type: ignore[arg-type]
        assert result == ["simple_agent"]
