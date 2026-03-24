"""Tests for core schema definitions."""

import pytest

from roots.core.schema import (
    Aggregation,
    AgentNodeConfig,
    AgentPoolNodeConfig,
    BackoffStrategy,
    DecisionEdge,
    DecisionMode,
    DecisionNodeConfig,
    ExecutionMode,
    NodeDefinition,
    NodeType,
    OnExhaustion,
    RetryConfig,
)


class TestNodeType:
    def test_all_eight_values(self) -> None:
        expected = {
            "agent",
            "agent_pool",
            "decision",
            "checkpoint",
            "fork",
            "join",
            "emit",
            "end",
        }
        assert {v.value for v in NodeType} == expected

    def test_is_str_enum(self) -> None:
        assert isinstance(NodeType.AGENT, str)
        assert NodeType.AGENT == "agent"


class TestRetryConfig:
    def test_defaults(self) -> None:
        rc = RetryConfig()
        assert rc.max_attempts == 1
        assert rc.backoff == BackoffStrategy.FIXED
        assert rc.backoff_seconds == 5.0
        assert rc.on_exhaustion == OnExhaustion.FAIL
        assert rc.fallback_edge is None

    def test_custom_values(self) -> None:
        rc = RetryConfig(
            max_attempts=3,
            backoff=BackoffStrategy.EXPONENTIAL,
            backoff_seconds=2.0,
            on_exhaustion=OnExhaustion.ROUTE,
            fallback_edge="error_handler",
        )
        assert rc.max_attempts == 3
        assert rc.backoff == BackoffStrategy.EXPONENTIAL
        assert rc.backoff_seconds == 2.0
        assert rc.on_exhaustion == OnExhaustion.ROUTE
        assert rc.fallback_edge == "error_handler"

    def test_route_requires_fallback_edge(self) -> None:
        with pytest.raises(ValueError, match="fallback_edge is required"):
            RetryConfig(on_exhaustion=OnExhaustion.ROUTE)

    def test_route_with_fallback_edge(self) -> None:
        rc = RetryConfig(
            on_exhaustion=OnExhaustion.ROUTE,
            fallback_edge="fallback",
        )
        assert rc.fallback_edge == "fallback"

    def test_fail_without_fallback_edge(self) -> None:
        rc = RetryConfig(on_exhaustion=OnExhaustion.FAIL)
        assert rc.fallback_edge is None

    def test_fail_with_fallback_edge_allowed(self) -> None:
        rc = RetryConfig(
            on_exhaustion=OnExhaustion.FAIL,
            fallback_edge="unused",
        )
        assert rc.fallback_edge == "unused"


class TestNodeDefinition:
    def test_valid_agent_node(self) -> None:
        node = NodeDefinition(
            id="node-1",
            type=NodeType.AGENT,
            label="My Agent",
            config={"model": "gpt-4"},
        )
        assert node.id == "node-1"
        assert node.type == NodeType.AGENT
        assert node.metadata == {}

    def test_valid_agent_node_with_retry(self) -> None:
        node = NodeDefinition(
            id="node-1",
            type=NodeType.AGENT,
            label="My Agent",
            config={},
            retry=RetryConfig(max_attempts=3),
        )
        assert node.retry is not None
        assert node.retry.max_attempts == 3

    def test_valid_agent_pool_node_with_retry(self) -> None:
        node = NodeDefinition(
            id="pool-1",
            type=NodeType.AGENT_POOL,
            label="Pool",
            config={},
            retry=RetryConfig(),
        )
        assert node.retry is not None

    def test_reject_retry_on_decision_node(self) -> None:
        with pytest.raises(
            ValueError, match="retry config is only valid on agent and agent_pool"
        ):
            NodeDefinition(
                id="d-1",
                type=NodeType.DECISION,
                label="Decision",
                config={},
                retry=RetryConfig(),
            )

    @pytest.mark.parametrize(
        "node_type",
        [
            NodeType.DECISION,
            NodeType.CHECKPOINT,
            NodeType.FORK,
            NodeType.JOIN,
            NodeType.EMIT,
            NodeType.END,
        ],
    )
    def test_reject_retry_on_non_agent_types(self, node_type: NodeType) -> None:
        with pytest.raises(
            ValueError, match="retry config is only valid on agent and agent_pool"
        ):
            NodeDefinition(
                id="n-1",
                type=node_type,
                label="Node",
                config={},
                retry=RetryConfig(),
            )

    def test_metadata_defaults_to_empty_dict(self) -> None:
        node = NodeDefinition(
            id="n-1",
            type=NodeType.END,
            label="End",
            config={},
        )
        assert node.metadata == {}

    def test_custom_metadata(self) -> None:
        node = NodeDefinition(
            id="n-1",
            type=NodeType.EMIT,
            label="Emit",
            config={},
            metadata={"author": "test"},
        )
        assert node.metadata == {"author": "test"}

    def test_all_node_types_valid_without_retry(self) -> None:
        for nt in NodeType:
            node = NodeDefinition(
                id=f"node-{nt.value}",
                type=nt,
                label=f"Node {nt.value}",
                config={},
            )
            assert node.type == nt


