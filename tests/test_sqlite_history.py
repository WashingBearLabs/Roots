"""Tests for SQLite storage backend — US-004: History, Checkpoints, Escalations."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from roots.storage.base import StorageError

if TYPE_CHECKING:
    from roots.storage.base import StorageBackend


# --- History Events ---


async def test_append_and_list_history_events(sqlite_storage: StorageBackend) -> None:
    """History events are appended and retrievable in chronological order."""
    run = await sqlite_storage.create_run("proc-1", {})

    await sqlite_storage.append_history_event(
        run.id, "node_entered", "node-1", {"msg": "first"}
    )
    await sqlite_storage.append_history_event(
        run.id, "node_exited", "node-1", {"msg": "second"}
    )
    await sqlite_storage.append_history_event(
        run.id, "node_entered", "node-2", {"msg": "third"}
    )

    events = await sqlite_storage.list_history_events(run.id)
    assert len(events) == 3
    assert events[0].event_type == "node_entered"
    assert events[0].data == {"msg": "first"}
    assert events[1].event_type == "node_exited"
    assert events[2].event_type == "node_entered"
    assert events[2].node_id == "node-2"
    # Chronological order
    assert events[0].created_at <= events[1].created_at <= events[2].created_at


async def test_list_history_events_empty(sqlite_storage: StorageBackend) -> None:
    """Listing history for a run with no events returns empty list."""
    run = await sqlite_storage.create_run("proc-1", {})
    events = await sqlite_storage.list_history_events(run.id)
    assert events == []


async def test_history_events_isolated_by_run(sqlite_storage: StorageBackend) -> None:
    """History events are scoped to their run_id."""
    r1 = await sqlite_storage.create_run("proc-1", {})
    r2 = await sqlite_storage.create_run("proc-1", {})

    await sqlite_storage.append_history_event(r1.id, "ev1", "n1", {})
    await sqlite_storage.append_history_event(r2.id, "ev2", "n2", {})

    events_r1 = await sqlite_storage.list_history_events(r1.id)
    events_r2 = await sqlite_storage.list_history_events(r2.id)
    assert len(events_r1) == 1
    assert len(events_r2) == 1
    assert events_r1[0].event_type == "ev1"
    assert events_r2[0].event_type == "ev2"


# --- Checkpoint Lifecycle ---


async def test_checkpoint_create_pending(sqlite_storage: StorageBackend) -> None:
    """Creating a checkpoint returns an ID with ckpt- prefix and status pending."""
    run = await sqlite_storage.create_run("proc-1", {})
    cp_id = await sqlite_storage.create_checkpoint(
        run.id, "node-1", "approval", "Please approve"
    )
    assert cp_id.startswith("ckpt-")

    pending = await sqlite_storage.get_pending_checkpoint(run.id)
    assert pending is not None
    assert pending.id == cp_id
    assert pending.status == "pending"
    assert pending.node_id == "node-1"
    assert pending.checkpoint_type == "approval"
    assert pending.prompt == "Please approve"
    assert pending.resolution is None
    assert pending.resolved_at is None


async def test_checkpoint_resolve(sqlite_storage: StorageBackend) -> None:
    """Resolving a checkpoint stores the decision dict and timestamp."""
    run = await sqlite_storage.create_run("proc-1", {})
    cp_id = await sqlite_storage.create_checkpoint(
        run.id, "node-1", "approval", "Approve?"
    )

    resolution = {"approved": True, "comment": "Looks good"}
    await sqlite_storage.resolve_checkpoint(cp_id, resolution)

    # No longer pending
    pending = await sqlite_storage.get_pending_checkpoint(run.id)
    assert pending is None


async def test_checkpoint_full_lifecycle(sqlite_storage: StorageBackend) -> None:
    """Checkpoints: create (pending) -> get_pending -> resolve lifecycle works."""
    run = await sqlite_storage.create_run("proc-1", {})

    # Create
    cp_id = await sqlite_storage.create_checkpoint(
        run.id, "node-1", "review", "Review this", {"recommendation": "approve"}
    )

    # Get pending
    pending = await sqlite_storage.get_pending_checkpoint(run.id)
    assert pending is not None
    assert pending.id == cp_id
    assert pending.ai_recommendation == {"recommendation": "approve"}

    # Resolve
    decision = {"action": "approved", "reviewer": "user-1"}
    await sqlite_storage.resolve_checkpoint(cp_id, decision)

    # No longer pending
    assert await sqlite_storage.get_pending_checkpoint(run.id) is None


async def test_checkpoint_duplicate_pending_raises(
    sqlite_storage: StorageBackend,
) -> None:
    """Creating a second pending checkpoint raises StorageError."""
    run = await sqlite_storage.create_run("proc-1", {})
    await sqlite_storage.create_checkpoint(run.id, "node-1", "approval", "First")

    with pytest.raises(StorageError, match="already has a pending checkpoint"):
        await sqlite_storage.create_checkpoint(run.id, "node-2", "approval", "Second")


async def test_checkpoint_can_create_after_resolve(
    sqlite_storage: StorageBackend,
) -> None:
    """After resolving a checkpoint, a new one can be created for the same run."""
    run = await sqlite_storage.create_run("proc-1", {})
    cp_id = await sqlite_storage.create_checkpoint(run.id, "node-1", "approval", "First")
    await sqlite_storage.resolve_checkpoint(cp_id, {"ok": True})

    # Should not raise
    cp_id2 = await sqlite_storage.create_checkpoint(run.id, "node-2", "approval", "Second")
    assert cp_id2.startswith("ckpt-")
    assert cp_id != cp_id2


# --- Escalation Lifecycle ---


async def test_escalation_create_pending(sqlite_storage: StorageBackend) -> None:
    """Creating an escalation returns an ID with esc- prefix and status pending."""
    run = await sqlite_storage.create_run("proc-1", {})
    esc_id = await sqlite_storage.create_escalation(
        run.id, "node-1", "threshold", "Confidence too low", {"data": "snapshot"}
    )
    assert esc_id.startswith("esc-")

    pending = await sqlite_storage.get_pending_escalation(run.id)
    assert pending is not None
    assert pending.id == esc_id
    assert pending.status == "pending"
    assert pending.node_id == "node-1"
    assert pending.trigger_type == "threshold"
    assert pending.reason == "Confidence too low"
    assert pending.work_item_snapshot == {"data": "snapshot"}
    assert pending.resolution is None
    assert pending.resolved_at is None


async def test_escalation_resolve(sqlite_storage: StorageBackend) -> None:
    """Resolving an escalation stores the decision dict and timestamp."""
    run = await sqlite_storage.create_run("proc-1", {})
    esc_id = await sqlite_storage.create_escalation(
        run.id, "node-1", "threshold", "Too low", {"snap": 1}
    )

    resolution = {"override": True, "by": "admin"}
    await sqlite_storage.resolve_escalation(esc_id, resolution)

    # No longer pending
    pending = await sqlite_storage.get_pending_escalation(run.id)
    assert pending is None


async def test_escalation_full_lifecycle(sqlite_storage: StorageBackend) -> None:
    """Escalations: create (pending) -> get_pending -> resolve lifecycle works."""
    run = await sqlite_storage.create_run("proc-1", {})

    # Create
    esc_id = await sqlite_storage.create_escalation(
        run.id, "node-1", "manual", "Needs review", {"state": "current"}
    )

    # Get pending
    pending = await sqlite_storage.get_pending_escalation(run.id)
    assert pending is not None
    assert pending.id == esc_id

    # Resolve
    decision = {"action": "proceed", "reviewer": "admin"}
    await sqlite_storage.resolve_escalation(esc_id, decision)

    # No longer pending
    assert await sqlite_storage.get_pending_escalation(run.id) is None


async def test_escalation_duplicate_pending_raises(
    sqlite_storage: StorageBackend,
) -> None:
    """Creating a second pending escalation raises StorageError."""
    run = await sqlite_storage.create_run("proc-1", {})
    await sqlite_storage.create_escalation(
        run.id, "node-1", "threshold", "First", {"snap": 1}
    )

    with pytest.raises(StorageError, match="already has a pending escalation"):
        await sqlite_storage.create_escalation(
            run.id, "node-2", "threshold", "Second", {"snap": 2}
        )


async def test_escalation_can_create_after_resolve(
    sqlite_storage: StorageBackend,
) -> None:
    """After resolving an escalation, a new one can be created for the same run."""
    run = await sqlite_storage.create_run("proc-1", {})
    esc_id = await sqlite_storage.create_escalation(
        run.id, "node-1", "threshold", "First", {"snap": 1}
    )
    await sqlite_storage.resolve_escalation(esc_id, {"ok": True})

    # Should not raise
    esc_id2 = await sqlite_storage.create_escalation(
        run.id, "node-2", "threshold", "Second", {"snap": 2}
    )
    assert esc_id2.startswith("esc-")
    assert esc_id != esc_id2


# --- Resolution stores decision dict and timestamp ---


async def test_checkpoint_resolution_stores_dict_and_timestamp(
    sqlite_storage: StorageBackend,
) -> None:
    """Resolution stores decision dict and timestamp for checkpoints."""
    run = await sqlite_storage.create_run("proc-1", {})
    cp_id = await sqlite_storage.create_checkpoint(
        run.id, "node-1", "approval", "Approve?"
    )
    decision = {"approved": True, "note": "all clear"}
    await sqlite_storage.resolve_checkpoint(cp_id, decision)

    # Read directly from DB to verify resolution was stored
    cursor = await sqlite_storage.db.execute(
        "SELECT resolution_json, resolved_at FROM checkpoints WHERE id = ?",
        (cp_id,),
    )
    row = await cursor.fetchone()
    assert row is not None
    import json
    stored_resolution = json.loads(row[0])
    assert stored_resolution == decision
    assert row[1] is not None  # resolved_at timestamp exists


async def test_escalation_resolution_stores_dict_and_timestamp(
    sqlite_storage: StorageBackend,
) -> None:
    """Resolution stores decision dict and timestamp for escalations."""
    run = await sqlite_storage.create_run("proc-1", {})
    esc_id = await sqlite_storage.create_escalation(
        run.id, "node-1", "threshold", "Reason", {"snap": 1}
    )
    decision = {"override": True, "by": "admin"}
    await sqlite_storage.resolve_escalation(esc_id, decision)

    # Read directly from DB to verify resolution was stored
    cursor = await sqlite_storage.db.execute(
        "SELECT resolution_json, resolved_at FROM escalations WHERE id = ?",
        (esc_id,),
    )
    row = await cursor.fetchone()
    assert row is not None
    import json
    stored_resolution = json.loads(row[0])
    assert stored_resolution == decision
    assert row[1] is not None  # resolved_at timestamp exists
