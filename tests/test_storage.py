"""Parameterized tests for storage backends — US-009.

Runs the same test suite against both SQLite and PostgreSQL backends
via the ``storage`` fixture (params=["sqlite", "postgres"]).
PostgreSQL tests skip cleanly when ROOTS_POSTGRES_DSN is not set.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from roots.storage.base import StorageError

if TYPE_CHECKING:
    from roots.storage.base import StorageBackend


# ---------------------------------------------------------------------------
# Run Metadata — US-001
# ---------------------------------------------------------------------------


async def test_create_run_with_metadata_roundtrip(storage: StorageBackend) -> None:
    meta = {"env": "staging", "version": 3, "debug": True, "score": 1.5}
    run = await storage.create_run("proc-1", {}, metadata=meta)
    assert run.metadata == meta

    fetched = await storage.get_run(run.id)
    assert fetched is not None
    assert fetched.metadata == meta


async def test_create_run_metadata_none_stores_and_retrieves(storage: StorageBackend) -> None:
    run = await storage.create_run("proc-1", {})
    assert run.metadata is None

    fetched = await storage.get_run(run.id)
    assert fetched is not None
    assert fetched.metadata is None


async def test_list_runs_includes_metadata(storage: StorageBackend) -> None:
    meta = {"tag": "smoke-test"}
    await storage.create_run("proc-1", {}, metadata=meta)
    await storage.create_run("proc-1", {})

    runs = await storage.list_runs(process_id="proc-1")
    assert len(runs) == 2
    with_meta = [r for r in runs if r.metadata is not None]
    without_meta = [r for r in runs if r.metadata is None]
    assert len(with_meta) == 1
    assert with_meta[0].metadata == meta
    assert len(without_meta) == 1


async def test_metadata_migration_is_idempotent(storage: StorageBackend) -> None:
    # Calling initialize() a second time must not raise (idempotent migration).
    await storage.initialize()


async def test_metadata_rejects_nested_dict(storage: StorageBackend) -> None:
    with pytest.raises(ValueError, match="JSON scalar"):
        await storage.create_run("proc-1", {}, metadata={"nested": {"key": "value"}})


async def test_metadata_rejects_list_value(storage: StorageBackend) -> None:
    with pytest.raises(ValueError, match="JSON scalar"):
        await storage.create_run("proc-1", {}, metadata={"tags": ["a", "b"]})


# ---------------------------------------------------------------------------
# Metadata Filter Operators — US-002
# ---------------------------------------------------------------------------


async def test_list_runs_filter_eq_string(storage: StorageBackend) -> None:
    await storage.create_run("proc-1", {}, metadata={"env": "staging"})
    await storage.create_run("proc-1", {}, metadata={"env": "prod"})
    await storage.create_run("proc-1", {})

    runs = await storage.list_runs(metadata_filter={"env": {"$eq": "staging"}})
    assert len(runs) == 1
    assert runs[0].metadata == {"env": "staging"}


async def test_list_runs_filter_eq_numeric(storage: StorageBackend) -> None:
    await storage.create_run("proc-1", {}, metadata={"count": 5})
    await storage.create_run("proc-1", {}, metadata={"count": 10})
    await storage.create_run("proc-1", {})

    runs = await storage.list_runs(metadata_filter={"count": {"$eq": 5}})
    assert len(runs) == 1
    assert runs[0].metadata is not None
    assert runs[0].metadata["count"] == 5


async def test_list_runs_filter_in(storage: StorageBackend) -> None:
    await storage.create_run("proc-1", {}, metadata={"env": "staging"})
    await storage.create_run("proc-1", {}, metadata={"env": "prod"})
    await storage.create_run("proc-1", {}, metadata={"env": "dev"})

    runs = await storage.list_runs(
        metadata_filter={"env": {"$in": ["staging", "prod"]}}
    )
    assert len(runs) == 2
    envs = {r.metadata["env"] for r in runs if r.metadata}
    assert envs == {"staging", "prod"}


async def test_list_runs_filter_exists(storage: StorageBackend) -> None:
    await storage.create_run("proc-1", {}, metadata={"tag": "x"})
    await storage.create_run("proc-1", {}, metadata={"other": "y"})
    await storage.create_run("proc-1", {})

    runs = await storage.list_runs(metadata_filter={"tag": {"$exists": True}})
    assert len(runs) == 1
    assert runs[0].metadata == {"tag": "x"}


async def test_list_runs_filter_shorthand(storage: StorageBackend) -> None:
    await storage.create_run("proc-1", {}, metadata={"epic_id": "abc"})
    await storage.create_run("proc-1", {}, metadata={"epic_id": "def"})

    runs = await storage.list_runs(metadata_filter={"epic_id": "abc"})
    assert len(runs) == 1
    assert runs[0].metadata == {"epic_id": "abc"}


async def test_list_runs_filter_invalid_key_raises(storage: StorageBackend) -> None:
    with pytest.raises(ValueError, match="invalid"):
        await storage.list_runs(metadata_filter={"bad-key": "value"})


async def test_metadata_write_invalid_key_raises(storage: StorageBackend) -> None:
    with pytest.raises(ValueError, match="invalid"):
        await storage.create_run("proc-1", {}, metadata={"bad-key": "value"})


# ---------------------------------------------------------------------------
# History Events
# ---------------------------------------------------------------------------


async def test_append_and_list_history_events(storage: StorageBackend) -> None:
    run = await storage.create_run("proc-1", {})

    await storage.append_history_event(run.id, "node_entered", "node-1", {"msg": "first"})
    await storage.append_history_event(run.id, "node_exited", "node-1", {"msg": "second"})
    await storage.append_history_event(run.id, "node_entered", "node-2", {"msg": "third"})

    events = await storage.list_history_events(run.id)
    assert len(events) == 3
    assert events[0].event_type == "node_entered"
    assert events[0].data == {"msg": "first"}
    assert events[1].event_type == "node_exited"
    assert events[2].node_id == "node-2"
    assert events[0].created_at <= events[1].created_at <= events[2].created_at


async def test_list_history_events_empty(storage: StorageBackend) -> None:
    run = await storage.create_run("proc-1", {})
    events = await storage.list_history_events(run.id)
    assert events == []


async def test_history_events_isolated_by_run(storage: StorageBackend) -> None:
    r1 = await storage.create_run("proc-1", {})
    r2 = await storage.create_run("proc-1", {})

    await storage.append_history_event(r1.id, "ev1", "n1", {})
    await storage.append_history_event(r2.id, "ev2", "n2", {})

    assert len(await storage.list_history_events(r1.id)) == 1
    assert len(await storage.list_history_events(r2.id)) == 1


# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------


async def test_checkpoint_create_pending(storage: StorageBackend) -> None:
    run = await storage.create_run("proc-1", {})
    cp_id = await storage.create_checkpoint(run.id, "node-1", "approval", "Please approve")
    assert cp_id.startswith("ckpt-")

    pending = await storage.get_pending_checkpoint(run.id)
    assert pending is not None
    assert pending.id == cp_id
    assert pending.status == "pending"
    assert pending.node_id == "node-1"
    assert pending.checkpoint_type == "approval"
    assert pending.prompt == "Please approve"
    assert pending.resolution is None
    assert pending.resolved_at is None


async def test_checkpoint_resolve(storage: StorageBackend) -> None:
    run = await storage.create_run("proc-1", {})
    cp_id = await storage.create_checkpoint(run.id, "node-1", "approval", "Approve?")
    await storage.resolve_checkpoint(cp_id, {"approved": True})
    assert await storage.get_pending_checkpoint(run.id) is None


async def test_checkpoint_full_lifecycle(storage: StorageBackend) -> None:
    run = await storage.create_run("proc-1", {})
    cp_id = await storage.create_checkpoint(
        run.id, "node-1", "review", "Review this", {"recommendation": "approve"}
    )

    pending = await storage.get_pending_checkpoint(run.id)
    assert pending is not None
    assert pending.ai_recommendation == {"recommendation": "approve"}

    await storage.resolve_checkpoint(cp_id, {"action": "approved"})
    assert await storage.get_pending_checkpoint(run.id) is None


async def test_checkpoint_duplicate_pending_raises(storage: StorageBackend) -> None:
    run = await storage.create_run("proc-1", {})
    await storage.create_checkpoint(run.id, "node-1", "approval", "First")

    with pytest.raises(StorageError, match="already has a pending checkpoint"):
        await storage.create_checkpoint(run.id, "node-2", "approval", "Second")


async def test_checkpoint_can_create_after_resolve(storage: StorageBackend) -> None:
    run = await storage.create_run("proc-1", {})
    cp_id = await storage.create_checkpoint(run.id, "node-1", "approval", "First")
    await storage.resolve_checkpoint(cp_id, {"ok": True})

    cp_id2 = await storage.create_checkpoint(run.id, "node-2", "approval", "Second")
    assert cp_id2.startswith("ckpt-")
    assert cp_id != cp_id2


# ---------------------------------------------------------------------------
# Escalations
# ---------------------------------------------------------------------------


async def test_escalation_create_pending(storage: StorageBackend) -> None:
    run = await storage.create_run("proc-1", {})
    esc_id = await storage.create_escalation(
        run.id, "node-1", "threshold", "Confidence too low", {"data": "snapshot"}
    )
    assert esc_id.startswith("esc-")

    pending = await storage.get_pending_escalation(run.id)
    assert pending is not None
    assert pending.id == esc_id
    assert pending.status == "pending"
    assert pending.trigger_type == "threshold"
    assert pending.reason == "Confidence too low"
    assert pending.work_item_snapshot == {"data": "snapshot"}
    assert pending.resolution is None


async def test_escalation_resolve(storage: StorageBackend) -> None:
    run = await storage.create_run("proc-1", {})
    esc_id = await storage.create_escalation(
        run.id, "node-1", "threshold", "Too low", {"snap": 1}
    )
    await storage.resolve_escalation(esc_id, {"override": True})
    assert await storage.get_pending_escalation(run.id) is None


async def test_escalation_full_lifecycle(storage: StorageBackend) -> None:
    run = await storage.create_run("proc-1", {})
    esc_id = await storage.create_escalation(
        run.id, "node-1", "manual", "Needs review", {"state": "current"}
    )

    pending = await storage.get_pending_escalation(run.id)
    assert pending is not None
    assert pending.id == esc_id

    await storage.resolve_escalation(esc_id, {"action": "proceed"})
    assert await storage.get_pending_escalation(run.id) is None


async def test_escalation_duplicate_pending_raises(storage: StorageBackend) -> None:
    run = await storage.create_run("proc-1", {})
    await storage.create_escalation(run.id, "node-1", "threshold", "First", {"snap": 1})

    with pytest.raises(StorageError, match="already has a pending escalation"):
        await storage.create_escalation(run.id, "node-2", "threshold", "Second", {"snap": 2})


async def test_escalation_can_create_after_resolve(storage: StorageBackend) -> None:
    run = await storage.create_run("proc-1", {})
    esc_id = await storage.create_escalation(
        run.id, "node-1", "threshold", "First", {"snap": 1}
    )
    await storage.resolve_escalation(esc_id, {"ok": True})

    esc_id2 = await storage.create_escalation(
        run.id, "node-2", "threshold", "Second", {"snap": 2}
    )
    assert esc_id2.startswith("esc-")
    assert esc_id != esc_id2


# ---------------------------------------------------------------------------
# Decision History
# ---------------------------------------------------------------------------


async def test_append_and_list_decisions(storage: StorageBackend) -> None:
    run = await storage.create_run("proc-1", {})

    await storage.append_decision(
        run.id, "proc-1", "node-1", "auto", {"key": "val"}, {"choice": "A"}, 0.95
    )
    await storage.append_decision(
        run.id, "proc-1", "node-1", "manual", {"key": "val2"}, {"choice": "B"}, 0.80
    )

    decisions = await storage.list_decisions("proc-1", "node-1")
    assert len(decisions) == 2
    # Results are ordered most-recent first; "manual" was appended second
    assert decisions[0].mode == "manual"
    assert decisions[0].input_state == {"key": "val2"}
    assert decisions[0].decision == {"choice": "B"}
    assert decisions[0].confidence == 0.80
    assert decisions[1].mode == "auto"


async def test_list_decisions_filters_by_process_and_node(
    storage: StorageBackend,
) -> None:
    run = await storage.create_run("proc-1", {})

    await storage.append_decision(run.id, "proc-1", "node-1", "auto", {}, {"a": 1}, 0.9)
    await storage.append_decision(run.id, "proc-1", "node-2", "auto", {}, {"b": 2}, 0.8)
    await storage.append_decision(run.id, "proc-2", "node-1", "auto", {}, {"c": 3}, 0.7)

    results = await storage.list_decisions("proc-1", "node-1")
    assert len(results) == 1
    assert results[0].decision == {"a": 1}


async def test_list_decisions_empty(storage: StorageBackend) -> None:
    decisions = await storage.list_decisions("no-proc", "no-node")
    assert decisions == []


async def test_decision_records_have_timestamps(storage: StorageBackend) -> None:
    run = await storage.create_run("proc-1", {})
    await storage.append_decision(run.id, "proc-1", "node-1", "auto", {}, {}, 0.5)

    decisions = await storage.list_decisions("proc-1", "node-1")
    assert len(decisions) == 1
    assert decisions[0].created_at is not None


async def test_decision_records_across_runs(storage: StorageBackend) -> None:
    r1 = await storage.create_run("proc-1", {})
    r2 = await storage.create_run("proc-1", {})

    await storage.append_decision(r1.id, "proc-1", "node-1", "auto", {}, {"run": 1}, 0.9)
    await storage.append_decision(r2.id, "proc-1", "node-1", "auto", {}, {"run": 2}, 0.8)

    decisions = await storage.list_decisions("proc-1", "node-1")
    assert len(decisions) == 2
    run_ids = {d.run_id for d in decisions}
    assert r1.id in run_ids
    assert r2.id in run_ids


# ---------------------------------------------------------------------------
# Retry State
# ---------------------------------------------------------------------------


async def test_get_retry_state_returns_none_when_no_state(
    storage: StorageBackend,
) -> None:
    result = await storage.get_retry_state("run-x", "node-x")
    assert result is None


async def test_first_increment_creates_row(storage: StorageBackend) -> None:
    await storage.increment_retry("run-1", "node-1", "some error")

    state = await storage.get_retry_state("run-1", "node-1")
    assert state is not None
    assert state.attempt_count == 1
    assert state.last_error == "some error"


async def test_subsequent_increments_increase_count(storage: StorageBackend) -> None:
    await storage.increment_retry("run-1", "node-1", "error 1")
    await storage.increment_retry("run-1", "node-1", "error 2")
    await storage.increment_retry("run-1", "node-1", "error 3")

    state = await storage.get_retry_state("run-1", "node-1")
    assert state is not None
    assert state.attempt_count == 3
    assert state.last_error == "error 3"


async def test_clear_retry_removes_row(storage: StorageBackend) -> None:
    await storage.increment_retry("run-1", "node-1", "err")
    assert await storage.get_retry_state("run-1", "node-1") is not None

    await storage.clear_retry("run-1", "node-1")
    assert await storage.get_retry_state("run-1", "node-1") is None


async def test_clear_retry_noop_when_no_state(storage: StorageBackend) -> None:
    await storage.clear_retry("run-x", "node-x")  # Should not raise


async def test_retry_state_isolated_by_run_and_node(storage: StorageBackend) -> None:
    await storage.increment_retry("run-1", "node-1", "err-a")
    await storage.increment_retry("run-1", "node-2", "err-b")
    await storage.increment_retry("run-2", "node-1", "err-c")

    s1 = await storage.get_retry_state("run-1", "node-1")
    s2 = await storage.get_retry_state("run-1", "node-2")
    s3 = await storage.get_retry_state("run-2", "node-1")

    assert s1 is not None and s1.last_error == "err-a"
    assert s2 is not None and s2.last_error == "err-b"
    assert s3 is not None and s3.last_error == "err-c"


async def test_retry_full_lifecycle(storage: StorageBackend) -> None:
    assert await storage.get_retry_state("run-1", "node-1") is None

    await storage.increment_retry("run-1", "node-1", "first error")
    state = await storage.get_retry_state("run-1", "node-1")
    assert state is not None and state.attempt_count == 1

    await storage.increment_retry("run-1", "node-1", "second error")
    state = await storage.get_retry_state("run-1", "node-1")
    assert state is not None and state.attempt_count == 2
    assert state.last_error == "second error"

    await storage.clear_retry("run-1", "node-1")
    assert await storage.get_retry_state("run-1", "node-1") is None

    await storage.increment_retry("run-1", "node-1", "new error")
    state = await storage.get_retry_state("run-1", "node-1")
    assert state is not None and state.attempt_count == 1


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


async def test_create_webhook_returns_record(storage: StorageBackend) -> None:
    wh = await storage.create_webhook(
        "https://example.com/hook", ["roots.run.completed"], secret="s3cret"
    )
    assert wh.id.startswith("wh-")
    assert wh.url == "https://example.com/hook"
    assert wh.events == ["roots.run.completed"]
    assert wh.secret == "s3cret"
    assert wh.created_at is not None


async def test_create_webhook_no_secret(storage: StorageBackend) -> None:
    wh = await storage.create_webhook("https://example.com/hook", ["*"])
    assert wh.secret is None


async def test_list_webhooks_returns_all(storage: StorageBackend) -> None:
    await storage.create_webhook("https://a.com", ["roots.run.*"])
    await storage.create_webhook("https://b.com", ["roots.process.created"])

    hooks = await storage.list_webhooks()
    assert len(hooks) == 2
    urls = {h.url for h in hooks}
    assert urls == {"https://a.com", "https://b.com"}


async def test_list_webhooks_empty(storage: StorageBackend) -> None:
    hooks = await storage.list_webhooks()
    assert hooks == []


async def test_delete_webhook_returns_true(storage: StorageBackend) -> None:
    wh = await storage.create_webhook("https://a.com", ["*"])
    result = await storage.delete_webhook(wh.id)
    assert result is True
    assert len(await storage.list_webhooks()) == 0


async def test_delete_webhook_returns_false_not_found(storage: StorageBackend) -> None:
    result = await storage.delete_webhook("wh-nonexistent")
    assert result is False


async def test_exact_match(storage: StorageBackend) -> None:
    await storage.create_webhook("https://exact.com", ["roots.run.completed"])
    matched = await storage.list_webhooks_by_pattern("roots.run.completed")
    assert len(matched) == 1


async def test_exact_match_no_match(storage: StorageBackend) -> None:
    await storage.create_webhook("https://exact.com", ["roots.run.completed"])
    matched = await storage.list_webhooks_by_pattern("roots.run.started")
    assert len(matched) == 0


async def test_wildcard_suffix_matches(storage: StorageBackend) -> None:
    await storage.create_webhook("https://wild.com", ["roots.run.*"])
    matched = await storage.list_webhooks_by_pattern("roots.run.completed")
    assert len(matched) == 1


async def test_universal_wildcard_matches_all(storage: StorageBackend) -> None:
    await storage.create_webhook("https://all.com", ["*"])
    for event in ["roots.run.completed", "roots.process.created", "anything.at.all"]:
        matched = await storage.list_webhooks_by_pattern(event)
        assert len(matched) == 1, f"Expected universal match for {event}"


async def test_multiple_webhooks_multiple_patterns(storage: StorageBackend) -> None:
    await storage.create_webhook("https://exact.com", ["roots.run.completed"])
    await storage.create_webhook("https://wild.com", ["roots.run.*"])
    await storage.create_webhook("https://all.com", ["*"])
    await storage.create_webhook("https://other.com", ["roots.process.created"])

    matched = await storage.list_webhooks_by_pattern("roots.run.completed")
    assert len(matched) == 3
    urls = {m.url for m in matched}
    assert urls == {"https://exact.com", "https://wild.com", "https://all.com"}


async def test_webhook_not_matched_twice(storage: StorageBackend) -> None:
    await storage.create_webhook(
        "https://overlap.com", ["roots.run.*", "roots.run.completed", "*"]
    )
    matched = await storage.list_webhooks_by_pattern("roots.run.completed")
    assert len(matched) == 1


# ---------------------------------------------------------------------------
# Run Locking (common behaviour across backends)
# ---------------------------------------------------------------------------


async def test_acquire_lock_on_unlocked_run(storage: StorageBackend) -> None:
    run = await storage.create_run("proc-1", {"key": "value"})
    result = await storage.acquire_run_lock(run.id, "owner-1")
    assert result is True


async def test_acquire_lock_sets_lock_fields(storage: StorageBackend) -> None:
    run = await storage.create_run("proc-1", {})
    await storage.acquire_run_lock(run.id, "owner-1")

    locked_by, locked_at = await storage.check_run_lock(run.id)
    assert locked_by == "owner-1"
    assert locked_at is not None


async def test_acquire_lock_on_nonexistent_run(storage: StorageBackend) -> None:
    result = await storage.acquire_run_lock("run-nonexistent", "owner-1")
    assert result is False


async def test_acquire_lock_on_already_locked_run(storage: StorageBackend) -> None:
    run = await storage.create_run("proc-1", {})
    await storage.acquire_run_lock(run.id, "owner-1")

    result = await storage.acquire_run_lock(run.id, "owner-2")
    assert result is False


async def test_lock_contention_preserves_original_owner(
    storage: StorageBackend,
) -> None:
    run = await storage.create_run("proc-1", {})
    await storage.acquire_run_lock(run.id, "owner-1")
    await storage.acquire_run_lock(run.id, "owner-2")

    locked_by, _ = await storage.check_run_lock(run.id)
    assert locked_by == "owner-1"


async def test_release_by_owner_clears_lock(storage: StorageBackend) -> None:
    run = await storage.create_run("proc-1", {})
    await storage.acquire_run_lock(run.id, "owner-1")

    await storage.release_run_lock(run.id, "owner-1")

    locked_by, locked_at = await storage.check_run_lock(run.id)
    assert locked_by is None
    assert locked_at is None


async def test_release_by_owner_allows_reacquire(storage: StorageBackend) -> None:
    run = await storage.create_run("proc-1", {})
    await storage.acquire_run_lock(run.id, "owner-1")
    await storage.release_run_lock(run.id, "owner-1")

    result = await storage.acquire_run_lock(run.id, "owner-2")
    assert result is True


async def test_release_by_non_owner_is_noop(storage: StorageBackend) -> None:
    run = await storage.create_run("proc-1", {})
    await storage.acquire_run_lock(run.id, "owner-1")

    await storage.release_run_lock(run.id, "owner-2")

    locked_by, _ = await storage.check_run_lock(run.id)
    assert locked_by == "owner-1"


async def test_check_lock_unlocked_run(storage: StorageBackend) -> None:
    run = await storage.create_run("proc-1", {})
    locked_by, locked_at = await storage.check_run_lock(run.id)
    assert locked_by is None
    assert locked_at is None


async def test_check_lock_nonexistent_run(storage: StorageBackend) -> None:
    locked_by, locked_at = await storage.check_run_lock("run-nonexistent")
    assert locked_by is None
    assert locked_at is None
