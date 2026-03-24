"""PostgreSQL storage backend for Roots."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

import asyncpg
from pydantic import BaseModel

from roots.core.schema import ProcessDefinition
from roots.core.utils import utcnow
from roots.storage.base import (
    CheckpointRecord,
    DecisionRecord,
    EscalationRecord,
    HistoryEvent,
    RetryState,
    RunRecord,
    StorageBackend,
    StorageError,
    WebhookRecord,
)

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS processes (
    id TEXT PRIMARY KEY,
    name TEXT,
    version TEXT,
    description TEXT,
    definition_json JSONB,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS agents (
    name TEXT PRIMARY KEY,
    type TEXT,
    config_json JSONB,
    created_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    process_id TEXT,
    status TEXT,
    current_node_id TEXT,
    work_item_state_json JSONB,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    locked_by TEXT,
    locked_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS run_history (
    id SERIAL PRIMARY KEY,
    run_id TEXT,
    event_type TEXT,
    node_id TEXT,
    data_json JSONB,
    created_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS checkpoints (
    id TEXT PRIMARY KEY,
    run_id TEXT,
    node_id TEXT,
    checkpoint_type TEXT,
    prompt TEXT,
    ai_recommendation_json JSONB,
    status TEXT DEFAULT 'pending',
    resolution_json JSONB,
    created_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS escalations (
    id TEXT PRIMARY KEY,
    run_id TEXT,
    node_id TEXT,
    trigger_type TEXT,
    reason TEXT,
    work_item_snapshot_json JSONB,
    status TEXT DEFAULT 'pending',
    resolution_json JSONB,
    created_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS decision_history (
    id SERIAL PRIMARY KEY,
    run_id TEXT,
    process_id TEXT,
    node_id TEXT,
    mode TEXT,
    input_state_json JSONB,
    decision_json JSONB,
    confidence DOUBLE PRECISION,
    created_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS retry_state (
    run_id TEXT,
    node_id TEXT,
    attempt_count INTEGER DEFAULT 0,
    last_error TEXT,
    PRIMARY KEY (run_id, node_id)
);

CREATE TABLE IF NOT EXISTS webhooks (
    id TEXT PRIMARY KEY,
    url TEXT,
    events_json JSONB,
    secret TEXT,
    created_at TIMESTAMPTZ
);
"""


def _serialize_process(process: ProcessDefinition) -> str:
    """Serialize a ProcessDefinition to JSON, handling config union type."""
    data = process.model_dump(mode="json")
    for i, node in enumerate(process.nodes):
        if isinstance(node.config, BaseModel):
            data["nodes"][i]["config"] = node.config.model_dump(mode="json")
    return json.dumps(data)


