"""SQLite storage backend for Roots."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

import aiosqlite
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
    definition_json TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS agents (
    name TEXT PRIMARY KEY,
    type TEXT,
    config_json TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    process_id TEXT,
    status TEXT,
    current_node_id TEXT,
    work_item_state_json TEXT,
    created_at TEXT,
    updated_at TEXT,
    locked_by TEXT,
    locked_at TEXT
);

CREATE TABLE IF NOT EXISTS run_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    event_type TEXT,
    node_id TEXT,
    data_json TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS checkpoints (
    id TEXT PRIMARY KEY,
    run_id TEXT,
    node_id TEXT,
    checkpoint_type TEXT,
    prompt TEXT,
    ai_recommendation_json TEXT,
    status TEXT DEFAULT 'pending',
    resolution_json TEXT,
    created_at TEXT,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS escalations (
    id TEXT PRIMARY KEY,
    run_id TEXT,
    node_id TEXT,
    trigger_type TEXT,
    reason TEXT,
    work_item_snapshot_json TEXT,
    status TEXT DEFAULT 'pending',
    resolution_json TEXT,
    created_at TEXT,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS decision_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    process_id TEXT,
    node_id TEXT,
    mode TEXT,
    input_state_json TEXT,
    decision_json TEXT,
    confidence REAL,
    created_at TEXT
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
    events_json TEXT,
    secret TEXT,
    created_at TEXT
);
"""


def _serialize_process(process: ProcessDefinition) -> str:
    """Serialize a ProcessDefinition to JSON, handling config union type."""
    data = process.model_dump(mode="json")
    for i, node in enumerate(process.nodes):
        if isinstance(node.config, BaseModel):
            data["nodes"][i]["config"] = node.config.model_dump(mode="json")
    return json.dumps(data)


