"""Vote aggregation for Roots agent pool nodes."""

from __future__ import annotations

from typing import Any

from roots.core.schema import Aggregation, TieBreak, VoteConfig


class AggregationError(Exception):
    """Raised when vote aggregation fails."""


def aggregate_votes(
    agents_outputs: list[tuple[str, dict[str, Any]]],
    strategy: Aggregation,
    vote_config: VoteConfig,
) -> dict[str, Any]:
    """Aggregate votes from agent outputs using the given strategy.

    Agents whose output lacks vote_key are treated as abstentions and excluded
    from the denominator. Raises AggregationError if all agents abstain or if
    the strategy cannot produce a winner.

    Args:
        agents_outputs: List of (agent_name, output_dict) pairs in config order.
        strategy: The aggregation strategy (MAJORITY_VOTE, WEIGHTED_VOTE, UNANIMOUS).
        vote_config: Vote configuration (vote_key, threshold, weights, tie_break).

    Returns:
        Dict with winning_value, vote_counts, strategy, and participating_agents.
    """
    votes: list[tuple[str, Any]] = [
        (name, output[vote_config.vote_key])
        for name, output in agents_outputs
        if vote_config.vote_key in output
    ]

    if not votes:
        raise AggregationError("All agents abstained — no votes cast")

    vote_counts: dict[Any, int] = {}
    for _, value in votes:
        vote_counts[value] = vote_counts.get(value, 0) + 1

    if strategy == Aggregation.MAJORITY_VOTE:
        winning_value = _majority_vote(votes, vote_config)
    elif strategy == Aggregation.WEIGHTED_VOTE:
        winning_value = _weighted_vote(votes, vote_config)
    elif strategy == Aggregation.UNANIMOUS:
        winning_value = _unanimous_vote(votes)
    else:
        raise AggregationError(f"Unsupported aggregation strategy: {strategy!r}")

    return {
        "winning_value": winning_value,
        "vote_counts": vote_counts,
        "strategy": str(strategy),
        "participating_agents": len(votes),
    }


def _majority_vote(votes: list[tuple[str, Any]], vote_config: VoteConfig) -> Any:
    total = len(votes)
    counts: dict[Any, int] = {}
    first_position: dict[Any, int] = {}

    for i, (_, value) in enumerate(votes):
        if value not in counts:
            counts[value] = 0
            first_position[value] = i
        counts[value] += 1

    max_count = max(counts.values())

    if max_count / total < vote_config.threshold:
        raise AggregationError(
            f"No value met the required threshold of {vote_config.threshold} "
            f"(best: {max_count}/{total} = {max_count / total:.2%})"
        )

    tied = [v for v, c in counts.items() if c == max_count]

    if len(tied) == 1:
        return tied[0]

    if vote_config.tie_break == TieBreak.REJECT:
        raise AggregationError(
            f"Majority vote resulted in a tie at {max_count}/{total}: {tied!r}"
        )
    return min(tied, key=lambda v: first_position[v])


def _weighted_vote(votes: list[tuple[str, Any]], vote_config: VoteConfig) -> Any:
    weights = vote_config.weights or {}
    tallies: dict[Any, float] = {}
    first_position: dict[Any, int] = {}

    for i, (agent_name, value) in enumerate(votes):
        weight = weights.get(agent_name, 1.0)
        if value not in tallies:
            tallies[value] = 0.0
            first_position[value] = i
        tallies[value] += weight

    max_tally = max(tallies.values())
    tied = [v for v, t in tallies.items() if t == max_tally]

    if len(tied) == 1:
        return tied[0]

    if vote_config.tie_break == TieBreak.REJECT:
        raise AggregationError(
            f"Weighted vote resulted in a tie with tally {max_tally}: {tied!r}"
        )
    return min(tied, key=lambda v: first_position[v])


def _unanimous_vote(votes: list[tuple[str, Any]]) -> Any:
    values = {value for _, value in votes}
    if len(values) == 1:
        return next(iter(values))
    raise AggregationError(
        f"Unanimous vote failed — agents returned different values: {sorted(str(v) for v in values)!r}"
    )
