"""Tests for roots.packaging.extractor."""

from __future__ import annotations

from typing import Any

from roots.agents.registry import AgentRegistry
from roots.agents.types import AgentRegistration, AgentType
from roots.core.schema import ProcessDefinition
from roots.packaging.extractor import extract_agent_contracts, extract_config_overrides


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_process(nodes: list[dict[str, Any]]) -> ProcessDefinition:
    """Build a minimal ProcessDefinition with the given node dicts."""
    edges = []
    for i in range(len(nodes) - 1):
        edges.append({
            "from": nodes[i]["id"],
            "to": nodes[i + 1]["id"],
        })
    return ProcessDefinition(
        id="test-proc",
        name="Test Process",
        version="1.0.0",
        nodes=nodes,
        edges=edges,
        entry_point=nodes[0]["id"],
    )


def _agent_node(
    node_id: str,
    agent: str,
    *,
    retry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "id": node_id,
        "type": "agent",
        "label": node_id,
        "config": {"agent": agent, "output_key": f"{agent}_out"},
    }
    if retry is not None:
        node["retry"] = retry
    return node


def _pool_node(
    node_id: str,
    agents: list[str],
    mode: str = "parallel",
) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "agent_pool",
        "label": node_id,
        "config": {
            "agents": agents,
            "execution_mode": mode,
            "output_key": f"{node_id}_out",
        },
    }


def _decision_node(
    node_id: str,
    *,
    threshold: float = 0.8,
    model: str | None = None,
    context_prompt: str | None = None,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "decision",
        "label": node_id,
        "config": {
            "mode": "ai_bounded",
            "confidence_threshold": threshold,
            "model": model,
            "context_prompt": context_prompt,
            "edges": [
                {"target": "next", "label": "yes"},
                {"target": "other", "label": "no"},
            ],
        },
    }


def _checkpoint_node(node_id: str, prompt: str = "Review?") -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "checkpoint",
        "label": node_id,
        "config": {"prompt": prompt},
    }


def _join_node(
    node_id: str, *, allow_partial: bool = False
) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "join",
        "label": node_id,
        "config": {"allow_partial": allow_partial},
    }


def _end_node(node_id: str = "end") -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "end",
        "label": "End",
        "config": {"status": "completed"},
    }


def _make_registry(*agents: tuple[str, dict[str, Any]]) -> AgentRegistry:
    """Create a registry with the given (name, extra_kwargs) pairs."""
    registry = AgentRegistry()
    for name, extras in agents:
        reg = AgentRegistration(
            name=name,
            agent_type=AgentType.LOCAL,
            callable=lambda x: x,
            **extras,
        )
        registry.register(reg)
    return registry


# ---------------------------------------------------------------------------
# extract_agent_contracts
# ---------------------------------------------------------------------------

class TestExtractAgentContracts:
    def test_single_agent_node(self):
        proc = _make_process([
            _agent_node("a1", "triage"),
            _end_node(),
        ])
        contracts = extract_agent_contracts(proc)
        assert len(contracts) == 1
        assert contracts[0].name == "triage"
        assert contracts[0].required is True

    def test_agent_pool_parallel(self):
        proc = _make_process([
            _pool_node("pool1", ["alpha", "beta"], "parallel"),
            _end_node(),
        ])
        contracts = extract_agent_contracts(proc)
        assert len(contracts) == 2
        names = [c.name for c in contracts]
        assert "alpha" in names
        assert "beta" in names
        assert all(c.required for c in contracts)

    def test_agent_pool_first_pass_marks_fallbacks_optional(self):
        proc = _make_process([
            _pool_node("pool1", ["primary", "fallback1", "fallback2"], "first_pass"),
            _end_node(),
        ])
        contracts = extract_agent_contracts(proc)
        by_name = {c.name: c for c in contracts}
        assert by_name["primary"].required is True
        assert by_name["fallback1"].required is False
        assert by_name["fallback2"].required is False

    def test_deduplicates_agents(self):
        proc = _make_process([
            _agent_node("a1", "shared"),
            _agent_node("a2", "shared"),
            _end_node(),
        ])
        contracts = extract_agent_contracts(proc)
        assert len(contracts) == 1
        assert contracts[0].name == "shared"

    def test_dedup_across_node_and_pool(self):
        proc = _make_process([
            _agent_node("a1", "alpha"),
            _pool_node("pool1", ["alpha", "beta"], "parallel"),
            _end_node(),
        ])
        contracts = extract_agent_contracts(proc)
        assert len(contracts) == 2

    def test_pulls_schemas_from_registry(self):
        proc = _make_process([
            _agent_node("a1", "enricher"),
            _end_node(),
        ])
        registry = _make_registry(
            ("enricher", {
                "input_schema": {"type": "object", "properties": {"x": {"type": "string"}}},
                "output_schema": {"type": "object"},
                "timeout_seconds": 60,
                "metadata": {"description": "Enriches stuff"},
            }),
        )
        contracts = extract_agent_contracts(proc, registry=registry)
        assert len(contracts) == 1
        c = contracts[0]
        assert c.input_schema == {"type": "object", "properties": {"x": {"type": "string"}}}
        assert c.output_schema == {"type": "object"}
        assert c.timeout_seconds == 60
        assert c.description == "Enriches stuff"

    def test_placeholder_for_unregistered_agent(self):
        proc = _make_process([
            _agent_node("a1", "unknown_agent"),
            _end_node(),
        ])
        registry = _make_registry()
        contracts = extract_agent_contracts(proc, registry=registry)
        assert len(contracts) == 1
        c = contracts[0]
        assert c.name == "unknown_agent"
        assert c.input_schema is None
        assert c.output_schema is None

    def test_no_registry_creates_placeholders(self):
        proc = _make_process([
            _agent_node("a1", "some_agent"),
            _end_node(),
        ])
        contracts = extract_agent_contracts(proc)
        assert len(contracts) == 1
        assert contracts[0].input_schema is None

    def test_sorted_output(self):
        proc = _make_process([
            _agent_node("a1", "zebra"),
            _agent_node("a2", "alpha"),
            _end_node(),
        ])
        contracts = extract_agent_contracts(proc)
        assert [c.name for c in contracts] == ["alpha", "zebra"]

    def test_multi_node_process_with_pools(self):
        """Full integration: agent nodes, pools, dedup, first_pass, registry."""
        proc = _make_process([
            _agent_node("triage", "triage_agent"),
            _pool_node("enrich", ["enricher_a", "enricher_b"], "parallel"),
            _pool_node("respond", ["responder", "fallback_resp"], "first_pass"),
            _agent_node("notify", "triage_agent"),  # duplicate
            _end_node(),
        ])
        registry = _make_registry(
            ("triage_agent", {
                "input_schema": {"type": "object"},
                "metadata": {"description": "Triages alerts"},
            }),
            ("enricher_a", {
                "timeout_seconds": 120,
                "metadata": {},
            }),
        )
        contracts = extract_agent_contracts(proc, registry=registry)

        names = [c.name for c in contracts]
        assert names == sorted(names)

        # 5 unique agents
        assert len(contracts) == 5

        by_name = {c.name: c for c in contracts}

        # triage_agent: from registry
        assert by_name["triage_agent"].input_schema == {"type": "object"}
        assert by_name["triage_agent"].description == "Triages alerts"
        assert by_name["triage_agent"].required is True

        # enricher_a: from registry, no description in metadata
        assert by_name["enricher_a"].timeout_seconds == 120
        assert by_name["enricher_a"].description is None

        # enricher_b: not in registry, placeholder
        assert by_name["enricher_b"].input_schema is None

        # first_pass pool: responder required, fallback optional
        assert by_name["responder"].required is True
        assert by_name["fallback_resp"].required is False


