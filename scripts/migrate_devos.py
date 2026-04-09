#!/usr/bin/env python3
"""Migrate DevOS data into Supervisor.

Reads from a DevOS SQLite database and imports projects as codebase resources,
findings as work items, and blocklist entries.

Usage:
  python scripts/migrate_devos.py --devos-db /path/to/devos.db --supavision-db .supavision/supavision.db
  python scripts/migrate_devos.py --devos-db /path/to/devos.db --supavision-db .supavision/supavision.db --dry-run
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# Add parent to path so we can import supavision
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from supavision.db import Store
from supavision.models import (
    BlocklistEntry,
    Feedback,
    FeedbackType,
    Finding,
    FindingSeverity,
    FindingStage,
    ManualTask,
    Priority,
    Resource,
    Schedule,
    TaskCategory,
    TaskSource,
    Transition,
)


def _log(msg: str) -> None:
    print(f"[migrate] {msg}", flush=True)


def migrate(
    devos_db_path: str,
    supavision_db_path: str,
    dry_run: bool = False,
) -> dict:
    """Migrate DevOS data into Supervisor. Returns migration stats."""
    if not Path(devos_db_path).exists():
        _log(f"Error: DevOS database not found at {devos_db_path}")
        sys.exit(1)

    # Open DevOS DB (read-only)
    devos_conn = sqlite3.connect(f"file:{devos_db_path}?mode=ro", uri=True)
    devos_conn.row_factory = sqlite3.Row

    stats = {
        "projects": 0,
        "findings": 0,
        "manual_tasks": 0,
        "blocklist": 0,
        "feedback": 0,
        "skipped_duplicates": 0,
    }

    # Open Supervisor Store (write)
    store = Store(supavision_db_path) if not dry_run else None

    # Track project_id -> resource_id mapping
    project_map: dict[str, str] = {}

    # ── Migrate projects as codebase resources ──────────────────────
    _log("Migrating projects...")
    try:
        projects = devos_conn.execute(
            "SELECT data FROM projects ORDER BY created_at"
        ).fetchall()
    except sqlite3.OperationalError:
        projects = []

    for row in projects:
        data = json.loads(row["data"])
        project_id = data.get("id", "")
        path = data.get("path", "")
        name = data.get("name", "")

        if not path or not name:
            continue

        # Check if resource already exists for this path
        resource_id = str(uuid4())
        if store:
            existing = store.list_resources()
            for r in existing:
                if r.config.get("path") == path:
                    _log(f"  Skipping {name} — resource already exists for {path}")
                    resource_id = r.id
                    project_map[project_id] = resource_id
                    stats["skipped_duplicates"] += 1
                    break
            else:
                resource = Resource(
                    id=resource_id,
                    name=name,
                    resource_type="codebase",
                    config={
                        "path": path,
                        "language_hint": data.get("language_hint", ""),
                    },
                )
                store.save_resource(resource)
                _log(f"  Created resource: {name} ({path})")
                stats["projects"] += 1
        else:
            _log(f"  [dry-run] Would create resource: {name} ({path})")
            stats["projects"] += 1

        project_map[project_id] = resource_id

    # ── Migrate findings/tasks ──────────────────────────────────────
    _log("Migrating findings and tasks...")
    try:
        items = devos_conn.execute(
            "SELECT data, source FROM findings ORDER BY created_at"
        ).fetchall()
    except sqlite3.OperationalError:
        items = []

    for row in items:
        data = json.loads(row["data"])
        source = row["source"]
        project_id = data.get("project_id", "")
        resource_id = project_map.get(project_id)

        if not resource_id:
            continue

        if source == "manual":
            task = ManualTask(
                resource_id=resource_id,
                stage=FindingStage(data.get("stage", "created")),
                title=data.get("title", ""),
                description=data.get("description", ""),
                task_category=TaskCategory(data.get("task_category", "improvement")),
                priority=Priority(data.get("priority", "medium")),
                severity=FindingSeverity(data.get("severity", "medium")),
                file_path=data.get("file_path", ""),
                line_number=data.get("line_number", 0),
                evaluation_verdict=data.get("evaluation_verdict", ""),
                evaluation_reasoning=data.get("evaluation_reasoning", ""),
                evaluation_fix_approach=data.get("evaluation_fix_approach", ""),
                evaluation_effort=data.get("evaluation_effort", ""),
                rejection_reason=data.get("rejection_reason", ""),
            )
            if store:
                store.save_work_item(task)
            stats["manual_tasks"] += 1
        else:
            finding = Finding(
                resource_id=resource_id,
                stage=FindingStage(data.get("stage", "scanned")),
                category=data.get("category", ""),
                severity=FindingSeverity(data.get("severity", "medium")),
                language=data.get("language", ""),
                file_path=data.get("file_path", ""),
                line_number=data.get("line_number", 0),
                snippet=data.get("snippet", ""),
                context_before=data.get("context_before", []),
                context_after=data.get("context_after", []),
                pattern_name=data.get("pattern_name", ""),
                evaluation_verdict=data.get("evaluation_verdict", ""),
                evaluation_reasoning=data.get("evaluation_reasoning", ""),
                evaluation_fix_approach=data.get("evaluation_fix_approach", ""),
                evaluation_effort=data.get("evaluation_effort", ""),
                rejection_reason=data.get("rejection_reason", ""),
                blocklist_match=data.get("blocklist_match", ""),
            )
            if store:
                store.save_work_item(finding)
            stats["findings"] += 1

    # ── Migrate blocklist ───────────────────────────────────────────
    _log("Migrating blocklist...")
    try:
        bl_rows = devos_conn.execute(
            "SELECT data FROM blocklist ORDER BY created_at"
        ).fetchall()
    except sqlite3.OperationalError:
        bl_rows = []

    for row in bl_rows:
        data = json.loads(row["data"])
        entry = BlocklistEntry(
            pattern_signature=data.get("pattern_signature", ""),
            category=data.get("category", ""),
            language=data.get("language", ""),
            description=data.get("description", ""),
            source_finding_id=data.get("source_finding_id", ""),
            match_count=data.get("match_count", 0),
        )
        if store:
            # Skip if signature already exists
            existing = store.get_blocklist_entry_by_signature(entry.pattern_signature)
            if existing:
                stats["skipped_duplicates"] += 1
                continue
            store.save_blocklist_entry(entry)
        stats["blocklist"] += 1

    devos_conn.close()
    if store:
        store.close()

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Migrate DevOS data into Supervisor"
    )
    parser.add_argument(
        "--devos-db", required=True,
        help="Path to DevOS SQLite database",
    )
    parser.add_argument(
        "--supavision-db", default=".supavision/supavision.db",
        help="Path to Supervisor SQLite database",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be migrated without writing",
    )
    args = parser.parse_args()

    _log(f"Source: {args.devos_db}")
    _log(f"Target: {args.supavision_db}")
    if args.dry_run:
        _log("DRY RUN — no changes will be made")

    stats = migrate(args.devos_db, args.supavision_db, args.dry_run)

    _log("\n=== Migration Summary ===")
    _log(f"  Projects → Resources: {stats['projects']}")
    _log(f"  Scanner findings:     {stats['findings']}")
    _log(f"  Manual tasks:         {stats['manual_tasks']}")
    _log(f"  Blocklist entries:    {stats['blocklist']}")
    _log(f"  Skipped duplicates:   {stats['skipped_duplicates']}")
    total = stats["projects"] + stats["findings"] + stats["manual_tasks"] + stats["blocklist"]
    _log(f"  Total migrated:       {total}")


if __name__ == "__main__":
    main()
