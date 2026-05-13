"""Tests for vote aggregation module (US-002)."""

from __future__ import annotations

from typing import Any

import pytest

from roots.core.aggregation import AggregationError, aggregate_votes
from roots.core.schema import Aggregation, TieBreak, VoteConfig


def make_vote_config(
    vote_key: str = "decision",
    threshold: float = 0.5,
    weights: dict[str, float] | None = None,
    tie_break: TieBreak = TieBreak.FIRST_AGENT,
) -> VoteConfig:
    return VoteConfig(
        vote_key=vote_key,
        threshold=threshold,
        weights=weights,
        tie_break=tie_break,
    )


def outputs(pairs: list[tuple[str, Any]], key: str = "decision") -> list[tuple[str, dict[str, Any]]]:
    return [(name, {key: value}) for name, value in pairs]


# --- All abstain ---


class TestAbstention:
    def test_all_abstain_raises(self) -> None:
        agents: list[tuple[str, dict[str, Any]]] = [
            ("a", {"other_key": "x"}),
            ("b", {"other_key": "y"}),
        ]
        with pytest.raises(AggregationError, match="abstained"):
            aggregate_votes(agents, Aggregation.MAJORITY_VOTE, make_vote_config())

    def test_empty_list_raises(self) -> None:
        with pytest.raises(AggregationError, match="abstained"):
            aggregate_votes([], Aggregation.MAJORITY_VOTE, make_vote_config())

    def test_partial_abstention_excluded_from_denominator(self) -> None:
        # 2 vote "approve", 1 abstains → 2/2 = 100% ≥ 0.5
        agents: list[tuple[str, dict[str, Any]]] = [
            ("a", {"decision": "approve"}),
            ("b", {"decision": "approve"}),
            ("c", {"other_key": "x"}),  # abstain
        ]
        result = aggregate_votes(agents, Aggregation.MAJORITY_VOTE, make_vote_config())
        assert result == "approve"

    def test_partial_abstention_threshold_uses_voters_not_pool(self) -> None:
        # 1 votes "approve" out of 3 agents (1 abstains, 1 votes "reject")
        # denominator = 2 voting agents → 1/2 = 50% which meets threshold 0.5
        agents: list[tuple[str, dict[str, Any]]] = [
            ("a", {"decision": "approve"}),
            ("b", {"decision": "reject"}),
            ("c", {}),  # abstain
        ]
        # With threshold=0.5, neither wins (50% each, tie → FIRST_AGENT picks "approve")
        result = aggregate_votes(agents, Aggregation.MAJORITY_VOTE, make_vote_config(threshold=0.5))
        assert result == "approve"


# --- MAJORITY_VOTE ---


class TestMajorityVote:
    def test_clear_majority_wins(self) -> None:
        result = aggregate_votes(
            outputs([("a", "yes"), ("b", "yes"), ("c", "no")]),
            Aggregation.MAJORITY_VOTE,
            make_vote_config(),
        )
        assert result == "yes"

    def test_threshold_not_met_raises(self) -> None:
        # 1/3 ≈ 33% < 0.5
        with pytest.raises(AggregationError, match="threshold"):
            aggregate_votes(
                outputs([("a", "yes"), ("b", "no"), ("c", "maybe")]),
                Aggregation.MAJORITY_VOTE,
                make_vote_config(threshold=0.5),
            )

    def test_exactly_at_threshold_wins(self) -> None:
        # 2/4 = 50% == threshold 0.5
        result = aggregate_votes(
            outputs([("a", "yes"), ("b", "yes"), ("c", "no"), ("d", "no")]),
            Aggregation.MAJORITY_VOTE,
            make_vote_config(threshold=0.5),
        )
        # Tie — FIRST_AGENT picks "yes" (first occurrence)
        assert result == "yes"

    def test_threshold_zero_any_value_wins(self) -> None:
        result = aggregate_votes(
            outputs([("a", "yes"), ("b", "no"), ("c", "no")]),
            Aggregation.MAJORITY_VOTE,
            make_vote_config(threshold=0.0),
        )
        assert result == "no"  # "no" has 2/3, "yes" has 1/3 — "no" wins

    def test_tie_first_agent_picks_earliest(self) -> None:
        # "approve" appears first in the list
        result = aggregate_votes(
            outputs([("a", "approve"), ("b", "reject")]),
            Aggregation.MAJORITY_VOTE,
            make_vote_config(threshold=0.5, tie_break=TieBreak.FIRST_AGENT),
        )
        assert result == "approve"

    def test_tie_first_agent_respects_config_order(self) -> None:
        # "reject" appears first in the list
        result = aggregate_votes(
            outputs([("a", "reject"), ("b", "approve")]),
            Aggregation.MAJORITY_VOTE,
            make_vote_config(threshold=0.5, tie_break=TieBreak.FIRST_AGENT),
        )
        assert result == "reject"

    def test_tie_reject_raises(self) -> None:
        with pytest.raises(AggregationError, match="tie"):
            aggregate_votes(
                outputs([("a", "yes"), ("b", "no")]),
                Aggregation.MAJORITY_VOTE,
                make_vote_config(threshold=0.5, tie_break=TieBreak.REJECT),
            )

    def test_single_voter_wins(self) -> None:
        result = aggregate_votes(
            outputs([("a", "approve")]),
            Aggregation.MAJORITY_VOTE,
            make_vote_config(threshold=1.0),
        )
        assert result == "approve"

    def test_unanimous_threshold_requires_all_agree(self) -> None:
        with pytest.raises(AggregationError, match="threshold"):
            aggregate_votes(
                outputs([("a", "yes"), ("b", "yes"), ("c", "no")]),
                Aggregation.MAJORITY_VOTE,
                make_vote_config(threshold=1.0),
            )

    def test_non_string_vote_values(self) -> None:
        result = aggregate_votes(
            outputs([("a", 1), ("b", 1), ("c", 0)]),
            Aggregation.MAJORITY_VOTE,
            make_vote_config(),
        )
        assert result == 1