class TestAgentNodeConfig:
    def test_valid_config(self) -> None:
        cfg = AgentNodeConfig(agent="summarizer", output_key="summary")
        assert cfg.agent == "summarizer"
        assert cfg.output_key == "summary"

    def test_requires_agent(self) -> None:
        with pytest.raises(ValueError):
            AgentNodeConfig(output_key="summary")  # type: ignore[call-arg]

    def test_requires_output_key(self) -> None:
        with pytest.raises(ValueError):
            AgentNodeConfig(agent="summarizer")  # type: ignore[call-arg]


class TestAgentPoolNodeConfig:
    def test_valid_config(self) -> None:
        cfg = AgentPoolNodeConfig(
            agents=["a1", "a2"],
            execution_mode=ExecutionMode.PARALLEL,
            output_key="result",
        )
        assert cfg.agents == ["a1", "a2"]
        assert cfg.execution_mode == ExecutionMode.PARALLEL
        assert cfg.aggregation == Aggregation.MERGE_ALL
        assert cfg.output_key == "result"

    def test_requires_at_least_one_agent(self) -> None:
        with pytest.raises(ValueError, match="at least"):
            AgentPoolNodeConfig(
                agents=[],
                execution_mode=ExecutionMode.SEQUENTIAL,
                output_key="result",
            )

    def test_all_execution_modes(self) -> None:
        for mode in ExecutionMode:
            cfg = AgentPoolNodeConfig(
                agents=["a1"],
                execution_mode=mode,
                output_key="out",
            )
            assert cfg.execution_mode == mode


class TestDecisionEdge:
    def test_all_fields(self) -> None:
        edge = DecisionEdge(
            target="node-2",
            condition="x > 0",
            label="Positive",
            description="Route when x is positive",
        )
        assert edge.target == "node-2"
        assert edge.condition == "x > 0"
        assert edge.label == "Positive"
        assert edge.description == "Route when x is positive"

    def test_minimal_edge(self) -> None:
        edge = DecisionEdge(target="node-2")
        assert edge.target == "node-2"
        assert edge.condition is None
        assert edge.label is None
        assert edge.description is None


class TestDecisionNodeConfig:
    def test_valid_deterministic(self) -> None:
        cfg = DecisionNodeConfig(
            mode=DecisionMode.DETERMINISTIC,
            edges=[
                DecisionEdge(target="a", condition="x > 0"),
                DecisionEdge(target="b", condition="x <= 0"),
            ],
        )
        assert cfg.mode == DecisionMode.DETERMINISTIC
        assert len(cfg.edges) == 2

    def test_deterministic_requires_condition_on_every_edge(self) -> None:
        with pytest.raises(
            ValueError, match="non-empty condition.*deterministic"
        ):
            DecisionNodeConfig(
                mode=DecisionMode.DETERMINISTIC,
                edges=[
                    DecisionEdge(target="a", condition="x > 0"),
                    DecisionEdge(target="b"),
                ],
            )

    def test_deterministic_rejects_empty_condition(self) -> None:
        with pytest.raises(
            ValueError, match="non-empty condition.*deterministic"
        ):
            DecisionNodeConfig(
                mode=DecisionMode.DETERMINISTIC,
                edges=[DecisionEdge(target="a", condition="")],
            )

    @pytest.mark.parametrize(
        "mode",
        [
            DecisionMode.AI_BOUNDED,
            DecisionMode.AI_CHECKPOINT,
            DecisionMode.AI_AUTONOMOUS,
        ],
    )
    def test_ai_modes_require_confidence_threshold(
        self, mode: DecisionMode
    ) -> None:
        with pytest.raises(
            ValueError, match="confidence_threshold is required"
        ):
            DecisionNodeConfig(
                mode=mode,
                edges=[DecisionEdge(target="a")],
            )

    @pytest.mark.parametrize(
        "mode",
        [
            DecisionMode.AI_BOUNDED,
            DecisionMode.AI_CHECKPOINT,
            DecisionMode.AI_AUTONOMOUS,
        ],
    )
    def test_ai_modes_valid_with_threshold(self, mode: DecisionMode) -> None:
        cfg = DecisionNodeConfig(
            mode=mode,
            confidence_threshold=0.8,
            edges=[DecisionEdge(target="a")],
        )
        assert cfg.confidence_threshold == 0.8

    def test_requires_at_least_one_edge(self) -> None:
        with pytest.raises(ValueError, match="at least"):
            DecisionNodeConfig(
                mode=DecisionMode.DETERMINISTIC,
                edges=[],
            )

    def test_confidence_threshold_bounds(self) -> None:
        with pytest.raises(ValueError):
            DecisionNodeConfig(
                mode=DecisionMode.AI_BOUNDED,
                confidence_threshold=1.5,
                edges=[DecisionEdge(target="a")],
            )
        with pytest.raises(ValueError):
            DecisionNodeConfig(
                mode=DecisionMode.AI_BOUNDED,
                confidence_threshold=-0.1,
                edges=[DecisionEdge(target="a")],
            )

    def test_optional_fields(self) -> None:
        cfg = DecisionNodeConfig(
            mode=DecisionMode.AI_BOUNDED,
            confidence_threshold=0.5,
            model="gpt-4",
            context_prompt="Decide the route",
            checkpoint_prompt="Are you sure?",
            edges=[DecisionEdge(target="a")],
        )
        assert cfg.model == "gpt-4"
        assert cfg.context_prompt == "Decide the route"
        assert cfg.checkpoint_prompt == "Are you sure?"
