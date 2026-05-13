"""Tests for SQLite storage backend — US-005: Decision History and Retry State."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from roots.storage.base import StorageBackend


# --- Decision History ---


async def test_append_and_list_decisions(sqlite_storage: StorageBackend) -> None:
    """Decision records are appended and queryable by process_id + node_id."""
    run = await sqlite_storage.create_run("proc-1", {})

    await sqlite_storage.append_decision(
        run.id, "proc-1", "node-1", "auto", {"key": "val"}, {"choice": "A"}, 0.95
    )
    await sqlite_storage.append_decision(
        run.id, "proc-1", "node-1", "manual", {"key": "val2"}, {"choice": "B"}, 0.80
    )

    decisions = await sqlite_storage.list_decisions("proc-1", "node-1")
    assert len(decisions) == 2
    # Results are ordered most-recent first; "manual" was appended second
    assert decisions[0].mode == "manual"
    assert decisions[0].input_state == {"key": "val2"}
    assert decisions[0].decision == {"choice": "B"}
    assert decisions[0].confidence == 0.80
    assert decisions[0].process_id == "proc-1"
    assert decisions[0].node_id == "node-1"
    assert decisions[0].run_id == run.id
    assert decisions[1].mode == "auto"
    assert decisions[1].confidence == 0.95


async def test_list_decisions_filters_by_process_and_node(
    sqlite_storage: StorageBackend,
) -> None:
    """list_decisions only returns records matching the given process_id and node_id."""
    run = await sqlite_storage.create_run("proc-1", {})

    await sqlite_storage.append_decision(
        run.id, "proc-1", "node-1", "auto", {}, {"a": 1}, 0.9
    )
    await sqlite_storage.append_decision(
        run.id, "proc-1", "node-2", "auto", {}, {"b": 2}, 0.8
    )
    await sqlite_storage.append_decision(
        run.id, "proc-2", "node-1", "auto", {}, {"c": 3}, 0.7
    )

    results = await sqlite_storage.list_decisions("proc-1", "node-1")
    assert len(results) == 1
    assert results[0].decision == {"a": 1}

    results_n2 = await sqlite_storage.list_decisions("proc-1", "node-2")
    assert len(results_n2) == 1
    assert results_n2[0].decision == {"b": 2}

    results_p2 = await sqlite_storage.list_decisions("proc-2", "node-1")
    assert len(results_p2) == 1
    assert results_p2[0].decision == {"c": 3}


async def test_list_decisions_empty(sqlite_storage: StorageBackend) -> None:
    """Listing decisions for a process/node with none returns empty list."""
    decisions = await sqlite_storage.list_decisions("no-proc", "no-node")
    assert decisions == []


async def test_decision_records_have_timestamps(
    sqlite_storage: StorageBackend,
) -> None:
    """Decision records include created_at timestamps."""
    run = await sqlite_storage.create_run("proc-1", {})
    await sqlite_storage.append_decision(
        run.id, "proc-1", "node-1", "auto", {}, {}, 0.5
    )

    decisions = await sqlite_storage.list_decisions("proc-1", "node-1")
    assert len(decisions) == 1
    assert decisions[0].created_at is not None


async def test_decision_records_across_runs(
    sqlite_storage: StorageBackend,
) -> None:
    """Decisions from different runs are all queryable by process_id + node_id."""
    r1 = await sqlite_storage.create_run("proc-1", {})
    r2 = await sqlite_storage.create_run("proc-1", {})

    await sqlite_storage.append_decision(
        r1.id, "proc-1", "node-1", "auto", {}, {"run": 1}, 0.9
    )
    await sqlite_storage.append_decision(
        r2.id, "proc-1", "node-1", "auto", {}, {"run": 2}, 0.8
    )

    decisions = await sqlite_storage.list_decisions("proc-1", "node-1")
    assert len(decisions) == 2
    run_ids = {d.run_id for d in decisions}
    assert r1.id in run_ids
    assert r2.id in run_ids


# --- Extended filter tests (US-001) ---


async def test_list_decisions_ordered_most_recent_first(
    sqlite_storage: StorageBackend,
) -> None:
    """Results are ordered by created_at DESC."""
    run = await sqlite_storage.create_run("proc-1", {})
    await sqlite_storage.append_decision(
        run.id, "proc-1", "node-1", "auto", {}, {"seq": 1}, 0.9
    )
    await sqlite_storage.append_decision(
        run.id, "proc-1", "node-1", "auto", {}, {"seq": 2}, 0.8
    )
    await sqlite_storage.append_decision(
        run.id, "proc-1", "node-1", "auto", {}, {"seq": 3}, 0.7
    )

    decisions = await sqlite_storage.list_decisions("proc-1", "node-1")
    assert len(decisions) == 3
    # Most recent (seq 3) should be first
    assert decisions[0].decision["seq"] == 3
    assert decisions[-1].decision["seq"] == 1


async def test_list_decisions_filter_by_run_id(
    sqlite_storage: StorageBackend,
) -> None:
    """run_id filter scopes results to a single run."""
    r1 = await sqlite_storage.create_run("proc-1", {})
    r2 = await sqlite_storage.create_run("proc-1", {})

    await sqlite_storage.append_decision(
        r1.id, "proc-1", "node-1", "auto", {}, {"run": 1}, 0.9
    )
    await sqlite_storage.append_decision(
        r2.id, "proc-1", "node-1", "auto", {}, {"run": 2}, 0.8
    )

    results = await sqlite_storage.list_decisions("proc-1", "node-1", run_id=r1.id)
    assert len(results) == 1
    assert results[0].decision == {"run": 1}
    assert results[0].run_id == r1.id


async def test_list_decisions_filter_by_mode(
    sqlite_storage: StorageBackend,
) -> None:
    """mode filter returns only decisions with matching mode."""
    run = await sqlite_storage.create_run("proc-1", {})
    await sqlite_storage.append_decision(
        run.id, "proc-1", "node-1", "ai_bounded", {}, {"m": "ai"}, 0.9
    )
    await sqlite_storage.append_decision(
        run.id, "proc-1", "node-1", "manual", {}, {"m": "manual"}, 0.8
    )
    await sqlite_storage.append_decision(
        run.id, "proc-1", "node-1", "ai_bounded", {}, {"m": "ai2"}, 0.7
    )

    ai_results = await sqlite_storage.list_decisions(
        "proc-1", "node-1", mode="ai_bounded"
    )
    assert len(ai_results) == 2
    modes = {d.mode for d in ai_results}
    assert modes == {"ai_bounded"}

    manual_results = await sqlite_storage.list_decisions(
        "proc-1", "node-1", mode="manual"
    )
    assert len(manual_results) == 1
    assert manual_results[0].decision == {"m": "manual"}


async def test_list_decisions_limit(
    sqlite_storage: StorageBackend,
) -> None:
    """limit restricts the number of results returned."""
    run = await sqlite_storage.create_run("proc-1", {})
    for i in range(5):
        await sqlite_storage.append_decision(
            run.id, "proc-1", "node-1", "auto", {}, {"i": i}, 0.9
        )

    results = await sqlite_storage.list_decisions("proc-1", "node-1", limit=2)
    assert len(results) == 2
    # With ORDER BY DESC the top 2 are the most recent (i=4 and i=3)
    assert results[0].decision["i"] == 4
    assert results[1].decision["i"] == 3


async def test_list_decisions_combined_filters(
    sqlite_storage: StorageBackend,
) -> None:
    """run_id, mode, and limit can be combined."""
    r1 = await sqlite_storage.create_run("proc-1", {})
    r2 = await sqlite_storage.create_run("proc-1", {})

    for i in range(3):
        await sqlite_storage.append_decision(
            r1.id, "proc-1", "node-1", "ai_bounded", {}, {"r1_ai": i}, 0.9
        )
    await sqlite_storage.append_decision(
        r1.id, "proc-1", "node-1", "manual", {}, {"r1_manual": 0}, 0.8
    )
    await sqlite_storage.append_decision(
        r2.id, "proc-1", "node-1", "ai_bounded", {}, {"r2_ai": 0}, 0.7
    )

    results = await sqlite_storage.list_decisions(
        "proc-1", "node-1", run_id=r1.id, mode="ai_bounded", limit=2
    )
    assert len(results) == 2
    for d in results:
        assert d.run_id == r1.id
        assert d.mode == "ai_bounded"


async def test_list_decisions_no_limit_returns_all(
    sqlite_storage: StorageBackend,
) -> None:
    """Default limit=None returns all matching records."""
    run = await sqlite_storage.create_run("proc-1", {})
    for i in range(10):
        await sqlite_storage.append_decision(
            run.id, "proc-1", "node-1", "auto", {}, {"i": i}, 0.9
        )

    results = await sqlite_storage.list_decisions("proc-1", "node-1")
    assert len(results) == 10


# --- Retry State ---


async def test_get_retry_state_returns_none_when_no_state(
    sqlite_storage: StorageBackend,
) -> None:
    """get_retry_state returns None when no state exists."""
    result = await sqlite_storage.get_retry_state("run-x", "node-x")
    assert result is None


async def test_first_increment_creates_row(
    sqlite_storage: StorageBackend,
) -> None:
    """First increment creates row with attempt_count=1."""
    await sqlite_storage.increment_retry("run-1", "node-1", "some error")

    state = await sqlite_storage.get_retry_state("run-1", "node-1")
    assert state is not None
    assert state.attempt_count == 1
    assert state.last_error == "some error"
    assert state.run_id == "run-1"
    assert state.node_id == "node-1"


async def test_subsequent_increments_increase_count(
    sqlite_storage: StorageBackend,
) -> None:
    """Subsequent increments increase attempt_count."""
    await sqlite_storage.increment_retry("run-1", "node-1", "error 1")
    await sqlite_storage.increment_retry("run-1", "node-1", "error 2")
    await sqlite_storage.increment_retry("run-1", "node-1", "error 3")

    state = await sqlite_storage.get_retry_state("run-1", "node-1")
    assert state is not None
    assert state.attempt_count == 3
    assert state.last_error == "error 3"


async def test_clear_retry_removes_row(
    sqlite_storage: StorageBackend,
) -> None:
    """clear removes the row."""
    await sqlite_storage.increment_retry("run-1", "node-1", "err")
    assert await sqlite_storage.get_retry_state("run-1", "node-1") is not None

    await sqlite_storage.clear_retry("run-1", "node-1")
    assert await sqlite_storage.get_retry_state("run-1", "node-1") is None


async def test_clear_retry_noop_when_no_state(
    sqlite_storage: StorageBackend,
) -> None:
    """Clearing retry when no state exists does not raise."""
    await sqlite_storage.clear_retry("run-x", "node-x")  # Should not raise


async def test_retry_state_isolated_by_run_and_node(
    sqlite_storage: StorageBackend,
) -> None:
    """Retry state is scoped to run_id + node_id pair."""
    await sqlite_storage.increment_retry("run-1", "node-1", "err-a")
    await sqlite_storage.increment_retry("run-1", "node-2", "err-b")
    await sqlite_storage.increment_retry("run-2", "node-1", "err-c")

    s1 = await sqlite_storage.get_retry_state("run-1", "node-1")
    s2 = await sqlite_storage.get_retry_state("run-1", "node-2")
    s3 = await sqlite_storage.get_retry_state("run-2", "node-1")

    assert s1 is not None and s1.last_error == "err-a"
    assert s2 is not None and s2.last_error == "err-b"
    assert s3 is not None and s3.last_error == "err-c"


async def test_retry_full_lifecycle(
    sqlite_storage: StorageBackend,
) -> None:
    """Full retry lifecycle: increment -> get -> clear -> get returns None."""
    # Start with no state
    assert await sqlite_storage.get_retry_state("run-1", "node-1") is None

    # First increment
    await sqlite_storage.increment_retry("run-1", "node-1", "first error")
    state = await sqlite_storage.get_retry_state("run-1", "node-1")
    assert state is not None
    assert state.attempt_count == 1

    # Second increment
    await sqlite_storage.increment_retry("run-1", "node-1", "second error")
    state = await sqlite_storage.get_retry_state("run-1", "node-1")
    assert state is not None
    assert state.attempt_count == 2
    assert state.last_error == "second error"

    # Clear
    await sqlite_storage.clear_retry("run-1", "node-1")
    assert await sqlite_storage.get_retry_state("run-1", "node-1") is None

    # Can increment again after clear
    await sqlite_storage.increment_retry("run-1", "node-1", "new error")
    state = await sqlite_storage.get_retry_state("run-1", "node-1")
    assert state is not None
    assert state.attempt_count == 1
    assert state.last_error == "new error"
