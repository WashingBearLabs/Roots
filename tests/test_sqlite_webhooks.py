"""Tests for SQLite storage backend — US-006: Webhook Registry and Pattern Matching."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from roots.storage.base import StorageBackend


# --- Webhook CRUD ---


async def test_create_webhook_returns_record(sqlite_storage: StorageBackend) -> None:
    """create_webhook returns a WebhookRecord with correct fields."""
    wh = await sqlite_storage.create_webhook(
        "https://example.com/hook", ["roots.run.completed"], secret="s3cret"
    )
    assert wh.id.startswith("wh-")
    assert wh.url == "https://example.com/hook"
    assert wh.events == ["roots.run.completed"]
    assert wh.secret == "s3cret"
    assert wh.created_at is not None


async def test_create_webhook_no_secret(sqlite_storage: StorageBackend) -> None:
    """create_webhook works without a secret."""
    wh = await sqlite_storage.create_webhook("https://example.com/hook", ["*"])
    assert wh.secret is None


async def test_list_webhooks_returns_all(sqlite_storage: StorageBackend) -> None:
    """list_webhooks returns all registered webhooks."""
    await sqlite_storage.create_webhook("https://a.com", ["roots.run.*"])
    await sqlite_storage.create_webhook("https://b.com", ["roots.process.created"])

    hooks = await sqlite_storage.list_webhooks()
    assert len(hooks) == 2
    urls = {h.url for h in hooks}
    assert urls == {"https://a.com", "https://b.com"}


async def test_list_webhooks_empty(sqlite_storage: StorageBackend) -> None:
    """list_webhooks returns empty list when none exist."""
    hooks = await sqlite_storage.list_webhooks()
    assert hooks == []


async def test_delete_webhook_returns_true(sqlite_storage: StorageBackend) -> None:
    """delete_webhook returns True when webhook exists."""
    wh = await sqlite_storage.create_webhook("https://a.com", ["*"])
    result = await sqlite_storage.delete_webhook(wh.id)
    assert result is True

    hooks = await sqlite_storage.list_webhooks()
    assert len(hooks) == 0


async def test_delete_webhook_returns_false_not_found(
    sqlite_storage: StorageBackend,
) -> None:
    """delete_webhook returns False when webhook does not exist."""
    result = await sqlite_storage.delete_webhook("wh-nonexistent")
    assert result is False


# --- Exact Pattern Matching ---


async def test_exact_match(sqlite_storage: StorageBackend) -> None:
    """Exact event pattern matches the same event type."""
    await sqlite_storage.create_webhook(
        "https://exact.com", ["roots.run.completed"]
    )

    matched = await sqlite_storage.list_webhooks_by_pattern("roots.run.completed")
    assert len(matched) == 1
    assert matched[0].url == "https://exact.com"


async def test_exact_match_no_match(sqlite_storage: StorageBackend) -> None:
    """Exact event pattern does not match a different event type."""
    await sqlite_storage.create_webhook(
        "https://exact.com", ["roots.run.completed"]
    )

    matched = await sqlite_storage.list_webhooks_by_pattern("roots.run.started")
    assert len(matched) == 0


# --- Wildcard Suffix Matching ---


async def test_wildcard_suffix_matches(sqlite_storage: StorageBackend) -> None:
    """Wildcard suffix 'roots.run.*' matches events starting with 'roots.run.'."""
    await sqlite_storage.create_webhook("https://wild.com", ["roots.run.*"])

    matched = await sqlite_storage.list_webhooks_by_pattern("roots.run.completed")
    assert len(matched) == 1
    assert matched[0].url == "https://wild.com"


async def test_wildcard_suffix_matches_different_events(
    sqlite_storage: StorageBackend,
) -> None:
    """Wildcard suffix matches multiple different sub-events."""
    await sqlite_storage.create_webhook("https://wild.com", ["roots.run.*"])

    for event in ["roots.run.started", "roots.run.failed", "roots.run.completed"]:
        matched = await sqlite_storage.list_webhooks_by_pattern(event)
        assert len(matched) == 1, f"Expected match for {event}"


async def test_wildcard_suffix_no_match(sqlite_storage: StorageBackend) -> None:
    """Wildcard suffix does not match events outside its prefix."""
    await sqlite_storage.create_webhook("https://wild.com", ["roots.run.*"])

    matched = await sqlite_storage.list_webhooks_by_pattern("roots.process.created")
    assert len(matched) == 0


# --- Universal Wildcard ---


async def test_universal_wildcard_matches_all(
    sqlite_storage: StorageBackend,
) -> None:
    """Universal wildcard '*' matches any event type."""
    await sqlite_storage.create_webhook("https://all.com", ["*"])

    for event in ["roots.run.completed", "roots.process.created", "anything.at.all"]:
        matched = await sqlite_storage.list_webhooks_by_pattern(event)
        assert len(matched) == 1, f"Expected universal match for {event}"


# --- Case Sensitivity ---


async def test_pattern_matching_is_case_sensitive(
    sqlite_storage: StorageBackend,
) -> None:
    """Pattern matching is case-sensitive."""
    await sqlite_storage.create_webhook(
        "https://case.com", ["roots.run.completed"]
    )

    matched_lower = await sqlite_storage.list_webhooks_by_pattern("roots.run.completed")
    assert len(matched_lower) == 1

    matched_upper = await sqlite_storage.list_webhooks_by_pattern("Roots.Run.Completed")
    assert len(matched_upper) == 0

    matched_caps = await sqlite_storage.list_webhooks_by_pattern("ROOTS.RUN.COMPLETED")
    assert len(matched_caps) == 0


# --- Combined Scenarios ---


async def test_multiple_webhooks_multiple_patterns(
    sqlite_storage: StorageBackend,
) -> None:
    """Multiple webhooks with different patterns match correctly."""
    await sqlite_storage.create_webhook("https://exact.com", ["roots.run.completed"])
    await sqlite_storage.create_webhook("https://wild.com", ["roots.run.*"])
    await sqlite_storage.create_webhook("https://all.com", ["*"])
    await sqlite_storage.create_webhook("https://other.com", ["roots.process.created"])

    matched = await sqlite_storage.list_webhooks_by_pattern("roots.run.completed")
    assert len(matched) == 3
    urls = {m.url for m in matched}
    assert urls == {"https://exact.com", "https://wild.com", "https://all.com"}


async def test_webhook_with_multiple_event_patterns(
    sqlite_storage: StorageBackend,
) -> None:
    """A webhook subscribed to multiple events matches any of them."""
    await sqlite_storage.create_webhook(
        "https://multi.com",
        ["roots.run.completed", "roots.process.created"],
    )

    matched_run = await sqlite_storage.list_webhooks_by_pattern("roots.run.completed")
    assert len(matched_run) == 1

    matched_proc = await sqlite_storage.list_webhooks_by_pattern("roots.process.created")
    assert len(matched_proc) == 1

    matched_none = await sqlite_storage.list_webhooks_by_pattern("roots.other.event")
    assert len(matched_none) == 0


async def test_webhook_not_matched_twice(sqlite_storage: StorageBackend) -> None:
    """A webhook with overlapping patterns is returned only once."""
    await sqlite_storage.create_webhook(
        "https://overlap.com", ["roots.run.*", "roots.run.completed", "*"]
    )

    matched = await sqlite_storage.list_webhooks_by_pattern("roots.run.completed")
    assert len(matched) == 1
