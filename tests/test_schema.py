"""Tests for core schema definitions."""

import pytest

from roots.core.schema import (
    Aggregation,
    AgentNodeConfig,
    AgentPoolNodeConfig,
    BackoffStrategy,
    CheckpointNodeConfig,
    DecisionEdge,
    DecisionMode,
    DecisionNodeConfig,
    EdgeDefinition,
    EmitNodeConfig,
    EndNodeConfig,
    EndStatus,
    ExecutionMode,
    ForkNodeConfig,
    JoinNodeConfig,
    MergeStrategy,
    NodeDefinition,
    NodeType,
    OnExhaustion,
    ProcessDefinition,
    RetryConfig,
    TieBreak,
    VoteConfig,
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


from typing import Any

VALID_CONFIGS: dict[NodeType, dict[str, Any]] = {
    NodeType.AGENT: {"agent": "summarizer", "output_key": "summary"},
    NodeType.AGENT_POOL: {
        "agents": ["a1"],
        "execution_mode": "parallel",
        "output_key": "out",
    },
    NodeType.DECISION: {
        "mode": "deterministic",
        "edges": [{"target": "a", "condition": "x > 0"}],
    },
    NodeType.CHECKPOINT: {"prompt": "Review this"},
    NodeType.FORK: {},
    NodeType.JOIN: {},
    NodeType.EMIT: {"event_type": "process.done"},
    NodeType.END: {"status": "completed"},
}


class TestNodeDefinition:
    def test_valid_agent_node(self) -> None:
        node = NodeDefinition(
            id="node-1",
            type=NodeType.AGENT,
            label="My Agent",
            config={"agent": "summarizer", "output_key": "summary"},
        )
        assert node.id == "node-1"
        assert node.type == NodeType.AGENT
        assert node.metadata == {}
        assert isinstance(node.config, AgentNodeConfig)

    def test_valid_agent_node_with_retry(self) -> None:
        node = NodeDefinition(
            id="node-1",
            type=NodeType.AGENT,
            label="My Agent",
            config={"agent": "summarizer", "output_key": "summary"},
            retry=RetryConfig(max_attempts=3),
        )
        assert node.retry is not None
        assert node.retry.max_attempts == 3

    def test_valid_agent_pool_node_with_retry(self) -> None:
        node = NodeDefinition(
            id="pool-1",
            type=NodeType.AGENT_POOL,
            label="Pool",
            config={
                "agents": ["a1"],
                "execution_mode": "parallel",
                "output_key": "out",
            },
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
                config={
                    "mode": "deterministic",
                    "edges": [{"target": "a", "condition": "x > 0"}],
                },
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
                config=VALID_CONFIGS[node_type],
                retry=RetryConfig(),
            )

    def test_metadata_defaults_to_empty_dict(self) -> None:
        node = NodeDefinition(
            id="n-1",
            type=NodeType.END,
            label="End",
            config={"status": "completed"},
        )
        assert node.metadata == {}

    def test_custom_metadata(self) -> None:
        node = NodeDefinition(
            id="n-1",
            type=NodeType.EMIT,
            label="Emit",
            config={"event_type": "process.done"},
            metadata={"author": "test"},
        )
        assert node.metadata == {"author": "test"}

    def test_all_node_types_valid_without_retry(self) -> None:
        for nt in NodeType:
            node = NodeDefinition(
                id=f"node-{nt.value}",
                type=nt,
                label=f"Node {nt.value}",
                config=VALID_CONFIGS[nt],
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


class TestAggregationEnum:
    def test_all_values(self) -> None:
        expected = {"merge_all", "majority_vote", "weighted_vote", "unanimous"}
        assert {v.value for v in Aggregation} == expected

    def test_is_str_enum(self) -> None:
        assert isinstance(Aggregation.MAJORITY_VOTE, str)
        assert Aggregation.MAJORITY_VOTE == "majority_vote"


class TestTieBreakEnum:
    def test_all_values(self) -> None:
        assert {v.value for v in TieBreak} == {"first_agent", "reject"}

    def test_is_str_enum(self) -> None:
        assert isinstance(TieBreak.FIRST_AGENT, str)
        assert TieBreak.FIRST_AGENT == "first_agent"
        assert TieBreak.REJECT == "reject"


class TestVoteConfig:
    def test_minimal(self) -> None:
        cfg = VoteConfig(vote_key="decision")
        assert cfg.vote_key == "decision"
        assert cfg.threshold == 0.5
        assert cfg.weights is None
        assert cfg.tie_break == TieBreak.FIRST_AGENT

    def test_all_fields(self) -> None:
        cfg = VoteConfig(
            vote_key="verdict",
            threshold=0.75,
            weights={"a1": 2.0, "a2": 1.0},
            tie_break=TieBreak.REJECT,
        )
        assert cfg.vote_key == "verdict"
        assert cfg.threshold == 0.75
        assert cfg.weights == {"a1": 2.0, "a2": 1.0}
        assert cfg.tie_break == TieBreak.REJECT

    def test_threshold_bounds(self) -> None:
        with pytest.raises(ValueError):
            VoteConfig(vote_key="x", threshold=-0.1)
        with pytest.raises(ValueError):
            VoteConfig(vote_key="x", threshold=1.1)

    def test_threshold_boundary_values(self) -> None:
        assert VoteConfig(vote_key="x", threshold=0.0).threshold == 0.0
        assert VoteConfig(vote_key="x", threshold=1.0).threshold == 1.0

    def test_requires_vote_key(self) -> None:
        with pytest.raises(ValueError):
            VoteConfig()  # type: ignore[call-arg]


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
        assert cfg.vote_config is None

    def test_requires_at_least_one_agent(self) -> None:
        with pytest.raises(ValueError, match="at least"):
            AgentPoolNodeConfig(
                agents=[],
                execution_mode=ExecutionMode.SEQUENTIAL,
                output_key="result",
            )

    def test_all_execution_modes_with_merge_all(self) -> None:
        for mode in ExecutionMode:
            cfg = AgentPoolNodeConfig(
                agents=["a1"],
                execution_mode=mode,
                output_key="out",
            )
            assert cfg.execution_mode == mode

    def test_majority_vote_requires_vote_config(self) -> None:
        with pytest.raises(ValueError, match="vote_config is required"):
            AgentPoolNodeConfig(
                agents=["a1", "a2"],
                execution_mode=ExecutionMode.PARALLEL,
                aggregation=Aggregation.MAJORITY_VOTE,
                output_key="out",
            )

    def test_weighted_vote_requires_vote_config(self) -> None:
        with pytest.raises(ValueError, match="vote_config is required"):
            AgentPoolNodeConfig(
                agents=["a1"],
                execution_mode=ExecutionMode.PARALLEL,
                aggregation=Aggregation.WEIGHTED_VOTE,
                output_key="out",
            )

    def test_unanimous_requires_vote_config(self) -> None:
        with pytest.raises(ValueError, match="vote_config is required"):
            AgentPoolNodeConfig(
                agents=["a1"],
                execution_mode=ExecutionMode.PARALLEL,
                aggregation=Aggregation.UNANIMOUS,
                output_key="out",
            )

    def test_vote_config_rejected_for_merge_all(self) -> None:
        with pytest.raises(ValueError, match="not allowed when aggregation is 'merge_all'"):
            AgentPoolNodeConfig(
                agents=["a1"],
                execution_mode=ExecutionMode.PARALLEL,
                aggregation=Aggregation.MERGE_ALL,
                output_key="out",
                vote_config=VoteConfig(vote_key="decision"),
            )

    def test_valid_majority_vote(self) -> None:
        cfg = AgentPoolNodeConfig(
            agents=["a1", "a2", "a3"],
            execution_mode=ExecutionMode.PARALLEL,
            aggregation=Aggregation.MAJORITY_VOTE,
            output_key="out",
            vote_config=VoteConfig(vote_key="decision", threshold=0.6),
        )
        assert cfg.aggregation == Aggregation.MAJORITY_VOTE
        assert cfg.vote_config is not None
        assert cfg.vote_config.vote_key == "decision"

    def test_valid_unanimous(self) -> None:
        cfg = AgentPoolNodeConfig(
            agents=["a1", "a2"],
            execution_mode=ExecutionMode.SEQUENTIAL,
            aggregation=Aggregation.UNANIMOUS,
            output_key="out",
            vote_config=VoteConfig(vote_key="verdict"),
        )
        assert cfg.aggregation == Aggregation.UNANIMOUS

    def test_valid_weighted_vote(self) -> None:
        cfg = AgentPoolNodeConfig(
            agents=["a1", "a2"],
            execution_mode=ExecutionMode.PARALLEL,
            aggregation=Aggregation.WEIGHTED_VOTE,
            output_key="out",
            vote_config=VoteConfig(
                vote_key="decision",
                weights={"a1": 2.0, "a2": 1.0},
            ),
        )
        assert cfg.aggregation == Aggregation.WEIGHTED_VOTE
        assert cfg.vote_config is not None
        assert cfg.vote_config.weights == {"a1": 2.0, "a2": 1.0}

    def test_weighted_vote_requires_weights(self) -> None:
        with pytest.raises(ValueError, match="weights are required"):
            AgentPoolNodeConfig(
                agents=["a1", "a2"],
                execution_mode=ExecutionMode.PARALLEL,
                aggregation=Aggregation.WEIGHTED_VOTE,
                output_key="out",
                vote_config=VoteConfig(vote_key="decision"),
            )

    def test_weight_keys_must_be_subset_of_agents(self) -> None:
        with pytest.raises(ValueError, match="not in the agents list"):
            AgentPoolNodeConfig(
                agents=["a1", "a2"],
                execution_mode=ExecutionMode.PARALLEL,
                aggregation=Aggregation.WEIGHTED_VOTE,
                output_key="out",
                vote_config=VoteConfig(
                    vote_key="decision",
                    weights={"a1": 1.0, "unknown_agent": 2.0},
                ),
            )

    def test_partial_weights_valid(self) -> None:
        cfg = AgentPoolNodeConfig(
            agents=["a1", "a2", "a3"],
            execution_mode=ExecutionMode.PARALLEL,
            aggregation=Aggregation.WEIGHTED_VOTE,
            output_key="out",
            vote_config=VoteConfig(
                vote_key="decision",
                weights={"a1": 2.0},
            ),
        )
        assert cfg.vote_config is not None
        assert cfg.vote_config.weights == {"a1": 2.0}

    def test_first_pass_rejected_with_majority_vote(self) -> None:
        with pytest.raises(ValueError, match="not compatible with execution_mode 'first_pass'"):
            AgentPoolNodeConfig(
                agents=["a1", "a2"],
                execution_mode=ExecutionMode.FIRST_PASS,
                aggregation=Aggregation.MAJORITY_VOTE,
                output_key="out",
                vote_config=VoteConfig(vote_key="decision"),
            )

    def test_first_pass_rejected_with_weighted_vote(self) -> None:
        with pytest.raises(ValueError, match="not compatible with execution_mode 'first_pass'"):
            AgentPoolNodeConfig(
                agents=["a1", "a2"],
                execution_mode=ExecutionMode.FIRST_PASS,
                aggregation=Aggregation.WEIGHTED_VOTE,
                output_key="out",
                vote_config=VoteConfig(
                    vote_key="decision",
                    weights={"a1": 1.0, "a2": 2.0},
                ),
            )

    def test_first_pass_rejected_with_unanimous(self) -> None:
        with pytest.raises(ValueError, match="not compatible with execution_mode 'first_pass'"):
            AgentPoolNodeConfig(
                agents=["a1"],
                execution_mode=ExecutionMode.FIRST_PASS,
                aggregation=Aggregation.UNANIMOUS,
                output_key="out",
                vote_config=VoteConfig(vote_key="decision"),
            )


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


class TestCheckpointNodeConfig:
    def test_valid(self) -> None:
        cfg = CheckpointNodeConfig(prompt="Review this step")
        assert cfg.prompt == "Review this step"

    def test_requires_prompt(self) -> None:
        with pytest.raises(ValueError):
            CheckpointNodeConfig()  # type: ignore[call-arg]


class TestForkNodeConfig:
    def test_valid_empty(self) -> None:
        cfg = ForkNodeConfig()
        assert isinstance(cfg, ForkNodeConfig)


class TestJoinNodeConfig:
    def test_defaults(self) -> None:
        cfg = JoinNodeConfig()
        assert cfg.merge_strategy == MergeStrategy.MERGE_ALL
        assert cfg.collect_key is None
        assert cfg.allow_partial is False

    def test_collect_requires_collect_key(self) -> None:
        with pytest.raises(ValueError, match="collect_key is required"):
            JoinNodeConfig(merge_strategy=MergeStrategy.COLLECT)

    def test_collect_with_collect_key(self) -> None:
        cfg = JoinNodeConfig(
            merge_strategy=MergeStrategy.COLLECT,
            collect_key="results",
        )
        assert cfg.collect_key == "results"

    def test_merge_all_ignores_collect_key(self) -> None:
        cfg = JoinNodeConfig(
            merge_strategy=MergeStrategy.MERGE_ALL,
            collect_key="optional",
        )
        assert cfg.collect_key == "optional"

    def test_allow_partial(self) -> None:
        cfg = JoinNodeConfig(allow_partial=True)
        assert cfg.allow_partial is True


class TestEmitNodeConfig:
    def test_valid(self) -> None:
        cfg = EmitNodeConfig(event_type="process.quality_check_complete")
        assert cfg.event_type == "process.quality_check_complete"
        assert cfg.payload_keys == []

    def test_with_payload_keys(self) -> None:
        cfg = EmitNodeConfig(
            event_type="process.done",
            payload_keys=["score", "summary"],
        )
        assert cfg.payload_keys == ["score", "summary"]

    def test_requires_event_type(self) -> None:
        with pytest.raises(ValueError):
            EmitNodeConfig()  # type: ignore[call-arg]


class TestEndNodeConfig:
    def test_completed(self) -> None:
        cfg = EndNodeConfig(status=EndStatus.COMPLETED)
        assert cfg.status == EndStatus.COMPLETED

    def test_failed(self) -> None:
        cfg = EndNodeConfig(status=EndStatus.FAILED)
        assert cfg.status == EndStatus.FAILED

    def test_requires_status(self) -> None:
        with pytest.raises(ValueError):
            EndNodeConfig()  # type: ignore[call-arg]

    def test_invalid_status(self) -> None:
        with pytest.raises(ValueError):
            EndNodeConfig(status="unknown")  # type: ignore[arg-type]


class TestConfigDiscriminator:
    """Tests for type-discriminated config parsing on NodeDefinition."""

    @pytest.mark.parametrize("node_type", list(NodeType))
    def test_dict_config_parsed_to_typed_model(
        self, node_type: NodeType
    ) -> None:
        node = NodeDefinition(
            id="n-1",
            type=node_type,
            label="Test",
            config=VALID_CONFIGS[node_type],
        )
        assert not isinstance(node.config, dict)

    def test_agent_config_parsed(self) -> None:
        node = NodeDefinition(
            id="a-1",
            type=NodeType.AGENT,
            label="Agent",
            config={"agent": "summarizer", "output_key": "summary"},
        )
        assert isinstance(node.config, AgentNodeConfig)
        assert node.config.agent == "summarizer"

    def test_checkpoint_config_parsed(self) -> None:
        node = NodeDefinition(
            id="cp-1",
            type=NodeType.CHECKPOINT,
            label="Checkpoint",
            config={"prompt": "Review this"},
        )
        assert isinstance(node.config, CheckpointNodeConfig)
        assert node.config.prompt == "Review this"

    def test_fork_config_parsed(self) -> None:
        node = NodeDefinition(
            id="f-1",
            type=NodeType.FORK,
            label="Fork",
            config={},
        )
        assert isinstance(node.config, ForkNodeConfig)

    def test_join_config_parsed(self) -> None:
        node = NodeDefinition(
            id="j-1",
            type=NodeType.JOIN,
            label="Join",
            config={"merge_strategy": "collect", "collect_key": "results"},
        )
        assert isinstance(node.config, JoinNodeConfig)
        assert node.config.merge_strategy == MergeStrategy.COLLECT

    def test_emit_config_parsed(self) -> None:
        node = NodeDefinition(
            id="e-1",
            type=NodeType.EMIT,
            label="Emit",
            config={"event_type": "process.done", "payload_keys": ["score"]},
        )
        assert isinstance(node.config, EmitNodeConfig)
        assert node.config.payload_keys == ["score"]

    def test_end_config_parsed(self) -> None:
        node = NodeDefinition(
            id="end-1",
            type=NodeType.END,
            label="End",
            config={"status": "completed"},
        )
        assert isinstance(node.config, EndNodeConfig)
        assert node.config.status == EndStatus.COMPLETED

    def test_already_typed_config_not_reparsed(self) -> None:
        typed_config = AgentNodeConfig(agent="summarizer", output_key="out")
        node = NodeDefinition(
            id="a-1",
            type=NodeType.AGENT,
            label="Agent",
            config=typed_config,
        )
        assert node.config is typed_config

    def test_mismatched_config_raises_with_context(self) -> None:
        with pytest.raises(ValueError, match="my_node.*agent"):
            NodeDefinition(
                id="my_node",
                type=NodeType.AGENT,
                label="Agent",
                config={"wrong_field": "value"},
            )

    def test_missing_required_field_raises_with_context(self) -> None:
        with pytest.raises(ValueError, match="cp-1.*checkpoint"):
            NodeDefinition(
                id="cp-1",
                type=NodeType.CHECKPOINT,
                label="Checkpoint",
                config={},
            )


def _make_node(
    id: str,
    node_type: NodeType = NodeType.AGENT,
    config: dict[str, Any] | None = None,
) -> NodeDefinition:
    """Helper to create a NodeDefinition for tests."""
    if config is None:
        config = VALID_CONFIGS[node_type]
    return NodeDefinition(id=id, type=node_type, label=f"Node {id}", config=config)


def _make_process(**overrides: Any) -> ProcessDefinition:
    """Helper to create a minimal valid ProcessDefinition."""
    defaults: dict[str, Any] = {
        "id": "proc-1",
        "name": "Test Process",
        "version": "1.0.0",
        "nodes": [
            _make_node("start"),
            _make_node("end", NodeType.END),
        ],
        "edges": [
            EdgeDefinition(from_node="start", to_node="end"),
        ],
        "entry_point": "start",
    }
    defaults.update(overrides)
    return ProcessDefinition(**defaults)


class TestEdgeDefinition:
    def test_create_with_aliases(self) -> None:
        edge = EdgeDefinition(**{"from": "a", "to": "b"})
        assert edge.from_node == "a"
        assert edge.to_node == "b"

    def test_create_with_field_names(self) -> None:
        edge = EdgeDefinition(from_node="a", to_node="b")
        assert edge.from_node == "a"
        assert edge.to_node == "b"

    def test_auto_generated_id(self) -> None:
        edge = EdgeDefinition(from_node="a", to_node="b")
        assert edge.id is not None
        assert len(edge.id) > 0

    def test_unique_ids(self) -> None:
        e1 = EdgeDefinition(from_node="a", to_node="b")
        e2 = EdgeDefinition(from_node="a", to_node="b")
        assert e1.id != e2.id

    def test_explicit_id(self) -> None:
        edge = EdgeDefinition(id="my-edge", from_node="a", to_node="b")
        assert edge.id == "my-edge"

    def test_optional_fields_default(self) -> None:
        edge = EdgeDefinition(from_node="a", to_node="b")
        assert edge.label is None
        assert edge.condition is None
        assert edge.emit_event is False

    def test_all_fields(self) -> None:
        edge = EdgeDefinition(
            id="e-1",
            from_node="a",
            to_node="b",
            label="Next",
            condition="x > 0",
            emit_event=True,
        )
        assert edge.id == "e-1"
        assert edge.from_node == "a"
        assert edge.to_node == "b"
        assert edge.label == "Next"
        assert edge.condition == "x > 0"
        assert edge.emit_event is True

    def test_serialization_uses_aliases(self) -> None:
        edge = EdgeDefinition(from_node="a", to_node="b")
        data = edge.model_dump(by_alias=True, mode="json")
        assert "from" in data
        assert "to" in data
        assert "from_node" not in data
        assert "to_node" not in data


class TestProcessDefinition:
    def test_valid_process(self) -> None:
        proc = _make_process()
        assert proc.id == "proc-1"
        assert proc.name == "Test Process"
        assert proc.version == "1.0.0"
        assert len(proc.nodes) == 2
        assert len(proc.edges) == 1
        assert proc.entry_point == "start"

    def test_metadata_defaults_to_empty_dict(self) -> None:
        proc = _make_process()
        assert proc.metadata == {}

    def test_metadata_with_values(self) -> None:
        meta = {"package_id": "pkg-1", "package_version": "1.0.0"}
        proc = _make_process(metadata=meta)
        assert proc.metadata == meta

    def test_metadata_round_trip_through_serialize(self) -> None:
        import json

        from pydantic import BaseModel as _BaseModel

        meta = {
            "package_id": "pkg-1",
            "package_version": "2.0.0",
            "installed_from": "/tmp/test.root",
            "installed_at": "2026-03-25T12:00:00Z",
        }
        proc = _make_process(metadata=meta)
        data = proc.model_dump(by_alias=True, mode="json")
        for i, node in enumerate(proc.nodes):
            if isinstance(node.config, _BaseModel):
                data["nodes"][i]["config"] = node.config.model_dump(mode="json")
        json_str = json.dumps(data)
        reloaded = ProcessDefinition.model_validate(json.loads(json_str))
        assert reloaded.metadata == meta

    def test_metadata_absent_in_dict_gives_empty(self) -> None:
        data = {
            "id": "proc-1",
            "name": "Test",
            "version": "1.0.0",
            "nodes": [
                {"id": "start", "type": "agent", "label": "Start",
                 "config": {"agent": "a", "output_key": "out"}},
                {"id": "end", "type": "end", "label": "End",
                 "config": {"status": "completed"}},
            ],
            "edges": [{"from": "start", "to": "end"}],
            "entry_point": "start",
        }
        proc = ProcessDefinition.model_validate(data)
        assert proc.metadata == {}

    def test_optional_fields_default(self) -> None:
        proc = _make_process()
        assert proc.description is None
        assert proc.work_item_schema is None

    def test_optional_fields_set(self) -> None:
        proc = _make_process(
            description="A test process",
            work_item_schema="schemas/work_item.json",
        )
        assert proc.description == "A test process"
        assert proc.work_item_schema == "schemas/work_item.json"

    def test_entry_point_must_exist(self) -> None:
        with pytest.raises(ValueError, match="entry_point.*nonexistent"):
            _make_process(entry_point="nonexistent")

    def test_edge_from_node_must_exist(self) -> None:
        with pytest.raises(ValueError, match="unknown from node.*ghost"):
            _make_process(
                edges=[EdgeDefinition(from_node="ghost", to_node="end")]
            )

    def test_edge_to_node_must_exist(self) -> None:
        with pytest.raises(ValueError, match="unknown to node.*ghost"):
            _make_process(
                edges=[EdgeDefinition(from_node="start", to_node="ghost")]
            )

    def test_get_node_found(self) -> None:
        proc = _make_process()
        node = proc.get_node("start")
        assert node is not None
        assert node.id == "start"

    def test_get_node_not_found(self) -> None:
        proc = _make_process()
        assert proc.get_node("nonexistent") is None

    def test_get_outbound_edges_non_decision(self) -> None:
        proc = _make_process()
        edges = proc.get_outbound_edges("start")
        assert len(edges) == 1
        assert isinstance(edges[0], EdgeDefinition)
        assert edges[0].to_node == "end"

    def test_get_outbound_edges_no_edges(self) -> None:
        proc = _make_process()
        edges = proc.get_outbound_edges("end")
        assert edges == []

    def test_get_outbound_edges_unknown_node(self) -> None:
        proc = _make_process()
        edges = proc.get_outbound_edges("nonexistent")
        assert edges == []

    def test_get_outbound_edges_decision_node_uses_config_edges(self) -> None:
        decision_node = _make_node(
            "decide",
            NodeType.DECISION,
            {
                "mode": "deterministic",
                "edges": [
                    {"target": "a", "condition": "x > 0"},
                    {"target": "b", "condition": "x <= 0"},
                ],
            },
        )
        node_a = _make_node("a")
        node_b = _make_node("b")
        proc = _make_process(
            nodes=[decision_node, node_a, node_b],
            edges=[
                EdgeDefinition(from_node="a", to_node="b"),
            ],
            entry_point="decide",
        )
        edges = proc.get_outbound_edges("decide")
        assert len(edges) == 2
        assert all(isinstance(e, DecisionEdge) for e in edges)

    def test_decision_outbound_ignores_top_level_edges(self) -> None:
        decision_node = _make_node(
            "decide",
            NodeType.DECISION,
            {
                "mode": "deterministic",
                "edges": [{"target": "a", "condition": "x > 0"}],
            },
        )
        node_a = _make_node("a")
        node_b = _make_node("b")
        proc = _make_process(
            nodes=[decision_node, node_a, node_b],
            edges=[
                EdgeDefinition(from_node="decide", to_node="b"),
            ],
            entry_point="decide",
        )
        edges = proc.get_outbound_edges("decide")
        assert len(edges) == 1
        assert isinstance(edges[0], DecisionEdge)
        assert edges[0].target == "a"

    def test_non_decision_outbound_uses_top_level_edges(self) -> None:
        node_a = _make_node("a")
        node_b = _make_node("b")
        node_c = _make_node("c")
        proc = _make_process(
            nodes=[node_a, node_b, node_c],
            edges=[
                EdgeDefinition(from_node="a", to_node="b"),
                EdgeDefinition(from_node="a", to_node="c"),
            ],
            entry_point="a",
        )
        edges = proc.get_outbound_edges("a")
        assert len(edges) == 2
        assert all(isinstance(e, EdgeDefinition) for e in edges)