# ---------------------------------------------------------------------------
# extract_config_overrides
# ---------------------------------------------------------------------------

class TestExtractConfigOverrides:
    def test_decision_threshold(self):
        proc = _make_process([
            _decision_node("d1", threshold=0.9),
            _end_node(),
        ])
        overrides = extract_config_overrides(proc)
        paths = [o.path for o in overrides]
        assert "nodes.d1.config.confidence_threshold" in paths
        ct = next(o for o in overrides if "confidence_threshold" in o.path)
        assert ct.default_value == 0.9
        assert ct.value_type == "float"

    def test_decision_model_and_prompt(self):
        proc = _make_process([
            _decision_node("d1", model="gpt-4", context_prompt="Evaluate risk"),
            _end_node(),
        ])
        overrides = extract_config_overrides(proc)
        paths = [o.path for o in overrides]
        assert "nodes.d1.config.model" in paths
        assert "nodes.d1.config.context_prompt" in paths

    def test_retry_settings(self):
        proc = _make_process([
            _agent_node("a1", "triage", retry={"max_attempts": 3, "backoff_seconds": 10.0}),
            _end_node(),
        ])
        overrides = extract_config_overrides(proc)
        paths = [o.path for o in overrides]
        assert "nodes.a1.config.retry.max_attempts" in paths
        assert "nodes.a1.config.retry.backoff_seconds" in paths

        ma = next(o for o in overrides if "max_attempts" in o.path)
        assert ma.default_value == 3
        assert ma.value_type == "int"

    def test_checkpoint_prompt(self):
        proc = _make_process([
            _checkpoint_node("cp1", "Are you sure?"),
            _end_node(),
        ])
        overrides = extract_config_overrides(proc)
        assert len(overrides) == 1
        assert overrides[0].path == "nodes.cp1.config.prompt"
        assert overrides[0].default_value == "Are you sure?"

    def test_join_allow_partial(self):
        proc = _make_process([
            _join_node("j1", allow_partial=True),
            _end_node(),
        ])
        overrides = extract_config_overrides(proc)
        assert len(overrides) == 1
        assert overrides[0].path == "nodes.j1.config.allow_partial"
        assert overrides[0].default_value is True
        assert overrides[0].value_type == "bool"

    def test_dot_notation_format(self):
        proc = _make_process([
            _decision_node("my_decision", threshold=0.7),
            _end_node(),
        ])
        overrides = extract_config_overrides(proc)
        for o in overrides:
            assert o.path.startswith("nodes.my_decision.config.")
            assert " " not in o.path

    def test_multi_node_extraction(self):
        """Overrides extracted from a process with multiple tunable node types."""
        proc = _make_process([
            _agent_node("a1", "triage", retry={"max_attempts": 2, "backoff_seconds": 5.0}),
            _decision_node("d1", threshold=0.85, model="claude-3"),
            _checkpoint_node("cp1", "Approve?"),
            _join_node("j1", allow_partial=False),
            _end_node(),
        ])
        overrides = extract_config_overrides(proc)
        paths = [o.path for o in overrides]

        # Retry
        assert "nodes.a1.config.retry.max_attempts" in paths
        assert "nodes.a1.config.retry.backoff_seconds" in paths
        # Decision
        assert "nodes.d1.config.confidence_threshold" in paths
        assert "nodes.d1.config.model" in paths
        # Checkpoint
        assert "nodes.cp1.config.prompt" in paths
        # Join
        assert "nodes.j1.config.allow_partial" in paths
