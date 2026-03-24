"""Abstract storage interface for Roots."""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from roots.core.schema import ProcessDefinition


# --- Exceptions ---


class StorageError(Exception):
    """Raised when a storage operation violates a constraint."""


# --- Data Models ---


@dataclass
class RunRecord:
    id: str
    process_id: str
    status: str
    current_node_id: str | None
    work_item_state: dict[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass
class CheckpointRecord:
    id: str
    run_id: str
    node_id: str
    checkpoint_type: str
    prompt: str
    ai_recommendation: dict[str, Any] | None
    status: str
    resolution: dict[str, Any] | None
    created_at: datetime
    resolved_at: datetime | None


@dataclass
class EscalationRecord:
    id: str
    run_id: str
    node_id: str
    trigger_type: str
    reason: str
    work_item_snapshot: dict[str, Any]
    status: str
    resolution: dict[str, Any] | None
    created_at: datetime
    resolved_at: datetime | None


@dataclass
class HistoryEvent:
    id: int
    run_id: str
    event_type: str
    node_id: str | None
    data: dict[str, Any]
    created_at: datetime


@dataclass
class DecisionRecord:
    id: int
    run_id: str
    process_id: str
    node_id: str
    mode: str
    input_state: dict[str, Any]
    decision: dict[str, Any]
    confidence: float
    created_at: datetime


@dataclass
class RetryState:
    run_id: str
    node_id: str
    attempt_count: int
    last_error: str


@dataclass
class WebhookRecord:
    id: str
    url: str
    events: list[str]
    secret: str | None
    created_at: datetime


# --- Abstract Storage Backend ---


class StorageBackend(abc.ABC):
    """Abstract base class for all Roots storage backends."""

    # --- Lifecycle ---

    @abc.abstractmethod
    async def initialize(self) -> None: ...

    @abc.abstractmethod
    async def close(self) -> None: ...

    # --- Process ---

    @abc.abstractmethod
    async def save_process(self, process: ProcessDefinition) -> None: ...

    @abc.abstractmethod
    async def get_process(self, id: str) -> ProcessDefinition | None: ...

    @abc.abstractmethod
    async def list_processes(self) -> list[ProcessDefinition]: ...

    @abc.abstractmethod
    async def delete_process(self, id: str) -> bool: ...

    # --- Agent ---

    @abc.abstractmethod
    async def save_agent(self, registration: dict[str, Any]) -> None: ...

    @abc.abstractmethod
    async def get_agent(self, name: str) -> dict[str, Any] | None: ...

    @abc.abstractmethod
    async def list_agents(self) -> list[dict[str, Any]]: ...

    @abc.abstractmethod
    async def delete_agent(self, name: str) -> bool: ...

    # --- Run ---

    @abc.abstractmethod
    async def create_run(
        self, process_id: str, work_item_state: dict[str, Any]
    ) -> RunRecord: ...

    @abc.abstractmethod
    async def get_run(self, run_id: str) -> RunRecord | None: ...

    @abc.abstractmethod
    async def update_run_status(
        self, run_id: str, status: str, current_node_id: str | None = None
    ) -> None: ...

    @abc.abstractmethod
    async def list_runs(
        self,
        process_id: str | None = None,
        status: str | None = None,
    ) -> list[RunRecord]: ...

    # --- Work Item ---

    @abc.abstractmethod
    async def get_work_item_state(self, run_id: str) -> dict[str, Any]: ...

    @abc.abstractmethod
    async def update_work_item_state(
        self, run_id: str, state: dict[str, Any]
    ) -> None: ...

    # --- History ---

    @abc.abstractmethod
    async def append_history_event(
        self, run_id: str, event_type: str, node_id: str | None, data: dict[str, Any]
    ) -> None: ...

    @abc.abstractmethod
    async def list_history_events(self, run_id: str) -> list[HistoryEvent]: ...

    # --- Checkpoint ---

    @abc.abstractmethod
    async def create_checkpoint(
        self,
        run_id: str,
        node_id: str,
        checkpoint_type: str,
        prompt: str,
        ai_recommendation: dict[str, Any] | None = None,
    ) -> str: ...

    @abc.abstractmethod
    async def get_pending_checkpoint(
        self, run_id: str
    ) -> CheckpointRecord | None: ...

    @abc.abstractmethod
    async def resolve_checkpoint(
        self, checkpoint_id: str, resolution: dict[str, Any]
    ) -> None: ...

    # --- Escalation ---

    @abc.abstractmethod
    async def create_escalation(
        self,
        run_id: str,
        node_id: str,
        trigger_type: str,
        reason: str,
        work_item_snapshot: dict[str, Any],
    ) -> str: ...

    @abc.abstractmethod
    async def get_pending_escalation(
        self, run_id: str
    ) -> EscalationRecord | None: ...

    @abc.abstractmethod
    async def resolve_escalation(
        self, escalation_id: str, resolution: dict[str, Any]
    ) -> None: ...

    # --- Decision ---

    @abc.abstractmethod
    async def append_decision(
        self,
        run_id: str,
        process_id: str,
        node_id: str,
        mode: str,
        input_state: dict[str, Any],
        decision: dict[str, Any],
        confidence: float,
    ) -> None: ...

    @abc.abstractmethod
    async def list_decisions(
        self, process_id: str, node_id: str
    ) -> list[DecisionRecord]: ...

    # --- Retry ---

    @abc.abstractmethod
    async def get_retry_state(
        self, run_id: str, node_id: str
    ) -> RetryState | None: ...

    @abc.abstractmethod
    async def increment_retry(
        self, run_id: str, node_id: str, error: str
    ) -> None: ...

    @abc.abstractmethod
    async def clear_retry(self, run_id: str, node_id: str) -> None: ...

    # --- Webhook ---

    @abc.abstractmethod
    async def create_webhook(
        self,
        url: str,
        events: list[str],
        secret: str | None = None,
    ) -> WebhookRecord: ...

    @abc.abstractmethod
    async def list_webhooks(self) -> list[WebhookRecord]: ...

    @abc.abstractmethod
    async def list_webhooks_by_pattern(
        self, event_type: str
    ) -> list[WebhookRecord]: ...

    @abc.abstractmethod
    async def delete_webhook(self, webhook_id: str) -> bool: ...

    # --- Atomic Run Update ---

    @abc.abstractmethod
    async def update_run_atomically(
        self,
        run_id: str,
        work_item_state: dict[str, Any],
        status: str,
        current_node_id: str | None,
    ) -> None: ...

    # --- Locking ---

    @abc.abstractmethod
    async def acquire_run_lock(
        self,
        run_id: str,
        owner_id: str,
        stale_timeout_seconds: int = 300,
    ) -> bool: ...

    @abc.abstractmethod
    async def release_run_lock(self, run_id: str, owner_id: str) -> None: ...

    @abc.abstractmethod
    async def check_run_lock(
        self, run_id: str
    ) -> tuple[str | None, datetime | None]: ...
