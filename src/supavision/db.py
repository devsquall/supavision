"""SQLite storage layer for Supavision.

One database file, one table per model. Each table stores the Pydantic model
as a JSON blob in a `data` column, with indexed columns for fast queries.

Synchronous SQLite — the bottleneck is Claude API latency (seconds), not DB I/O.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import (
    Checklist,
    Evaluation,
    Report,
    Resource,
    Run,
    RunStatus,
    RunType,
    Session,
    SystemContext,
    User,
)

logger = logging.getLogger(__name__)

DB_PATH_DEFAULT = ".supavision/supavision.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS resources (
    id TEXT PRIMARY KEY,
    parent_id TEXT,
    resource_type TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_resources_parent ON resources(parent_id);
CREATE INDEX IF NOT EXISTS idx_resources_type ON resources(resource_type);

CREATE TABLE IF NOT EXISTS system_contexts (
    id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contexts_resource ON system_contexts(resource_id, version DESC);

CREATE TABLE IF NOT EXISTS checklists (
    id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_checklists_resource ON checklists(resource_id, version DESC);

CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL,
    run_type TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_resource ON reports(resource_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reports_type ON reports(resource_id, run_type, created_at DESC);

CREATE TABLE IF NOT EXISTS evaluations (
    id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evaluations_report ON evaluations(report_id);
CREATE INDEX IF NOT EXISTS idx_evaluations_resource ON evaluations(resource_id, created_at DESC);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL,
    run_type TEXT NOT NULL,
    status TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_resource ON runs(resource_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);

CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    key_hash TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'admin',
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    last_used_at TEXT
);

-- Legacy tables (kept for cascade-delete compatibility)
CREATE TABLE IF NOT EXISTS work_items (
    id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    severity TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    file_path TEXT NOT NULL DEFAULT '',
    line_number INTEGER DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'scanner',
    priority TEXT NOT NULL DEFAULT 'medium',
    task_category TEXT NOT NULL DEFAULT 'security',
    run_id TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_work_items_resource ON work_items(resource_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_work_items_stage ON work_items(stage);
CREATE INDEX IF NOT EXISTS idx_work_items_severity ON work_items(severity);
CREATE INDEX IF NOT EXISTS idx_work_items_category ON work_items(category);
CREATE INDEX IF NOT EXISTS idx_work_items_file_category ON work_items(file_path, category);
CREATE INDEX IF NOT EXISTS idx_work_items_source ON work_items(source);
CREATE INDEX IF NOT EXISTS idx_work_items_run ON work_items(run_id);

CREATE TABLE IF NOT EXISTS agent_jobs (
    id TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    pid INTEGER DEFAULT 0,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_jobs_status ON agent_jobs(status);
CREATE INDEX IF NOT EXISTS idx_agent_jobs_work_item ON agent_jobs(work_item_id);
CREATE INDEX IF NOT EXISTS idx_agent_jobs_resource ON agent_jobs(resource_id);


CREATE TABLE IF NOT EXISTS work_feedback (
    id TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL,
    feedback_type TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_work_feedback_item ON work_feedback(work_item_id);

CREATE TABLE IF NOT EXISTS transitions (
    id TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL,
    from_stage TEXT NOT NULL,
    to_stage TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transitions_item ON transitions(work_item_id, created_at);

CREATE TABLE IF NOT EXISTS notification_log (
    id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    severity TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'sent',
    error TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notification_log_resource ON notification_log(resource_id, created_at DESC);

CREATE TABLE IF NOT EXISTS metrics (
    id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL,
    report_id TEXT NOT NULL,
    name TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metrics_resource_name ON metrics(resource_id, name, created_at DESC);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'viewer',
    is_active INTEGER NOT NULL DEFAULT 1,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    csrf_token TEXT NOT NULL,
    ip_address TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_activity_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS auth_audit_log (
    id TEXT PRIMARY KEY,
    event TEXT NOT NULL,
    user_id TEXT,
    email TEXT,
    ip_address TEXT,
    detail TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auth_audit_event ON auth_audit_log(event, created_at DESC);
"""
class Store:
    """SQLite storage layer for all Supavision models."""

    def __init__(self, db_path: str | Path = DB_PATH_DEFAULT):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.db_path.parent, 0o700)  # Owner-only access to data directory
        except OSError:
            pass  # May fail on some filesystems (Docker volumes, network mounts)
        self._lock = threading.RLock()  # Reentrant — same thread can acquire multiple times
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._execute("PRAGMA journal_mode=WAL")
        self._execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._migrate()
        try:
            os.chmod(self.db_path, 0o600)  # Owner read/write only
        except OSError:
            pass

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _migrate(self) -> None:
        """Apply incremental schema migrations for existing databases."""
        cols = [r[1] for r in self._conn.execute("PRAGMA table_info(api_keys)").fetchall()]
        # v0.3.1: Add role column to api_keys
        if "role" not in cols:
            self._conn.execute("ALTER TABLE api_keys ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'")
            self._conn.commit()
        # Workstream I: Add last_used_at column to api_keys
        if "last_used_at" not in cols:
            self._conn.execute("ALTER TABLE api_keys ADD COLUMN last_used_at TEXT")
            self._conn.commit()

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Thread-safe execute with lock."""
        with self._lock:
            return self._conn.execute(sql, params)

    def _executescript(self, sql: str) -> None:
        with self._lock:
            self._conn.executescript(sql)

    def _commit(self) -> None:
        with self._lock:
            self._conn.commit()

    # ── Resources ────────────────────────────────────────────────────

    def save_resource(self, resource: Resource) -> None:
        data = json.dumps(resource.model_dump(mode="json"), default=str, ensure_ascii=False)
        self._execute(
            "INSERT OR REPLACE INTO resources (id, parent_id, resource_type, data, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (resource.id, resource.parent_id, resource.resource_type, data, str(resource.created_at)),
        )
        self._commit()

    def get_resource(self, resource_id: str) -> Resource | None:
        row = self._execute(
            "SELECT data FROM resources WHERE id = ?", (resource_id,)
        ).fetchone()
        if row is None:
            return None
        return Resource.model_validate(json.loads(row[0]))

    def list_resources(self, parent_id: str | None = None) -> list[Resource]:
        if parent_id is not None:
            rows = self._execute(
                "SELECT data FROM resources WHERE parent_id = ? ORDER BY created_at",
                (parent_id,),
            ).fetchall()
        else:
            rows = self._execute(
                "SELECT data FROM resources ORDER BY created_at"
            ).fetchall()
        return [Resource.model_validate(json.loads(r[0])) for r in rows]

    def list_resources_paginated(
        self,
        limit: int = 20,
        offset: int = 0,
        resource_type: str | None = None,
    ) -> tuple[list["Resource"], int]:
        """Paginated resource listing with optional type filter (Workstream E2).

        Returns (resources, total_count). Type filtering uses json_extract since
        resource_type is inside the JSON `data` blob.
        """
        if resource_type:
            count_row = self._execute(
                "SELECT COUNT(*) FROM resources WHERE json_extract(data, '$.resource_type') = ?",
                (resource_type,),
            ).fetchone()
            total = count_row[0] if count_row else 0
            rows = self._execute(
                "SELECT data FROM resources "
                "WHERE json_extract(data, '$.resource_type') = ? "
                "ORDER BY created_at LIMIT ? OFFSET ?",
                (resource_type, limit, offset),
            ).fetchall()
        else:
            count_row = self._execute("SELECT COUNT(*) FROM resources").fetchone()
            total = count_row[0] if count_row else 0
            rows = self._execute(
                "SELECT data FROM resources ORDER BY created_at LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        resources = [Resource.model_validate(json.loads(r[0])) for r in rows]
        return resources, total

    def get_related_resources(self, resource_id: str) -> list[Resource]:
        """Get resources related to this one (parent, siblings, children)."""
        resource = self.get_resource(resource_id)
        if not resource:
            return []

        related: list[Resource] = []

        # Children of this resource
        children = self._execute(
            "SELECT data FROM resources WHERE parent_id = ? AND id != ?",
            (resource_id, resource_id),
        ).fetchall()
        related.extend(Resource.model_validate(json.loads(r[0])) for r in children)

        # Siblings (same parent) + parent
        if resource.parent_id:
            parent = self.get_resource(resource.parent_id)
            if parent:
                related.append(parent)
            siblings = self._execute(
                "SELECT data FROM resources WHERE parent_id = ? AND id != ?",
                (resource.parent_id, resource_id),
            ).fetchall()
            related.extend(Resource.model_validate(json.loads(r[0])) for r in siblings)

        return related

    def delete_resource(self, resource_id: str) -> None:
        # Cascade: delete all related data before removing the resource
        # Health data
        for table in ("runs", "reports", "evaluations", "system_contexts", "checklists"):
            self._execute(f"DELETE FROM {table} WHERE resource_id = ?", (resource_id,))
        # Legacy work tables (cascade through work items)
        self.delete_work_items_for_resource(resource_id)
        self._execute("DELETE FROM resources WHERE id = ?", (resource_id,))
        self._commit()

    def get_resource_tree(self, resource_id: str) -> list[Resource]:
        """Walk up the parent chain using a recursive CTE. Returns [root, ..., self]."""
        sql = """
        WITH RECURSIVE ancestors(id, parent_id, data, depth) AS (
            SELECT id, parent_id, data, 0
            FROM resources WHERE id = ?
            UNION ALL
            SELECT r.id, r.parent_id, r.data, a.depth + 1
            FROM resources r
            JOIN ancestors a ON r.id = a.parent_id
            WHERE a.depth < 100
        )
        SELECT data FROM ancestors ORDER BY depth DESC
        """
        with self._lock:
            rows = self._conn.execute(sql, (resource_id,)).fetchall()
        return [Resource.model_validate(json.loads(r[0])) for r in rows]

    # ── System Contexts ──────────────────────────────────────────────

    def save_context(self, ctx: SystemContext) -> None:
        data = json.dumps(ctx.model_dump(mode="json"), default=str, ensure_ascii=False)
        self._execute(
            "INSERT OR REPLACE INTO system_contexts (id, resource_id, version, data, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (ctx.id, ctx.resource_id, ctx.version, data, str(ctx.created_at)),
        )
        self._commit()

    def get_latest_context(self, resource_id: str) -> SystemContext | None:
        row = self._execute(
            "SELECT data FROM system_contexts WHERE resource_id = ? ORDER BY version DESC LIMIT 1",
            (resource_id,),
        ).fetchone()
        if row is None:
            return None
        return SystemContext.model_validate(json.loads(row[0]))

    def get_context_history(self, resource_id: str, limit: int = 5) -> list[SystemContext]:
        rows = self._execute(
            "SELECT data FROM system_contexts WHERE resource_id = ? ORDER BY version DESC LIMIT ?",
            (resource_id, limit),
        ).fetchall()
        return [SystemContext.model_validate(json.loads(r[0])) for r in rows]

    # ── Checklists ───────────────────────────────────────────────────

    def save_checklist(self, checklist: Checklist) -> None:
        data = json.dumps(checklist.model_dump(mode="json"), default=str, ensure_ascii=False)
        self._execute(
            "INSERT OR REPLACE INTO checklists (id, resource_id, version, data, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (checklist.id, checklist.resource_id, checklist.version, data, str(checklist.created_at)),
        )
        self._commit()

    def get_latest_checklist(self, resource_id: str) -> Checklist | None:
        row = self._execute(
            "SELECT data FROM checklists WHERE resource_id = ? ORDER BY version DESC LIMIT 1",
            (resource_id,),
        ).fetchone()
        if row is None:
            return None
        return Checklist.model_validate(json.loads(row[0]))

    # ── Reports ──────────────────────────────────────────────────────

    def save_report(self, report: Report) -> None:
        data = json.dumps(report.model_dump(mode="json"), default=str, ensure_ascii=False)
        self._execute(
            "INSERT OR REPLACE INTO reports (id, resource_id, run_type, data, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (report.id, report.resource_id, str(report.run_type), data, str(report.created_at)),
        )
        self._commit()

    def get_report(self, report_id: str) -> Report | None:
        row = self._execute(
            "SELECT data FROM reports WHERE id = ?", (report_id,)
        ).fetchone()
        if row is None:
            return None
        return Report.model_validate(json.loads(row[0]))

    def get_recent_reports(
        self, resource_id: str, run_type: RunType, limit: int = 3
    ) -> list[Report]:
        rows = self._execute(
            "SELECT data FROM reports WHERE resource_id = ? AND run_type = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (resource_id, str(run_type), limit),
        ).fetchall()
        return [Report.model_validate(json.loads(r[0])) for r in rows]

    # ── Evaluations ──────────────────────────────────────────────────

    def save_evaluation(self, evaluation: Evaluation) -> None:
        data = json.dumps(evaluation.model_dump(mode="json"), default=str, ensure_ascii=False)
        self._execute(
            "INSERT OR REPLACE INTO evaluations (id, report_id, resource_id, data, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (evaluation.id, evaluation.report_id, evaluation.resource_id, data, str(evaluation.created_at)),
        )
        self._commit()

    def get_evaluation(self, evaluation_id: str) -> Evaluation | None:
        row = self._execute(
            "SELECT data FROM evaluations WHERE id = ?", (evaluation_id,)
        ).fetchone()
        if row is None:
            return None
        return Evaluation.model_validate(json.loads(row[0]))

    def get_recent_evaluations(self, resource_id: str, limit: int = 5) -> list[Evaluation]:
        rows = self._execute(
            "SELECT data FROM evaluations WHERE resource_id = ? ORDER BY created_at DESC LIMIT ?",
            (resource_id, limit),
        ).fetchall()
        return [Evaluation.model_validate(json.loads(r[0])) for r in rows]

    def get_latest_evaluations_batch(self) -> dict[str, Evaluation]:
        """Get most recent evaluation per resource in one query. Returns {resource_id: Evaluation}."""
        sql = """
        SELECT data FROM evaluations e1
        WHERE created_at = (
            SELECT MAX(e2.created_at) FROM evaluations e2
            WHERE e2.resource_id = e1.resource_id
        )
        """
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        result: dict[str, Evaluation] = {}
        for row in rows:
            ev = Evaluation.model_validate(json.loads(row[0]))
            result[ev.resource_id] = ev
        return result

    def get_health_grid(self, resource_id: str, days: int = 30) -> dict[str, list[str]]:
        """Get severity values grouped by UTC date for the last N days.

        Returns {date_str: [severity1, severity2, ...]} where date_str is YYYY-MM-DD.
        """
        from datetime import datetime, timedelta, timezone

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self._execute(
            "SELECT data FROM evaluations WHERE resource_id = ? AND created_at >= ? ORDER BY created_at",
            (resource_id, cutoff),
        ).fetchall()

        grid: dict[str, list[str]] = {}
        for row in rows:
            ev = json.loads(row[0])
            day = ev.get("created_at", "")[:10]  # YYYY-MM-DD
            severity = ev.get("severity", "")
            if day and severity:
                grid.setdefault(day, []).append(severity)
        return grid

    # ── Metrics ──────────────────────────────────────────────────────

    def save_metrics(self, resource_id: str, report_id: str, metrics: list[dict]) -> None:
        """Save validated metrics. Each dict has {name, value, unit}."""
        now = datetime.now(timezone.utc).isoformat()
        for m in metrics:
            metric_id = str(__import__("uuid").uuid4())
            self._execute(
                "INSERT INTO metrics (id, resource_id, report_id, name, value, unit, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (metric_id, resource_id, report_id, m["name"], m["value"], m["unit"], now),
            )
        if metrics:
            self._commit()

    def get_latest_metrics(self, resource_id: str) -> dict[str, float]:
        """Get the most recent value for each metric name. Returns {name: value}."""
        rows = self._execute(
            """SELECT name, value FROM metrics
               WHERE resource_id = ? AND created_at = (
                   SELECT MAX(m2.created_at) FROM metrics m2
                   WHERE m2.resource_id = metrics.resource_id AND m2.name = metrics.name
               )""",
            (resource_id,),
        ).fetchall()
        return {row[0]: row[1] for row in rows}

    def get_latest_metrics_batch(self) -> dict[str, dict[str, float]]:
        """Get latest metrics for all resources in one query. Returns {resource_id: {name: value}}."""
        sql = """
        SELECT resource_id, name, value FROM metrics m1
        WHERE created_at = (
            SELECT MAX(m2.created_at) FROM metrics m2
            WHERE m2.resource_id = m1.resource_id AND m2.name = m1.name
        )
        """
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        result: dict[str, dict[str, float]] = {}
        for row in rows:
            result.setdefault(row[0], {})[row[1]] = row[2]
        return result

    def get_metrics_history(
        self, resource_id: str, metric_name: str, days: int = 30
    ) -> list[dict]:
        """Get time-series for a specific metric. Returns [{value, created_at}]."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self._execute(
            "SELECT value, created_at FROM metrics "
            "WHERE resource_id = ? AND name = ? AND created_at >= ? ORDER BY created_at",
            (resource_id, metric_name, cutoff),
        ).fetchall()
        return [{"value": row[0], "created_at": row[1]} for row in rows]

    # ── Runs ─────────────────────────────────────────────────────────

    def save_run(self, run: Run) -> None:
        data = json.dumps(run.model_dump(mode="json"), default=str, ensure_ascii=False)
        self._execute(
            "INSERT OR REPLACE INTO runs (id, resource_id, run_type, status, data, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run.id, run.resource_id, str(run.run_type), str(run.status), data, str(run.created_at)),
        )
        self._commit()

    def get_run(self, run_id: str) -> Run | None:
        row = self._execute(
            "SELECT data FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return Run.model_validate(json.loads(row[0]))

    def get_pending_runs(self) -> list[Run]:
        rows = self._execute(
            "SELECT data FROM runs WHERE status = ? ORDER BY created_at",
            (str(RunStatus.PENDING),),
        ).fetchall()
        return [Run.model_validate(json.loads(r[0])) for r in rows]

    def get_running_runs(self) -> list[Run]:
        rows = self._execute(
            "SELECT data FROM runs WHERE status = ? ORDER BY created_at",
            (str(RunStatus.RUNNING),),
        ).fetchall()
        return [Run.model_validate(json.loads(r[0])) for r in rows]

    def get_recent_runs_global(self, limit: int = 10, since: str | None = None) -> list[Run]:
        """Get most recent runs across all resources, optionally filtered by cutoff time."""
        if since:
            rows = self._execute(
                "SELECT data FROM runs WHERE created_at > ? ORDER BY created_at DESC LIMIT ?",
                (since, limit),
            ).fetchall()
        else:
            rows = self._execute(
                "SELECT data FROM runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [Run.model_validate(json.loads(r[0])) for r in rows]

    def get_runs(self, resource_id: str, limit: int = 10, offset: int = 0) -> list[Run]:
        rows = self._execute(
            "SELECT data FROM runs WHERE resource_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (resource_id, limit, offset),
        ).fetchall()
        return [Run.model_validate(json.loads(r[0])) for r in rows]

    def get_latest_runs_batch(self) -> dict[tuple[str, str], Run]:
        """Get latest run for each (resource_id, run_type) pair in one query.

        Returns {(resource_id, run_type): Run} for the most recent completed run.
        Used by the scheduler to avoid N+1 queries.
        """
        sql = """
        SELECT data FROM runs r1
        WHERE created_at = (
            SELECT MAX(r2.created_at) FROM runs r2
            WHERE r2.resource_id = r1.resource_id
            AND r2.run_type = r1.run_type
            AND r2.status = ?
        )
        """
        with self._lock:
            rows = self._conn.execute(sql, (str(RunStatus.COMPLETED),)).fetchall()
        result: dict[tuple[str, str], Run] = {}
        for row in rows:
            run = Run.model_validate(json.loads(row[0]))
            result[(run.resource_id, str(run.run_type))] = run
        return result

    def get_latest_run(self, resource_id: str, run_type: RunType) -> Run | None:
        row = self._execute(
            "SELECT data FROM runs WHERE resource_id = ? AND run_type = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (resource_id, str(run_type)),
        ).fetchone()
        if row is None:
            return None
        return Run.model_validate(json.loads(row[0]))

    def get_stale_runs(self, hours: int = 4) -> list[Run]:
        """Get runs stuck in RUNNING status older than `hours`."""
        rows = self._execute(
            "SELECT data FROM runs WHERE status = ?",
            (str(RunStatus.RUNNING),),
        ).fetchall()
        stale = []
        for row in rows:
            run = Run.model_validate(json.loads(row[0]))
            if run.started_at:
                age_hours = (
                    datetime.now(timezone.utc) - run.started_at
                ).total_seconds() / 3600
                if age_hours > hours:
                    stale.append(run)
        return stale

    def purge_old_reports(self, days: int = 90) -> int:
        """Delete reports and their evaluations older than `days`. Returns count."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._lock:
            self._conn.execute(
                "DELETE FROM evaluations WHERE report_id IN "
                "(SELECT id FROM reports WHERE created_at < ?)",
                (cutoff,),
            )
            cursor = self._conn.execute(
                "DELETE FROM reports WHERE created_at < ?", (cutoff,)
            )
            count = cursor.rowcount
            self._conn.commit()
        return count

    def purge_old_runs(self, days: int = 90) -> int:
        """Delete completed/failed runs older than `days`. Returns count."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM runs WHERE status IN (?, ?) AND created_at < ?",
                (str(RunStatus.COMPLETED), str(RunStatus.FAILED), cutoff),
            )
            count = cursor.rowcount
            self._conn.commit()
        return count

    # ── API Keys ────────────────────────────────────────────────────

    def save_api_key(self, key_id: str, key_hash: str, label: str = "", role: str = "admin") -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO api_keys (id, key_hash, label, role, created_at) VALUES (?, ?, ?, ?, ?)",
                (key_id, key_hash, label, role, now),
            )
            self._conn.commit()

    def validate_api_key(self, key_hash: str) -> dict | None:
        """Returns key record if valid (exists and not revoked), else None.

        Also updates last_used_at on successful validation (Workstream I).
        """
        row = self._execute(
            "SELECT id, label, role, created_at, revoked_at FROM api_keys WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
        if not row:
            return None
        if row[4]:  # revoked_at is set
            return None
        # Workstream I: track last usage
        try:
            from datetime import datetime, timezone
            self._execute(
                "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), row[0]),
            )
            self._commit()
        except Exception as e:
            logger.debug("last_used_at update skipped (legacy DB?): %s", e)
        return {"id": row[0], "label": row[1], "role": row[2], "created_at": row[3]}

    def list_api_keys(self) -> list[dict]:
        rows = self._execute(
            "SELECT id, label, created_at, revoked_at, last_used_at FROM api_keys ORDER BY created_at DESC"
        ).fetchall()
        return [
            {
                "id": r[0],
                "label": r[1],
                "created_at": r[2],
                "revoked": bool(r[3]),
                "last_used_at": r[4] if len(r) > 4 else None,
            }
            for r in rows
        ]

    def revoke_api_key(self, key_id: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (now, key_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    # ══════════════════════════════════════════════════════════════════
    # Legacy work item methods (kept for cascade-delete compatibility)
    # These methods must NEVER write to the evaluations table.
    # Finding-level judgments are stored as fields on the WorkItem itself.
    # ══════════════════════════════════════════════════════════════════

    def delete_work_items_for_resource(self, resource_id: str) -> None:
        """Cascade-delete legacy work items when a resource is removed."""
        with self._lock:
            item_ids = [r[0] for r in self._conn.execute(
                "SELECT id FROM work_items WHERE resource_id = ?", (resource_id,)
            ).fetchall()]
            for item_id in item_ids:
                self._conn.execute("DELETE FROM work_feedback WHERE work_item_id = ?", (item_id,))
                self._conn.execute("DELETE FROM transitions WHERE work_item_id = ?", (item_id,))
            self._conn.execute("DELETE FROM agent_jobs WHERE resource_id = ?", (resource_id,))
            self._conn.execute("DELETE FROM work_items WHERE resource_id = ?", (resource_id,))
            self._conn.commit()

    # ── Notification Log ────────────────────────────────────

    def log_notification(
        self,
        resource_id: str,
        channel: str,
        severity: str,
        summary: str,
        status: str = "sent",
        error: str = "",
    ) -> None:
        from uuid import uuid4
        notif_id = uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        self._execute(
            """INSERT INTO notification_log
               (id, resource_id, channel, severity, summary, status, error, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (notif_id, resource_id, channel, severity, summary, status, error, now),
        )
        self._commit()

    def list_notifications(self, resource_id: str | None = None, limit: int = 20) -> list[dict]:
        if resource_id:
            rows = self._execute(
                "SELECT id, resource_id, channel, severity, summary, status, error, created_at "
                "FROM notification_log WHERE resource_id = ? ORDER BY created_at DESC LIMIT ?",
                (resource_id, limit),
            ).fetchall()
        else:
            rows = self._execute(
                "SELECT id, resource_id, channel, severity, summary, status, error, created_at "
                "FROM notification_log ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": r[0], "resource_id": r[1], "channel": r[2],
                "severity": r[3], "summary": r[4], "status": r[5],
                "error": r[6], "created_at": r[7],
            }
            for r in rows
        ]

    # ── Users ──────────────────────────────────────────────────────

    def create_user(self, user: User) -> None:
        self._execute(
            "INSERT INTO users (id, email, password_hash, name, role, is_active, data, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user.id, user.email, user.password_hash, user.name, user.role,
                int(user.is_active), json.dumps(user.model_dump(mode="json")),
                user.created_at.isoformat(),
            ),
        )
        self._commit()

    def get_user_by_email(self, email: str) -> User | None:
        row = self._execute("SELECT data FROM users WHERE email = ?", (email,)).fetchone()
        return User.model_validate(json.loads(row[0])) if row else None

    def get_user(self, user_id: str) -> User | None:
        row = self._execute("SELECT data FROM users WHERE id = ?", (user_id,)).fetchone()
        return User.model_validate(json.loads(row[0])) if row else None

    def list_users(self) -> list[User]:
        rows = self._execute("SELECT data FROM users ORDER BY created_at").fetchall()
        return [User.model_validate(json.loads(r[0])) for r in rows]

    def update_user(self, user: User) -> None:
        self._execute(
            "UPDATE users SET email=?, password_hash=?, name=?, role=?, is_active=?, data=?, "
            "last_login_at=? WHERE id=?",
            (
                user.email, user.password_hash, user.name, user.role, int(user.is_active),
                json.dumps(user.model_dump(mode="json")),
                user.last_login_at.isoformat() if user.last_login_at else None,
                user.id,
            ),
        )
        self._commit()

    def deactivate_user(self, user_id: str) -> None:
        user = self.get_user(user_id)
        if user:
            user.is_active = False
            self.update_user(user)
            self.revoke_user_sessions(user_id)

    def count_users(self) -> int:
        row = self._execute("SELECT COUNT(*) FROM users").fetchone()
        return row[0] if row else 0

    # ── Sessions ───────────────────────────────────────────────────

    def create_session(self, session: Session) -> None:
        self._execute(
            "INSERT INTO sessions (id, user_id, csrf_token, ip_address, user_agent, data, "
            "created_at, expires_at, last_activity_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session.id, session.user_id, session.csrf_token, session.ip_address,
                session.user_agent, json.dumps(session.model_dump(mode="json")),
                session.created_at.isoformat(), session.expires_at.isoformat(),
                session.last_activity_at.isoformat(),
            ),
        )
        self._commit()

    def get_session(self, session_id: str) -> Session | None:
        """Get a valid session. Returns None if expired, revoked, or idle > configured timeout."""
        row = self._execute("SELECT data FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            return None
        session = Session.model_validate(json.loads(row[0]))
        now = datetime.now(timezone.utc)
        # Check absolute expiry
        if session.expires_at < now:
            return None
        # Check revocation
        if session.revoked_at:
            return None
        # Check idle timeout (default 2 hours)
        from .config import SESSION_IDLE_MINUTES
        idle_limit = timedelta(minutes=SESSION_IDLE_MINUTES)
        if now - session.last_activity_at > idle_limit:
            return None
        return session

    def touch_session(self, session_id: str) -> None:
        """Update last_activity_at to now (for idle timeout tracking)."""
        now = datetime.now(timezone.utc)
        row = self._execute("SELECT data FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row:
            session = Session.model_validate(json.loads(row[0]))
            if session.expires_at < now or session.revoked_at:
                return  # Don't revive expired or revoked sessions
            session.last_activity_at = now
            self._execute(
                "UPDATE sessions SET data=?, last_activity_at=? WHERE id=?",
                (json.dumps(session.model_dump(mode="json")), now.isoformat(), session_id),
            )
            self._commit()

    def revoke_session(self, session_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        row = self._execute("SELECT data FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row:
            session = Session.model_validate(json.loads(row[0]))
            session.revoked_at = datetime.now(timezone.utc)
            self._execute(
                "UPDATE sessions SET data=?, revoked_at=? WHERE id=?",
                (json.dumps(session.model_dump(mode="json")), now, session_id),
            )
            self._commit()

    def revoke_user_sessions(self, user_id: str) -> None:
        """Revoke ALL sessions for a user (called on password change, role change, deactivation)."""
        now = datetime.now(timezone.utc).isoformat()
        rows = self._execute(
            "SELECT id, data FROM sessions WHERE user_id = ? AND revoked_at IS NULL",
            (user_id,),
        ).fetchall()
        for row in rows:
            session = Session.model_validate(json.loads(row[1]))
            session.revoked_at = datetime.now(timezone.utc)
            self._execute(
                "UPDATE sessions SET data=?, revoked_at=? WHERE id=?",
                (json.dumps(session.model_dump(mode="json")), now, row[0]),
            )
        if rows:
            self._commit()

    def cleanup_expired_sessions(self) -> int:
        """Delete sessions past their expires_at. Returns count deleted."""
        now = datetime.now(timezone.utc).isoformat()
        cursor = self._execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        self._commit()
        return cursor.rowcount

    def get_user_sessions(self, user_id: str) -> list[Session]:
        """Get all active (non-revoked, non-expired) sessions for a user."""
        now = datetime.now(timezone.utc).isoformat()
        rows = self._execute(
            "SELECT data FROM sessions WHERE user_id = ? AND revoked_at IS NULL "
            "AND expires_at > ? ORDER BY last_activity_at DESC",
            (user_id, now),
        ).fetchall()
        return [Session.model_validate(json.loads(r[0])) for r in rows]

    # ── Audit Log ──────────────────────────────────────────────────

    def log_auth_event(
        self, event: str, user_id: str | None = None, email: str | None = None,
        ip_address: str | None = None, detail: str = "",
    ) -> None:
        from uuid import uuid4
        self._execute(
            "INSERT INTO auth_audit_log (id, event, user_id, email, ip_address, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid4()), event, user_id, email, ip_address, detail, datetime.now(timezone.utc).isoformat()),
        )
        self._commit()

    def get_auth_audit_log(self, limit: int = 50) -> list[dict]:
        rows = self._execute(
            "SELECT event, user_id, email, ip_address, detail, created_at FROM auth_audit_log "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {"event": r[0], "user_id": r[1], "email": r[2], "ip": r[3], "detail": r[4], "created_at": r[5]}
            for r in rows
        ]

    # ── Cross-resource queries (for dashboard pages) ─────────────────

    def list_all_reports(
        self,
        limit: int = 50,
        offset: int = 0,
        resource_id: str | None = None,
        run_type: str | None = None,
    ) -> tuple[list[dict], int]:
        """List reports across all resources with optional filters. Returns (reports, total)."""
        conditions: list[str] = []
        params: list = []
        if resource_id:
            conditions.append("r.resource_id = ?")
            params.append(resource_id)
        if run_type:
            conditions.append("r.run_type = ?")
            params.append(run_type)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        count_row = self._execute(
            f"SELECT COUNT(*) FROM reports r {where}", params
        ).fetchone()
        total = count_row[0] if count_row else 0

        rows = self._execute(
            f"""SELECT r.id, r.resource_id, r.run_type, r.created_at,
                       res.data AS resource_data, r.data AS report_data
                FROM reports r
                LEFT JOIN resources res ON res.id = r.resource_id
                {where}
                ORDER BY r.created_at DESC LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()

        reports = []
        for row in rows:
            report = Report.model_validate(json.loads(row[5]))
            resource_name = ""
            if row[4]:
                try:
                    resource_name = json.loads(row[4]).get("name", "")
                except (json.JSONDecodeError, KeyError):
                    pass
            content_preview = (report.content or "")[:150]
            if len(report.content or "") > 150:
                content_preview += "..."
            reports.append({
                "id": row[0],
                "resource_id": row[1],
                "resource_name": resource_name,
                "run_type": row[2],
                "created_at": row[3],
                "preview": content_preview,
            })
        return reports, total

    def list_recent_runs(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        run_type: str | None = None,
    ) -> tuple[list[Run], int]:
        """List runs across all resources with optional filters. Returns (runs, total)."""
        conditions: list[str] = []
        params: list = []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if run_type:
            conditions.append("run_type = ?")
            params.append(run_type)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        count_row = self._execute(
            f"SELECT COUNT(*) FROM runs {where}", params
        ).fetchone()
        total = count_row[0] if count_row else 0

        rows = self._execute(
            f"SELECT data FROM runs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        runs = [Run.model_validate(json.loads(r[0])) for r in rows]
        return runs, total

        """List auth audit events for the activity page."""
        rows = self._execute(
            "SELECT event, user_id, email, ip_address, detail, created_at "
            "FROM auth_audit_log ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [
            {"event": r[0], "user_id": r[1], "email": r[2], "ip": r[3],
             "detail": r[4], "created_at": r[5]}
            for r in rows
        ]

    def list_notifications_extended(
        self,
        limit: int = 50,
        offset: int = 0,
        resource_id: str | None = None,
        severity: str | None = None,
        channel: str | None = None,
        status: str | None = None,
    ) -> tuple[list[dict], int]:
        """List notifications with full filtering and pagination. Returns (notifications, total)."""
        conditions: list[str] = []
        params: list = []
        if resource_id:
            conditions.append("n.resource_id = ?")
            params.append(resource_id)
        if severity:
            conditions.append("n.severity = ?")
            params.append(severity)
        if channel:
            conditions.append("n.channel = ?")
            params.append(channel)
        if status:
            conditions.append("n.status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        count_row = self._execute(
            f"SELECT COUNT(*) FROM notification_log n {where}", params
        ).fetchone()
        total = count_row[0] if count_row else 0

        rows = self._execute(
            f"""SELECT n.id, n.resource_id, n.channel, n.severity, n.summary,
                       n.status, n.error, n.created_at
                FROM notification_log n
                {where}
                ORDER BY n.created_at DESC LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()
        notifications = [
            {
                "id": r[0], "resource_id": r[1], "channel": r[2],
                "severity": r[3], "summary": r[4], "status": r[5],
                "error": r[6], "created_at": r[7],
            }
            for r in rows
        ]
        return notifications, total

