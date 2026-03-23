"""SQLite storage layer for Supervisor.

One database file, one table per model. Each table stores the Pydantic model
as a JSON blob in a `data` column, with indexed columns for fast queries.

Synchronous SQLite — the bottleneck is Claude API latency (seconds), not DB I/O.
"""

from __future__ import annotations

import json
import sqlite3
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
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # ── Resources ────────────────────────────────────────────────────

    def save_resource(self, resource: Resource) -> None:
        data = json.dumps(resource.model_dump(mode="json"), default=str, ensure_ascii=False)
        self._conn.execute(
            "INSERT OR REPLACE INTO resources (id, parent_id, resource_type, data, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (resource.id, resource.parent_id, resource.resource_type, data, str(resource.created_at)),
        )
        self._conn.commit()

    def get_resource(self, resource_id: str) -> Resource | None:
        row = self._conn.execute(
            "SELECT data FROM resources WHERE id = ?", (resource_id,)
        ).fetchone()
        if row is None:
            return None
        return Resource.model_validate(json.loads(row[0]))

    def list_resources(self, parent_id: str | None = None) -> list[Resource]:
        if parent_id is not None:
            rows = self._conn.execute(
                "SELECT data FROM resources WHERE parent_id = ? ORDER BY created_at",
                (parent_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT data FROM resources ORDER BY created_at"
            ).fetchall()
        return [Resource.model_validate(json.loads(r[0])) for r in rows]

    def delete_resource(self, resource_id: str) -> None:
        self._conn.execute("DELETE FROM resources WHERE id = ?", (resource_id,))
        self._conn.commit()

    def get_resource_tree(self, resource_id: str) -> list[Resource]:
        """Walk up the parent chain. Returns [root, ..., self]."""
        chain: list[Resource] = []
        current_id: str | None = resource_id
        visited: set[str] = set()

        while current_id and current_id not in visited:
            visited.add(current_id)
            resource = self.get_resource(current_id)
            if resource is None:
                break
            chain.append(resource)
            current_id = resource.parent_id

        chain.reverse()
        return chain

    # ── System Contexts ──────────────────────────────────────────────

    def save_context(self, ctx: SystemContext) -> None:
        data = json.dumps(ctx.model_dump(mode="json"), default=str, ensure_ascii=False)
        self._conn.execute(
            "INSERT OR REPLACE INTO system_contexts (id, resource_id, version, data, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (ctx.id, ctx.resource_id, ctx.version, data, str(ctx.created_at)),
        )
        self._conn.commit()

    def get_latest_context(self, resource_id: str) -> SystemContext | None:
        row = self._conn.execute(
            "SELECT data FROM system_contexts WHERE resource_id = ? ORDER BY version DESC LIMIT 1",
            (resource_id,),
        ).fetchone()
        if row is None:
            return None
        return SystemContext.model_validate(json.loads(row[0]))

    def get_context_history(self, resource_id: str, limit: int = 5) -> list[SystemContext]:
        rows = self._conn.execute(
            "SELECT data FROM system_contexts WHERE resource_id = ? ORDER BY version DESC LIMIT ?",
            (resource_id, limit),
        ).fetchall()
        return [SystemContext.model_validate(json.loads(r[0])) for r in rows]

    # ── Checklists ───────────────────────────────────────────────────

    def save_checklist(self, checklist: Checklist) -> None:
        data = json.dumps(checklist.model_dump(mode="json"), default=str, ensure_ascii=False)
        self._conn.execute(
            "INSERT OR REPLACE INTO checklists (id, resource_id, version, data, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (checklist.id, checklist.resource_id, checklist.version, data, str(checklist.created_at)),
        )
        self._conn.commit()

    def get_latest_checklist(self, resource_id: str) -> Checklist | None:
        row = self._conn.execute(
            "SELECT data FROM checklists WHERE resource_id = ? ORDER BY version DESC LIMIT 1",
            (resource_id,),
        ).fetchone()
        if row is None:
            return None
        return Checklist.model_validate(json.loads(row[0]))

    # ── Reports ──────────────────────────────────────────────────────

    def save_report(self, report: Report) -> None:
        data = json.dumps(report.model_dump(mode="json"), default=str, ensure_ascii=False)
        self._conn.execute(
            "INSERT OR REPLACE INTO reports (id, resource_id, run_type, data, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (report.id, report.resource_id, str(report.run_type), data, str(report.created_at)),
        )
        self._conn.commit()

    def get_report(self, report_id: str) -> Report | None:
        row = self._conn.execute(
            "SELECT data FROM reports WHERE id = ?", (report_id,)
        ).fetchone()
        if row is None:
            return None
        return Report.model_validate(json.loads(row[0]))

    def get_recent_reports(
        self, resource_id: str, run_type: RunType, limit: int = 3
    ) -> list[Report]:
        rows = self._conn.execute(
            "SELECT data FROM reports WHERE resource_id = ? AND run_type = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (resource_id, str(run_type), limit),
        ).fetchall()
        return [Report.model_validate(json.loads(r[0])) for r in rows]

    # ── Evaluations ──────────────────────────────────────────────────

    def save_evaluation(self, evaluation: Evaluation) -> None:
        data = json.dumps(evaluation.model_dump(mode="json"), default=str, ensure_ascii=False)
        self._conn.execute(
            "INSERT OR REPLACE INTO evaluations (id, report_id, resource_id, data, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (evaluation.id, evaluation.report_id, evaluation.resource_id, data, str(evaluation.created_at)),
        )
        self._conn.commit()

    def get_evaluation(self, evaluation_id: str) -> Evaluation | None:
        row = self._conn.execute(
            "SELECT data FROM evaluations WHERE id = ?", (evaluation_id,)
        ).fetchone()
        if row is None:
            return None
        return Evaluation.model_validate(json.loads(row[0]))

    # ── Runs ─────────────────────────────────────────────────────────

    def save_run(self, run: Run) -> None:
        data = json.dumps(run.model_dump(mode="json"), default=str, ensure_ascii=False)
        self._conn.execute(
            "INSERT OR REPLACE INTO runs (id, resource_id, run_type, status, data, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run.id, run.resource_id, str(run.run_type), str(run.status), data, str(run.created_at)),
        )
        self._conn.commit()

    def get_run(self, run_id: str) -> Run | None:
        row = self._conn.execute(
            "SELECT data FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return Run.model_validate(json.loads(row[0]))

    def get_pending_runs(self) -> list[Run]:
        rows = self._conn.execute(
            "SELECT data FROM runs WHERE status = ? ORDER BY created_at",
            (str(RunStatus.PENDING),),
        ).fetchall()
        return [Run.model_validate(json.loads(r[0])) for r in rows]

    def get_runs(self, resource_id: str, limit: int = 10) -> list[Run]:
        rows = self._conn.execute(
            "SELECT data FROM runs WHERE resource_id = ? ORDER BY created_at DESC LIMIT ?",
            (resource_id, limit),
        ).fetchall()
        return [Run.model_validate(json.loads(r[0])) for r in rows]

    def get_latest_run(self, resource_id: str, run_type: RunType) -> Run | None:
        row = self._conn.execute(
            "SELECT data FROM runs WHERE resource_id = ? AND run_type = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (resource_id, str(run_type)),
        ).fetchone()
        if row is None:
            return None
        return Run.model_validate(json.loads(row[0]))
