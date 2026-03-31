"""SQLite storage layer for Supervisor.

One database file, one table per model. Each table stores the Pydantic model
as a JSON blob in a `data` column, with indexed columns for fast queries.

Synchronous SQLite — the bottleneck is Claude API latency (seconds), not DB I/O.
"""

from __future__ import annotations

import json
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
    SystemContext,
)

DB_PATH_DEFAULT = ".supervisor/supervisor.db"

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
"""


class Store:
    """SQLite storage layer for all Supervisor models."""

    def __init__(self, db_path: str | Path = DB_PATH_DEFAULT):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()  # Reentrant — same thread can acquire multiple times
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._execute("PRAGMA journal_mode=WAL")
        self._execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

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

    def delete_resource(self, resource_id: str) -> None:
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

    def get_runs(self, resource_id: str, limit: int = 10) -> list[Run]:
        rows = self._execute(
            "SELECT data FROM runs WHERE resource_id = ? ORDER BY created_at DESC LIMIT ?",
            (resource_id, limit),
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