class PostgresBackend(StorageBackend):
    """PostgreSQL-backed storage using asyncpg."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None  # type: ignore[type-arg]

    @property
    def pool(self) -> asyncpg.Pool:  # type: ignore[type-arg]
        if self._pool is None:
            raise RuntimeError("PostgresBackend not initialized; call initialize() first")
        return self._pool

    # --- Lifecycle ---

    async def initialize(self) -> None:
        self._pool = await asyncpg.create_pool(self._dsn)
        async with self.pool.acquire() as conn:
            await conn.execute(_SCHEMA_SQL)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    # --- Process ---

    async def save_process(self, process: ProcessDefinition) -> None:
        now = utcnow()
        definition_json = _serialize_process(process)
        async with self.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO processes
                   (id, name, version, description, definition_json, created_at, updated_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)
                   ON CONFLICT (id) DO UPDATE SET
                   name = $2, version = $3, description = $4,
                   definition_json = $5, updated_at = $7""",
                process.id,
                process.name,
                process.version,
                process.description,
                definition_json,
                now,
                now,
            )

    async def get_process(self, id: str) -> ProcessDefinition | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT definition_json FROM processes WHERE id = $1", id
            )
        if row is None:
            return None
        data = row["definition_json"]
        if isinstance(data, str):
            data = json.loads(data)
        return ProcessDefinition.model_validate(data)

    async def list_processes(self) -> list[ProcessDefinition]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT definition_json FROM processes")
        result = []
        for row in rows:
            data = row["definition_json"]
            if isinstance(data, str):
                data = json.loads(data)
            result.append(ProcessDefinition.model_validate(data))
        return result

    async def delete_process(self, id: str) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM processes WHERE id = $1", id
            )
        return result == "DELETE 1"

    # --- Agent ---

    async def save_agent(self, registration: dict[str, Any]) -> None:
        now = utcnow()
        name = registration["name"]
        agent_type = registration.get("type", "")
        config_json = json.dumps(registration)
        async with self.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO agents (name, type, config_json, created_at)
                   VALUES ($1, $2, $3, $4)
                   ON CONFLICT (name) DO UPDATE SET
                   type = $2, config_json = $3""",
                name,
                agent_type,
                config_json,
                now,
            )

    async def get_agent(self, name: str) -> dict[str, Any] | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT config_json FROM agents WHERE name = $1", name
            )
        if row is None:
            return None
        data = row["config_json"]
        if isinstance(data, str):
            data = json.loads(data)
        return data

    async def list_agents(self) -> list[dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT config_json FROM agents")
        result = []
        for row in rows:
            data = row["config_json"]
            if isinstance(data, str):
                data = json.loads(data)
            result.append(data)
        return result

    async def delete_agent(self, name: str) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM agents WHERE name = $1", name
            )
        return result == "DELETE 1"

    # --- Run ---

    async def create_run(
        self, process_id: str, work_item_state: dict[str, Any]
    ) -> RunRecord:
        run_id = f"run-{uuid4()}"
        now = utcnow()
        async with self.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO runs
                   (id, process_id, status, current_node_id, work_item_state_json,
                    created_at, updated_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                run_id,
                process_id,
                "pending",
                None,
                json.dumps(work_item_state),
                now,
                now,
            )
        return RunRecord(
            id=run_id,
            process_id=process_id,
            status="pending",
            current_node_id=None,
            work_item_state=work_item_state,
            created_at=now,
            updated_at=now,
        )

    async def get_run(self, run_id: str) -> RunRecord | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, process_id, status, current_node_id, work_item_state_json, "
                "created_at, updated_at FROM runs WHERE id = $1",
                run_id,
            )
        if row is None:
            return None
        wis = row["work_item_state_json"]
        if isinstance(wis, str):
            wis = json.loads(wis)
        return RunRecord(
            id=row["id"],
            process_id=row["process_id"],
            status=row["status"],
            current_node_id=row["current_node_id"],
            work_item_state=wis,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def update_run_status(
        self, run_id: str, status: str, current_node_id: str | None = None
    ) -> None:
        now = utcnow()
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE runs SET status = $1, current_node_id = $2, updated_at = $3 "
                "WHERE id = $4",
                status,
                current_node_id,
                now,
                run_id,
            )

    async def list_runs(
        self,
        process_id: str | None = None,
        status: str | None = None,
    ) -> list[RunRecord]:
        query = (
            "SELECT id, process_id, status, current_node_id, "
            "work_item_state_json, created_at, updated_at FROM runs"
        )
        params: list[Any] = []
        clauses: list[str] = []
        if process_id is not None:
            params.append(process_id)
            clauses.append(f"process_id = ${len(params)}")
        if status is not None:
            params.append(status)
            clauses.append(f"status = ${len(params)}")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        result = []
        for r in rows:
            wis = r["work_item_state_json"]
            if isinstance(wis, str):
                wis = json.loads(wis)
            result.append(
                RunRecord(
                    id=r["id"],
                    process_id=r["process_id"],
                    status=r["status"],
                    current_node_id=r["current_node_id"],
                    work_item_state=wis,
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                )
            )
        return result

    # --- Work Item ---

    async def get_work_item_state(self, run_id: str) -> dict[str, Any]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT work_item_state_json FROM runs WHERE id = $1", run_id
            )
        if row is None:
            return {}
        wis = row["work_item_state_json"]
        if isinstance(wis, str):
            wis = json.loads(wis)
        return wis

    async def update_work_item_state(
        self, run_id: str, state: dict[str, Any]
    ) -> None:
        now = utcnow()
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE runs SET work_item_state_json = $1, updated_at = $2 WHERE id = $3",
                json.dumps(state),
                now,
                run_id,
            )

    # --- History ---

    async def append_history_event(
        self, run_id: str, event_type: str, node_id: str | None, data: dict[str, Any]
    ) -> None:
        now = utcnow()
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO run_history (run_id, event_type, node_id, data_json, created_at) "
                "VALUES ($1, $2, $3, $4, $5)",
                run_id,
                event_type,
                node_id,
                json.dumps(data),
                now,
            )

    async def list_history_events(self, run_id: str) -> list[HistoryEvent]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, run_id, event_type, node_id, data_json, created_at "
                "FROM run_history WHERE run_id = $1 ORDER BY id",
                run_id,
            )
        result = []
        for r in rows:
            data = r["data_json"]
            if isinstance(data, str):
                data = json.loads(data)
            result.append(
                HistoryEvent(
                    id=r["id"],
                    run_id=r["run_id"],
                    event_type=r["event_type"],
                    node_id=r["node_id"],
                    data=data,
                    created_at=r["created_at"],
                )
            )
        return result

    # --- Checkpoint ---

    async def create_checkpoint(
        self,
        run_id: str,
        node_id: str,
        checkpoint_type: str,
        prompt: str,
        ai_recommendation: dict[str, Any] | None = None,
    ) -> str:
        existing = await self.get_pending_checkpoint(run_id)
        if existing is not None:
            raise StorageError(
                f"Run {run_id} already has a pending checkpoint"
            )
        cp_id = f"ckpt-{uuid4()}"
        now = utcnow()
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO checkpoints "
                "(id, run_id, node_id, checkpoint_type, prompt, ai_recommendation_json, "
                "status, created_at) VALUES ($1, $2, $3, $4, $5, $6, 'pending', $7)",
                cp_id,
                run_id,
                node_id,
                checkpoint_type,
                prompt,
                json.dumps(ai_recommendation) if ai_recommendation else None,
                now,
            )
        return cp_id

    async def get_pending_checkpoint(
        self, run_id: str
    ) -> CheckpointRecord | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, run_id, node_id, checkpoint_type, prompt, "
                "ai_recommendation_json, status, resolution_json, created_at, resolved_at "
                "FROM checkpoints WHERE run_id = $1 AND status = 'pending' LIMIT 1",
                run_id,
            )
        if row is None:
            return None
        ai_rec = row["ai_recommendation_json"]
        if isinstance(ai_rec, str):
            ai_rec = json.loads(ai_rec)
        resolution = row["resolution_json"]
        if isinstance(resolution, str):
            resolution = json.loads(resolution)
        return CheckpointRecord(
            id=row["id"],
            run_id=row["run_id"],
            node_id=row["node_id"],
            checkpoint_type=row["checkpoint_type"],
            prompt=row["prompt"],
            ai_recommendation=ai_rec,
            status=row["status"],
            resolution=resolution,
            created_at=row["created_at"],
            resolved_at=row["resolved_at"],
        )

    async def resolve_checkpoint(
        self, checkpoint_id: str, resolution: dict[str, Any]
    ) -> None:
        now = utcnow()
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE checkpoints SET status = 'resolved', resolution_json = $1, "
                "resolved_at = $2 WHERE id = $3",
                json.dumps(resolution),
                now,
                checkpoint_id,
            )

    # --- Escalation ---

    async def create_escalation(
        self,
        run_id: str,
        node_id: str,
        trigger_type: str,
        reason: str,
        work_item_snapshot: dict[str, Any],
    ) -> str:
        existing = await self.get_pending_escalation(run_id)
        if existing is not None:
            raise StorageError(
                f"Run {run_id} already has a pending escalation"
            )
        esc_id = f"esc-{uuid4()}"
        now = utcnow()
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO escalations "
                "(id, run_id, node_id, trigger_type, reason, work_item_snapshot_json, "
                "status, created_at) VALUES ($1, $2, $3, $4, $5, $6, 'pending', $7)",
                esc_id,
                run_id,
                node_id,
                trigger_type,
                reason,
                json.dumps(work_item_snapshot),
                now,
            )
        return esc_id

    async def get_pending_escalation(
        self, run_id: str
    ) -> EscalationRecord | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, run_id, node_id, trigger_type, reason, "
                "work_item_snapshot_json, status, resolution_json, created_at, resolved_at "
                "FROM escalations WHERE run_id = $1 AND status = 'pending' LIMIT 1",
                run_id,
            )
        if row is None:
            return None
        snapshot = row["work_item_snapshot_json"]
        if isinstance(snapshot, str):
            snapshot = json.loads(snapshot)
        resolution = row["resolution_json"]
        if isinstance(resolution, str):
            resolution = json.loads(resolution)
        return EscalationRecord(
            id=row["id"],
            run_id=row["run_id"],
            node_id=row["node_id"],
            trigger_type=row["trigger_type"],
            reason=row["reason"],
            work_item_snapshot=snapshot,
            status=row["status"],
            resolution=resolution,
            created_at=row["created_at"],
            resolved_at=row["resolved_at"],
        )

    async def resolve_escalation(
        self, escalation_id: str, resolution: dict[str, Any]
    ) -> None:
        now = utcnow()
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE escalations SET status = 'resolved', resolution_json = $1, "
                "resolved_at = $2 WHERE id = $3",
                json.dumps(resolution),
                now,
                escalation_id,
            )

    # --- Decision ---

    async def append_decision(
        self,
        run_id: str,
        process_id: str,
        node_id: str,
        mode: str,
        input_state: dict[str, Any],
        decision: dict[str, Any],
        confidence: float,
    ) -> None:
        now = utcnow()
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO decision_history "
                "(run_id, process_id, node_id, mode, input_state_json, "
                "decision_json, confidence, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                run_id,
                process_id,
                node_id,
                mode,
                json.dumps(input_state),
                json.dumps(decision),
                confidence,
                now,
            )

    async def list_decisions(
        self, process_id: str, node_id: str
    ) -> list[DecisionRecord]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, run_id, process_id, node_id, mode, input_state_json, "
                "decision_json, confidence, created_at FROM decision_history "
                "WHERE process_id = $1 AND node_id = $2",
                process_id,
                node_id,
            )
        result = []
        for r in rows:
            input_st = r["input_state_json"]
            if isinstance(input_st, str):
                input_st = json.loads(input_st)
            dec = r["decision_json"]
            if isinstance(dec, str):
                dec = json.loads(dec)
            result.append(
                DecisionRecord(
                    id=r["id"],
                    run_id=r["run_id"],
                    process_id=r["process_id"],
                    node_id=r["node_id"],
                    mode=r["mode"],
                    input_state=input_st,
                    decision=dec,
                    confidence=r["confidence"],
                    created_at=r["created_at"],
                )
            )
        return result

    # --- Retry ---

    async def get_retry_state(
        self, run_id: str, node_id: str
    ) -> RetryState | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT run_id, node_id, attempt_count, last_error "
                "FROM retry_state WHERE run_id = $1 AND node_id = $2",
                run_id,
                node_id,
            )
        if row is None:
            return None
        return RetryState(
            run_id=row["run_id"],
            node_id=row["node_id"],
            attempt_count=row["attempt_count"],
            last_error=row["last_error"],
        )

    async def increment_retry(
        self, run_id: str, node_id: str, error: str
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO retry_state (run_id, node_id, attempt_count, last_error) "
                "VALUES ($1, $2, 1, $3) "
                "ON CONFLICT (run_id, node_id) DO UPDATE SET "
                "attempt_count = retry_state.attempt_count + 1, last_error = $3",
                run_id,
                node_id,
                error,
            )

    async def clear_retry(self, run_id: str, node_id: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM retry_state WHERE run_id = $1 AND node_id = $2",
                run_id,
                node_id,
            )

    # --- Webhook ---

    async def create_webhook(
        self,
        url: str,
        events: list[str],
        secret: str | None = None,
    ) -> WebhookRecord:
        wh_id = f"wh-{uuid4()}"
        now = utcnow()
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO webhooks (id, url, events_json, secret, created_at) "
                "VALUES ($1, $2, $3, $4, $5)",
                wh_id,
                url,
                json.dumps(events),
                secret,
                now,
            )
        return WebhookRecord(
            id=wh_id,
            url=url,
            events=events,
            secret=secret,
            created_at=now,
        )

    async def list_webhooks(self) -> list[WebhookRecord]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, url, events_json, secret, created_at FROM webhooks"
            )
        result = []
        for r in rows:
            evts = r["events_json"]
            if isinstance(evts, str):
                evts = json.loads(evts)
            result.append(
                WebhookRecord(
                    id=r["id"],
                    url=r["url"],
                    events=evts,
                    secret=r["secret"],
                    created_at=r["created_at"],
                )
            )
        return result

    async def list_webhooks_by_pattern(
        self, event_type: str
    ) -> list[WebhookRecord]:
        all_hooks = await self.list_webhooks()
        matched: list[WebhookRecord] = []
        for wh in all_hooks:
            for pattern in wh.events:
                if pattern == "*":
                    matched.append(wh)
                    break
                if pattern.endswith(".*"):
                    prefix = pattern[:-1]  # e.g. "roots.run."
                    if event_type.startswith(prefix):
                        matched.append(wh)
                        break
                elif pattern == event_type:
                    matched.append(wh)
                    break
        return matched

    async def delete_webhook(self, webhook_id: str) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM webhooks WHERE id = $1", webhook_id
            )
        return result == "DELETE 1"

    # --- Atomic Run Update ---

    async def update_run_atomically(
        self,
        run_id: str,
        work_item_state: dict[str, Any],
        status: str,
        current_node_id: str | None,
    ) -> None:
        now = utcnow()
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE runs SET work_item_state_json = $1, status = $2, "
                "current_node_id = $3, updated_at = $4 WHERE id = $5",
                json.dumps(work_item_state),
                status,
                current_node_id,
                now,
                run_id,
            )

    # --- Locking ---

    async def acquire_run_lock(
        self,
        run_id: str,
        owner_id: str,
        stale_timeout_seconds: int = 300,
    ) -> bool:
        now = utcnow()
        stale_threshold = now - timedelta(seconds=stale_timeout_seconds)
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE runs SET locked_by = $1, locked_at = $2 "
                "WHERE id = $3 AND (locked_by IS NULL OR locked_at < $4)",
                owner_id,
                now,
                run_id,
                stale_threshold,
            )
        return result == "UPDATE 1"

    async def release_run_lock(self, run_id: str, owner_id: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE runs SET locked_by = NULL, locked_at = NULL "
                "WHERE id = $1 AND locked_by = $2",
                run_id,
                owner_id,
            )

    async def check_run_lock(
        self, run_id: str
    ) -> tuple[str | None, datetime | None]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT locked_by, locked_at FROM runs WHERE id = $1", run_id
            )
        if row is None:
            return (None, None)
        return (row["locked_by"], row["locked_at"])