class SqliteBackend(StorageBackend):
    """SQLite-backed storage using aiosqlite."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SqliteBackend not initialized; call initialize() first")
        return self._db

    # --- Lifecycle ---

    async def initialize(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        await self._db.executescript(_SCHEMA_SQL)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    # --- Process ---

    async def save_process(self, process: ProcessDefinition) -> None:
        now = utcnow().isoformat()
        definition_json = _serialize_process(process)
        await self.db.execute(
            """INSERT OR REPLACE INTO processes
               (id, name, version, description, definition_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                process.id,
                process.name,
                process.version,
                process.description,
                definition_json,
                now,
                now,
            ),
        )
        await self.db.commit()

    async def get_process(self, id: str) -> ProcessDefinition | None:
        cursor = await self.db.execute(
            "SELECT definition_json FROM processes WHERE id = ?", (id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return ProcessDefinition.model_validate(json.loads(row[0]))

    async def list_processes(self) -> list[ProcessDefinition]:
        cursor = await self.db.execute("SELECT definition_json FROM processes")
        rows = await cursor.fetchall()
        return [
            ProcessDefinition.model_validate(json.loads(row[0])) for row in rows
        ]

    async def delete_process(self, id: str) -> bool:
        cursor = await self.db.execute(
            "DELETE FROM processes WHERE id = ?", (id,)
        )
        await self.db.commit()
        return cursor.rowcount > 0

    # --- Agent ---

    async def save_agent(self, registration: dict[str, Any]) -> None:
        now = utcnow().isoformat()
        name = registration["name"]
        agent_type = registration.get("type", "")
        config_json = json.dumps(registration)
        await self.db.execute(
            """INSERT OR REPLACE INTO agents
               (name, type, config_json, created_at)
               VALUES (?, ?, ?, ?)""",
            (name, agent_type, config_json, now),
        )
        await self.db.commit()

    async def get_agent(self, name: str) -> dict[str, Any] | None:
        cursor = await self.db.execute(
            "SELECT config_json FROM agents WHERE name = ?", (name,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    async def list_agents(self) -> list[dict[str, Any]]:
        cursor = await self.db.execute("SELECT config_json FROM agents")
        rows = await cursor.fetchall()
        return [json.loads(row[0]) for row in rows]

    async def delete_agent(self, name: str) -> bool:
        cursor = await self.db.execute(
            "DELETE FROM agents WHERE name = ?", (name,)
        )
        await self.db.commit()
        return cursor.rowcount > 0

    # --- Run ---

    async def create_run(
        self, process_id: str, work_item_state: dict[str, Any]
    ) -> RunRecord:
        run_id = f"run-{uuid4()}"
        now = utcnow()
        await self.db.execute(
            """INSERT INTO runs
               (id, process_id, status, current_node_id, work_item_state_json,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                process_id,
                "pending",
                None,
                json.dumps(work_item_state),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        await self.db.commit()
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
        cursor = await self.db.execute(
            "SELECT id, process_id, status, current_node_id, work_item_state_json, "
            "created_at, updated_at FROM runs WHERE id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return RunRecord(
            id=row[0],
            process_id=row[1],
            status=row[2],
            current_node_id=row[3],
            work_item_state=json.loads(row[4]),
            created_at=datetime.fromisoformat(row[5]),
            updated_at=datetime.fromisoformat(row[6]),
        )

    async def update_run_status(
        self, run_id: str, status: str, current_node_id: str | None = None
    ) -> None:
        now = utcnow().isoformat()
        await self.db.execute(
            "UPDATE runs SET status = ?, current_node_id = ?, updated_at = ? "
            "WHERE id = ?",
            (status, current_node_id, now, run_id),
        )
        await self.db.commit()

    async def list_runs(
        self,
        process_id: str | None = None,
        status: str | None = None,
    ) -> list[RunRecord]:
        query = (
            "SELECT id, process_id, status, current_node_id, "
            "work_item_state_json, created_at, updated_at FROM runs"
        )
        params: list[str] = []
        clauses: list[str] = []
        if process_id is not None:
            clauses.append("process_id = ?")
            params.append(process_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        cursor = await self.db.execute(query, params)
        rows = await cursor.fetchall()
        return [
            RunRecord(
                id=r[0],
                process_id=r[1],
                status=r[2],
                current_node_id=r[3],
                work_item_state=json.loads(r[4]),
                created_at=datetime.fromisoformat(r[5]),
                updated_at=datetime.fromisoformat(r[6]),
            )
            for r in rows
        ]

    # --- Work Item ---

    async def get_work_item_state(self, run_id: str) -> dict[str, Any]:
        cursor = await self.db.execute(
            "SELECT work_item_state_json FROM runs WHERE id = ?", (run_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return {}
        return json.loads(row[0])

    async def update_work_item_state(
        self, run_id: str, state: dict[str, Any]
    ) -> None:
        now = utcnow().isoformat()
        await self.db.execute(
            "UPDATE runs SET work_item_state_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(state), now, run_id),
        )
        await self.db.commit()

    # --- History ---

    async def append_history_event(
        self, run_id: str, event_type: str, node_id: str | None, data: dict[str, Any]
    ) -> None:
        now = utcnow().isoformat()
        await self.db.execute(
            "INSERT INTO run_history (run_id, event_type, node_id, data_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, event_type, node_id, json.dumps(data), now),
        )
        await self.db.commit()

    async def list_history_events(self, run_id: str) -> list[HistoryEvent]:
        cursor = await self.db.execute(
            "SELECT id, run_id, event_type, node_id, data_json, created_at "
            "FROM run_history WHERE run_id = ? ORDER BY id",
            (run_id,),
        )
        rows = await cursor.fetchall()
        return [
            HistoryEvent(
                id=r[0],
                run_id=r[1],
                event_type=r[2],
                node_id=r[3],
                data=json.loads(r[4]),
                created_at=datetime.fromisoformat(r[5]),
            )
            for r in rows
        ]

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
        now = utcnow().isoformat()
        await self.db.execute(
            "INSERT INTO checkpoints "
            "(id, run_id, node_id, checkpoint_type, prompt, ai_recommendation_json, "
            "status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
            (
                cp_id,
                run_id,
                node_id,
                checkpoint_type,
                prompt,
                json.dumps(ai_recommendation) if ai_recommendation else None,
                now,
            ),
        )
        await self.db.commit()
        return cp_id

    async def get_pending_checkpoint(
        self, run_id: str
    ) -> CheckpointRecord | None:
        cursor = await self.db.execute(
            "SELECT id, run_id, node_id, checkpoint_type, prompt, "
            "ai_recommendation_json, status, resolution_json, created_at, resolved_at "
            "FROM checkpoints WHERE run_id = ? AND status = 'pending' LIMIT 1",
            (run_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return CheckpointRecord(
            id=row[0],
            run_id=row[1],
            node_id=row[2],
            checkpoint_type=row[3],
            prompt=row[4],
            ai_recommendation=json.loads(row[5]) if row[5] else None,
            status=row[6],
            resolution=json.loads(row[7]) if row[7] else None,
            created_at=datetime.fromisoformat(row[8]),
            resolved_at=datetime.fromisoformat(row[9]) if row[9] else None,
        )

    async def resolve_checkpoint(
        self, checkpoint_id: str, resolution: dict[str, Any]
    ) -> None:
        now = utcnow().isoformat()
        await self.db.execute(
            "UPDATE checkpoints SET status = 'resolved', resolution_json = ?, "
            "resolved_at = ? WHERE id = ?",
            (json.dumps(resolution), now, checkpoint_id),
        )
        await self.db.commit()

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
        now = utcnow().isoformat()
        await self.db.execute(
            "INSERT INTO escalations "
            "(id, run_id, node_id, trigger_type, reason, work_item_snapshot_json, "
            "status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
            (
                esc_id,
                run_id,
                node_id,
                trigger_type,
                reason,
                json.dumps(work_item_snapshot),
                now,
            ),
        )
        await self.db.commit()
        return esc_id

    async def get_pending_escalation(
        self, run_id: str
    ) -> EscalationRecord | None:
        cursor = await self.db.execute(
            "SELECT id, run_id, node_id, trigger_type, reason, "
            "work_item_snapshot_json, status, resolution_json, created_at, resolved_at "
            "FROM escalations WHERE run_id = ? AND status = 'pending' LIMIT 1",
            (run_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return EscalationRecord(
            id=row[0],
            run_id=row[1],
            node_id=row[2],
            trigger_type=row[3],
            reason=row[4],
            work_item_snapshot=json.loads(row[5]),
            status=row[6],
            resolution=json.loads(row[7]) if row[7] else None,
            created_at=datetime.fromisoformat(row[8]),
            resolved_at=datetime.fromisoformat(row[9]) if row[9] else None,
        )

    async def resolve_escalation(
        self, escalation_id: str, resolution: dict[str, Any]
    ) -> None:
        now = utcnow().isoformat()
        await self.db.execute(
            "UPDATE escalations SET status = 'resolved', resolution_json = ?, "
            "resolved_at = ? WHERE id = ?",
            (json.dumps(resolution), now, escalation_id),
        )
        await self.db.commit()

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
        now = utcnow().isoformat()
        await self.db.execute(
            "INSERT INTO decision_history "
            "(run_id, process_id, node_id, mode, input_state_json, "
            "decision_json, confidence, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                process_id,
                node_id,
                mode,
                json.dumps(input_state),
                json.dumps(decision),
                confidence,
                now,
            ),
        )
        await self.db.commit()

    async def list_decisions(
        self, process_id: str, node_id: str
    ) -> list[DecisionRecord]:
        cursor = await self.db.execute(
            "SELECT id, run_id, process_id, node_id, mode, input_state_json, "
            "decision_json, confidence, created_at FROM decision_history "
            "WHERE process_id = ? AND node_id = ?",
            (process_id, node_id),
        )
        rows = await cursor.fetchall()
        return [
            DecisionRecord(
                id=r[0],
                run_id=r[1],
                process_id=r[2],
                node_id=r[3],
                mode=r[4],
                input_state=json.loads(r[5]),
                decision=json.loads(r[6]),
                confidence=r[7],
                created_at=datetime.fromisoformat(r[8]),
            )
            for r in rows
        ]

    # --- Retry ---

    async def get_retry_state(
        self, run_id: str, node_id: str
    ) -> RetryState | None:
        cursor = await self.db.execute(
            "SELECT run_id, node_id, attempt_count, last_error "
            "FROM retry_state WHERE run_id = ? AND node_id = ?",
            (run_id, node_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return RetryState(
            run_id=row[0],
            node_id=row[1],
            attempt_count=row[2],
            last_error=row[3],
        )

    async def increment_retry(
        self, run_id: str, node_id: str, error: str
    ) -> None:
        await self.db.execute(
            "INSERT INTO retry_state (run_id, node_id, attempt_count, last_error) "
            "VALUES (?, ?, 1, ?) "
            "ON CONFLICT(run_id, node_id) DO UPDATE SET "
            "attempt_count = attempt_count + 1, last_error = ?",
            (run_id, node_id, error, error),
        )
        await self.db.commit()

    async def clear_retry(self, run_id: str, node_id: str) -> None:
        await self.db.execute(
            "DELETE FROM retry_state WHERE run_id = ? AND node_id = ?",
            (run_id, node_id),
        )
        await self.db.commit()

    # --- Webhook ---

    async def create_webhook(
        self,
        url: str,
        events: list[str],
        secret: str | None = None,
    ) -> WebhookRecord:
        wh_id = f"wh-{uuid4()}"
        now = utcnow()
        await self.db.execute(
            "INSERT INTO webhooks (id, url, events_json, secret, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (wh_id, url, json.dumps(events), secret, now.isoformat()),
        )
        await self.db.commit()
        return WebhookRecord(
            id=wh_id,
            url=url,
            events=events,
            secret=secret,
            created_at=now,
        )

    async def list_webhooks(self) -> list[WebhookRecord]:
        cursor = await self.db.execute(
            "SELECT id, url, events_json, secret, created_at FROM webhooks"
        )
        rows = await cursor.fetchall()
        return [
            WebhookRecord(
                id=r[0],
                url=r[1],
                events=json.loads(r[2]),
                secret=r[3],
                created_at=datetime.fromisoformat(r[4]),
            )
            for r in rows
        ]

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
        cursor = await self.db.execute(
            "DELETE FROM webhooks WHERE id = ?", (webhook_id,)
        )
        await self.db.commit()
        return cursor.rowcount > 0

    # --- Atomic Run Update ---

    async def update_run_atomically(
        self,
        run_id: str,
        work_item_state: dict[str, Any],
        status: str,
        current_node_id: str | None,
    ) -> None:
        now = utcnow().isoformat()
        await self.db.execute(
            "UPDATE runs SET work_item_state_json = ?, status = ?, "
            "current_node_id = ?, updated_at = ? WHERE id = ?",
            (json.dumps(work_item_state), status, current_node_id, now, run_id),
        )
        await self.db.commit()

    # --- Locking ---

    async def acquire_run_lock(
        self,
        run_id: str,
        owner_id: str,
        stale_timeout_seconds: int = 300,
    ) -> bool:
        now = utcnow()
        stale_threshold = (
            now - timedelta(seconds=stale_timeout_seconds)
        ).isoformat()
        cursor = await self.db.execute(
            "UPDATE runs SET locked_by = ?, locked_at = ? "
            "WHERE id = ? AND (locked_by IS NULL OR locked_at < ?)",
            (owner_id, now.isoformat(), run_id, stale_threshold),
        )
        await self.db.commit()
        return cursor.rowcount == 1

    async def release_run_lock(self, run_id: str, owner_id: str) -> None:
        await self.db.execute(
            "UPDATE runs SET locked_by = NULL, locked_at = NULL "
            "WHERE id = ? AND locked_by = ?",
            (run_id, owner_id),
        )
        await self.db.commit()

    async def check_run_lock(
        self, run_id: str
    ) -> tuple[str | None, datetime | None]:
        cursor = await self.db.execute(
            "SELECT locked_by, locked_at FROM runs WHERE id = ?", (run_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return (None, None)
        locked_by = row[0]
        locked_at = datetime.fromisoformat(row[1]) if row[1] else None
        return (locked_by, locked_at)
