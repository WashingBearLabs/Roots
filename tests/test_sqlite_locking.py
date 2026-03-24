"""Tests for SQLite storage backend — US-007: Run Locking."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from roots.storage.base import StorageBackend


# --- Acquire Lock ---


async def test_acquire_lock_on_unlocked_run(sqlite_storage: StorageBackend) -> None:
    """Acquiring a lock on an unlocked run returns True."""
    run = await sqlite_storage.create_run("proc-1", {"key": "value"})
    result = await sqlite_storage.acquire_run_lock(run.id, "owner-1")
    assert result is True


async def test_acquire_lock_sets_lock_fields(sqlite_storage: StorageBackend) -> None:
    """After acquiring, check_run_lock returns the owner and timestamp."""
    run = await sqlite_storage.create_run("proc-1", {})
    await sqlite_storage.acquire_run_lock(run.id, "owner-1")

    locked_by, locked_at = await sqlite_storage.check_run_lock(run.id)
    assert locked_by == "owner-1"
    assert locked_at is not None


async def test_acquire_lock_on_nonexistent_run(sqlite_storage: StorageBackend) -> None:
    """Acquiring a lock on a run that doesn't exist returns False."""
    result = await sqlite_storage.acquire_run_lock("run-nonexistent", "owner-1")
    assert result is False


# --- Contention ---


async def test_acquire_lock_on_already_locked_run(
    sqlite_storage: StorageBackend,
) -> None:
    """Acquiring a lock on an already-locked run returns False."""
    run = await sqlite_storage.create_run("proc-1", {})
    await sqlite_storage.acquire_run_lock(run.id, "owner-1")

    result = await sqlite_storage.acquire_run_lock(run.id, "owner-2")
    assert result is False


async def test_lock_contention_preserves_original_owner(
    sqlite_storage: StorageBackend,
) -> None:
    """Failed lock attempt does not change the lock owner."""
    run = await sqlite_storage.create_run("proc-1", {})
    await sqlite_storage.acquire_run_lock(run.id, "owner-1")
    await sqlite_storage.acquire_run_lock(run.id, "owner-2")

    locked_by, _ = await sqlite_storage.check_run_lock(run.id)
    assert locked_by == "owner-1"


# --- Stale Lock Reclaim ---


async def test_stale_lock_is_reclaimed(sqlite_storage: StorageBackend) -> None:
    """A lock older than the stale timeout can be reclaimed."""
    run = await sqlite_storage.create_run("proc-1", {})

    # Acquire with a past timestamp by mocking utcnow
    past = datetime.now(timezone.utc) - timedelta(seconds=600)
    with patch("roots.storage.sqlite.utcnow", return_value=past):
        await sqlite_storage.acquire_run_lock(run.id, "owner-1")

    # Now reclaim with default 300s timeout — lock is 600s old
    result = await sqlite_storage.acquire_run_lock(run.id, "owner-2")
    assert result is True

    locked_by, _ = await sqlite_storage.check_run_lock(run.id)
    assert locked_by == "owner-2"


async def test_non_stale_lock_is_not_reclaimed(
    sqlite_storage: StorageBackend,
) -> None:
    """A lock within the stale timeout cannot be reclaimed."""
    run = await sqlite_storage.create_run("proc-1", {})
    await sqlite_storage.acquire_run_lock(run.id, "owner-1")

    # Try to reclaim with a very long timeout — lock is fresh
    result = await sqlite_storage.acquire_run_lock(
        run.id, "owner-2", stale_timeout_seconds=3600
    )
    assert result is False


# --- Release ---


async def test_release_by_owner_clears_lock(sqlite_storage: StorageBackend) -> None:
    """Releasing a lock by the owner clears the lock fields."""
    run = await sqlite_storage.create_run("proc-1", {})
    await sqlite_storage.acquire_run_lock(run.id, "owner-1")

    await sqlite_storage.release_run_lock(run.id, "owner-1")

    locked_by, locked_at = await sqlite_storage.check_run_lock(run.id)
    assert locked_by is None
    assert locked_at is None


async def test_release_by_owner_allows_reacquire(
    sqlite_storage: StorageBackend,
) -> None:
    """After release, another owner can acquire the lock."""
    run = await sqlite_storage.create_run("proc-1", {})
    await sqlite_storage.acquire_run_lock(run.id, "owner-1")
    await sqlite_storage.release_run_lock(run.id, "owner-1")

    result = await sqlite_storage.acquire_run_lock(run.id, "owner-2")
    assert result is True


# --- Non-Owner Release ---


async def test_release_by_non_owner_is_noop(sqlite_storage: StorageBackend) -> None:
    """Releasing a lock by a non-owner does not clear the lock."""
    run = await sqlite_storage.create_run("proc-1", {})
    await sqlite_storage.acquire_run_lock(run.id, "owner-1")

    await sqlite_storage.release_run_lock(run.id, "owner-2")

    locked_by, _ = await sqlite_storage.check_run_lock(run.id)
    assert locked_by == "owner-1"


async def test_release_by_non_owner_does_not_allow_reacquire(
    sqlite_storage: StorageBackend,
) -> None:
    """After a non-owner release attempt, the lock is still held."""
    run = await sqlite_storage.create_run("proc-1", {})
    await sqlite_storage.acquire_run_lock(run.id, "owner-1")
    await sqlite_storage.release_run_lock(run.id, "owner-2")

    result = await sqlite_storage.acquire_run_lock(run.id, "owner-3")
    assert result is False


# --- Check Lock ---


async def test_check_lock_unlocked_run(sqlite_storage: StorageBackend) -> None:
    """check_run_lock on an unlocked run returns (None, None)."""
    run = await sqlite_storage.create_run("proc-1", {})
    locked_by, locked_at = await sqlite_storage.check_run_lock(run.id)
    assert locked_by is None
    assert locked_at is None


async def test_check_lock_nonexistent_run(sqlite_storage: StorageBackend) -> None:
    """check_run_lock on a nonexistent run returns (None, None)."""
    locked_by, locked_at = await sqlite_storage.check_run_lock("run-nonexistent")
    assert locked_by is None
    assert locked_at is None