# --- WEIGHTED_VOTE ---


class TestWeightedVote:
    def test_weights_determine_winner(self) -> None:
        # "yes": 10.0, "no": 1.0+1.0=2.0
        result = aggregate_votes(
            outputs([("a", "yes"), ("b", "no"), ("c", "no")]),
            Aggregation.WEIGHTED_VOTE,
            make_vote_config(weights={"a": 10.0, "b": 1.0, "c": 1.0}),
        )
        assert result == "yes"

    def test_equal_weight_majority_wins(self) -> None:
        result = aggregate_votes(
            outputs([("a", "yes"), ("b", "yes"), ("c", "no")]),
            Aggregation.WEIGHTED_VOTE,
            make_vote_config(weights={"a": 1.0, "b": 1.0, "c": 1.0}),
        )
        assert result == "yes"

    def test_weighted_tie_first_agent_wins(self) -> None:
        # "approve": agent a weight 2.0, "reject": agent b weight 2.0
        result = aggregate_votes(
            outputs([("a", "approve"), ("b", "reject")]),
            Aggregation.WEIGHTED_VOTE,
            make_vote_config(
                weights={"a": 2.0, "b": 2.0},
                tie_break=TieBreak.FIRST_AGENT,
            ),
        )
        assert result == "approve"

    def test_weighted_tie_reject_raises(self) -> None:
        with pytest.raises(AggregationError, match="tie"):
            aggregate_votes(
                outputs([("a", "yes"), ("b", "no")]),
                Aggregation.WEIGHTED_VOTE,
                make_vote_config(
                    weights={"a": 1.0, "b": 1.0},
                    tie_break=TieBreak.REJECT,
                ),
            )

    def test_missing_weight_defaults_to_one(self) -> None:
        # agent "c" has no explicit weight → defaults to 1.0
        # "no": 5.0 + 1.0 = 6.0, "yes": 1.0
        result = aggregate_votes(
            outputs([("a", "yes"), ("b", "no"), ("c", "no")]),
            Aggregation.WEIGHTED_VOTE,
            make_vote_config(weights={"b": 5.0}),
        )
        assert result == "no"

    def test_abstaining_agent_with_weight_excluded(self) -> None:
        # agent "b" abstains; "yes" wins with just agent "a"
        agents: list[tuple[str, dict[str, Any]]] = [
            ("a", {"decision": "yes"}),
            ("b", {}),  # abstain
        ]
        result = aggregate_votes(
            agents,
            Aggregation.WEIGHTED_VOTE,
            make_vote_config(weights={"a": 1.0, "b": 10.0}),
        )
        assert result == "yes"


# --- UNANIMOUS ---


class TestUnanimousVote:
    def test_all_agree_returns_value(self) -> None:
        result = aggregate_votes(
            outputs([("a", "approve"), ("b", "approve"), ("c", "approve")]),
            Aggregation.UNANIMOUS,
            make_vote_config(),
        )
        assert result == "approve"

    def test_disagreement_raises(self) -> None:
        with pytest.raises(AggregationError, match="Unanimous vote failed"):
            aggregate_votes(
                outputs([("a", "approve"), ("b", "reject"), ("c", "approve")]),
                Aggregation.UNANIMOUS,
                make_vote_config(),
            )

    def test_single_voter_succeeds(self) -> None:
        result = aggregate_votes(
            outputs([("a", "yes")]),
            Aggregation.UNANIMOUS,
            make_vote_config(),
        )
        assert result == "yes"

    def test_abstentions_excluded_and_remaining_agree(self) -> None:
        agents: list[tuple[str, dict[str, Any]]] = [
            ("a", {"decision": "approve"}),
            ("b", {}),  # abstain
            ("c", {"decision": "approve"}),
        ]
        result = aggregate_votes(agents, Aggregation.UNANIMOUS, make_vote_config())
        assert result == "approve"

    def test_abstentions_excluded_and_remaining_disagree(self) -> None:
        agents: list[tuple[str, dict[str, Any]]] = [
            ("a", {"decision": "approve"}),
            ("b", {}),  # abstain
            ("c", {"decision": "reject"}),
        ]
        with pytest.raises(AggregationError, match="Unanimous vote failed"):
            aggregate_votes(agents, Aggregation.UNANIMOUS, make_vote_config())
