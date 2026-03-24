"""Tests for core schema definitions."""

import pytest

from roots.core.schema import (
    BackoffStrategy,
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
