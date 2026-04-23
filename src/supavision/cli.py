"""CLI entry point for Supavision.

JSON output to stdout. Human-readable messages to stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from ._auth_check import check_claude_auth


def _load_dotenv() -> None:
    """Load .env file if it exists. Simple key=value parser, no dependency."""
    env_path = Path(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:  # Don't override existing env vars
                os.environ[key] = value
_load_dotenv()

from .db import Store
from .engine import Engine
from .models import (
    Credential,
    Resource,
    RunType,
    Schedule,
)
from .templates import TEMPLATE_DIR_DEFAULT, list_templates

DB_PATH_DEFAULT = ".supavision/supavision.db"
def _json_out(data: dict) -> None:
    json.dump(data, sys.stdout, default=str, ensure_ascii=False)
    sys.stdout.write("\n")
def _error(msg: str) -> None:
    _json_out({"ok": False, "error": msg})
    sys.exit(1)
# ── Table formatting (Workstream E1) ────────────────────────────────
#
# When --format=table (or auto on TTY), list commands print human-readable
# tables instead of raw JSON. Hand-rolled to avoid adding a dependency.

_FORMAT: str = "json"  # Set by main() from args.format
def _should_table() -> bool:
    """Whether the current output mode calls for table format."""
    if _FORMAT == "json":
        return False
    if _FORMAT == "table":
        return True
    # auto: table on TTY, JSON when piped
    return sys.stdout.isatty()
def _print_table(headers: list[str], rows: list[list[str]], min_width: int = 6) -> None:
    """Print a simple aligned table to stdout."""
    if not rows:
        print("(no results)")
        return
    # Compute column widths from headers + data
    widths = [max(min_width, len(h)) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(str(cell)))
    # Header
    header_line = "  ".join(h.upper().ljust(w) for h, w in zip(headers, widths))
    print(header_line)
    print("  ".join("-" * w for w in widths))
    # Rows
    for row in rows:
        cells = [str(c).ljust(w) for c, w in zip(row, widths)]
        print("  ".join(cells))
def _get_store(args: argparse.Namespace) -> Store:
    db_path = getattr(args, "db", DB_PATH_DEFAULT)
    return Store(db_path)
def _get_engine(store: Store, args: argparse.Namespace) -> Engine:
    template_dir = getattr(args, "templates", TEMPLATE_DIR_DEFAULT)
    return Engine(store=store, template_dir=template_dir)
# ── Resource commands ────────────────────────────────────────────────
def cmd_resource_add(args: argparse.Namespace) -> None:
    store = _get_store(args)
    config = {}
    if args.config:
        for item in args.config:
            if "=" not in item:
                _error(f"Invalid config format: {item!r}. Use key=value.")
            key, val = item.split("=", 1)
            config[key] = val

    resource = Resource(
        name=args.name,
        resource_type=args.type,
        parent_id=args.parent or None,
        config=config,
    )
    store.save_resource(resource)
    _json_out({"ok": True, "command": "resource_add", "resource_id": resource.id, "name": resource.name})
def cmd_resource_list(args: argparse.Namespace) -> None:
    store = _get_store(args)
    resources = store.list_resources()
    if _should_table():
        _print_table(
            ["Name", "Type", "ID"],
            [[r.name, r.resource_type, r.id[:12]] for r in resources],
        )
    else:
        _json_out({
            "ok": True,
            "command": "resource_list",
            "resources": [
                {"id": r.id, "name": r.name, "type": r.resource_type, "parent_id": r.parent_id}
                for r in resources
            ],
        })
def cmd_resource_show(args: argparse.Namespace) -> None:
    store = _get_store(args)
    resource = store.get_resource(args.resource_id)
    if not resource:
        _error(f"Resource {args.resource_id} not found. Run 'supavision resource-list' to see available resources.")
    _json_out({"ok": True, "command": "resource_show", "resource": resource.model_dump(mode="json")})
def cmd_resource_set_schedule(args: argparse.Namespace) -> None:
    store = _get_store(args)
    resource = store.get_resource(args.resource_id)
    if not resource:
        _error(f"Resource {args.resource_id} not found. Run 'supavision resource-list' to see available resources.")

    from croniter import croniter

    if args.discovery:
        try:
            croniter(args.discovery)
        except (ValueError, KeyError) as e:
            _error(f"Invalid discovery cron expression '{args.discovery}': {e}")
        resource.discovery_schedule = Schedule(cron=args.discovery)
    if args.health_check:
        try:
            croniter(args.health_check)
        except (ValueError, KeyError) as e:
            _error(f"Invalid health check cron expression '{args.health_check}': {e}")
        resource.health_check_schedule = Schedule(cron=args.health_check)

    store.save_resource(resource)
    _json_out({"ok": True, "command": "set_schedule", "resource_id": resource.id})
def cmd_resource_add_credential(args: argparse.Namespace) -> None:
    store = _get_store(args)
    resource = store.get_resource(args.resource_id)
    if not resource:
        _error(f"Resource {args.resource_id} not found. Run 'supavision resource-list' to see available resources.")

    resource.credentials[args.name] = Credential(env_var=args.env_var)
    store.save_resource(resource)
    _json_out({"ok": True, "command": "add_credential", "resource_id": resource.id, "credential": args.name})
# ── Run commands ─────────────────────────────────────────────────────
def cmd_run_discovery(args: argparse.Namespace) -> None:
    store = _get_store(args)
    engine = _get_engine(store, args)
    run = engine.run_discovery(args.resource_id)
    eval_obj = store.get_evaluation(run.evaluation_id) if run.evaluation_id else None
    _json_out({
        "ok": True,
        "command": "run_discovery",
        "run_id": run.id,
        "status": str(run.status),
        "severity": str(eval_obj.severity) if eval_obj else None,
        "should_alert": eval_obj.should_alert if eval_obj else False,
    })
def cmd_run_health_check(args: argparse.Namespace) -> None:
    store = _get_store(args)
    engine = _get_engine(store, args)
    run = engine.run_health_check(args.resource_id)
    eval_obj = store.get_evaluation(run.evaluation_id) if run.evaluation_id else None
    _json_out({
        "ok": True,
        "command": "run_health_check",
        "run_id": run.id,
        "status": str(run.status),
        "severity": str(eval_obj.severity) if eval_obj else None,
        "should_alert": eval_obj.should_alert if eval_obj else False,
    })
def cmd_run_status(args: argparse.Namespace) -> None:
    store = _get_store(args)
    run = store.get_run(args.run_id)
    if not run:
        _error(f"Run {args.run_id} not found. Run 'supavisionreport-list <resource_id>' to find run IDs.")
    _json_out({"ok": True, "command": "run_status", "run": run.model_dump(mode="json")})
# ── Report commands ──────────────────────────────────────────────────
def cmd_report_show(args: argparse.Namespace) -> None:
    store = _get_store(args)
    report = store.get_report(args.report_id)
    if not report:
        _error(f"Report {args.report_id} not found. Run 'supavisionreport-list <resource_id>' to find report IDs.")
    # Print content to stderr for readability, JSON to stdout
    print(report.content, file=sys.stderr)
    _json_out({"ok": True, "command": "report_show", "report_id": report.id, "run_type": str(report.run_type)})
def cmd_report_list(args: argparse.Namespace) -> None:
    store = _get_store(args)
    reports = store.get_recent_reports(
        args.resource_id,
        RunType(args.type) if args.type else RunType.HEALTH_CHECK,
        limit=args.limit,
    )
    if _should_table():
        _print_table(
            ["ID", "Type", "Status", "Created"],
            [
                [r.id[:12], str(r.run_type), r.payload.status if r.payload else "legacy", str(r.created_at)[:19]]
                for r in reports
            ],
        )
    else:
        _json_out({
            "ok": True,
            "command": "report_list",
            "reports": [
                {"id": r.id, "run_type": str(r.run_type), "created_at": str(r.created_at)}
                for r in reports
            ],
        })
# ── Context/checklist commands ───────────────────────────────────────
def cmd_context_show(args: argparse.Namespace) -> None:
    store = _get_store(args)
    ctx = store.get_latest_context(args.resource_id)
    if not ctx:
        _error(f"No system context found for resource {args.resource_id}")
    print(ctx.content, file=sys.stderr)
    _json_out({"ok": True, "command": "context_show", "version": ctx.version, "resource_id": ctx.resource_id})
def cmd_checklist_show(args: argparse.Namespace) -> None:
    store = _get_store(args)
    cl = store.get_latest_checklist(args.resource_id)
    if not cl:
        _error(f"No checklist found for resource {args.resource_id}")
    for item in cl.items:
        print(f"  [{item.source}] {item.description}", file=sys.stderr)
    _json_out({
        "ok": True,
        "command": "checklist_show",
        "version": cl.version,
        "item_count": len(cl.items),
    })
def cmd_checklist_add(args: argparse.Namespace) -> None:
    store = _get_store(args)
    resource = store.get_resource(args.resource_id)
    if not resource:
        _error(f"Resource {args.resource_id} not found. Run 'supavision resource-list' to see available resources.")

    resource.monitoring_requests.append(args.request)
    store.save_resource(resource)
    _json_out({
        "ok": True,
        "command": "checklist_add",
        "resource_id": resource.id,
        "request": args.request,
    })
# ── Template commands ────────────────────────────────────────────────
def cmd_template_list(args: argparse.Namespace) -> None:
    templates = list_templates(getattr(args, "templates", TEMPLATE_DIR_DEFAULT))
    _json_out({"ok": True, "command": "template_list", "templates": templates})
# ── Scheduler ────────────────────────────────────────────────────────
def cmd_run_scheduler(args: argparse.Namespace) -> None:
    from .scheduler import Scheduler

    store = _get_store(args)
    engine = _get_engine(store, args)
    scheduler = Scheduler(store=store, engine=engine)

    print("Starting scheduler... (Ctrl+C to stop)", file=sys.stderr)
    try:
        scheduler.start()
    except KeyboardInterrupt:
        scheduler.stop()
        print("\nScheduler stopped.", file=sys.stderr)
# ── Doctor ───────────────────────────────────────────────────────────
def cmd_doctor(args: argparse.Namespace) -> None:
    checks: list[dict] = []

    # Backend
    backend = os.environ.get("SUPAVISION_BACKEND", "claude_cli")
    checks.append({"check": "backend", "ok": True, "detail": backend})

    # Claude CLI (needed for claude_cli backend)
    if backend == "claude_cli":
        claude_path = shutil.which("claude")
        has_claude = bool(claude_path)
        checks.append({"check": "claude_cli", "ok": has_claude, "detail": claude_path or "not found in PATH"})
        if has_claude:
            auth_ok, auth_detail = check_claude_auth()
            checks.append({"check": "claude_auth", "ok": auth_ok, "detail": auth_detail})
    else:
        # OPENROUTER_API_KEY (needed for openrouter backend)
        key = os.environ.get("OPENROUTER_API_KEY", "")
        has_key = bool(key and not key.startswith("sk-or-your"))
        checks.append({"check": "openrouter_api_key", "ok": has_key, "detail": "set" if has_key else "missing"})

    # Template directory
    tdir = Path(getattr(args, "templates", TEMPLATE_DIR_DEFAULT))
    has_templates = tdir.exists()
    checks.append({"check": "template_dir", "ok": has_templates, "detail": str(tdir.resolve())})

    # SQLite writable
    db_dir = Path(args.db).parent
    try:
        db_dir.mkdir(parents=True, exist_ok=True)
        writable = os.access(db_dir, os.W_OK)
    except OSError:
        writable = False
    checks.append({"check": "db_writable", "ok": writable, "detail": str(db_dir.resolve())})

    # croniter
    try:
        import croniter as _  # noqa: F401
        has_croniter = True
    except ImportError:
        has_croniter = False
    detail = "importable" if has_croniter else "not installed"
    checks.append({"check": "croniter", "ok": has_croniter, "detail": detail})

    all_ok = all(c["ok"] for c in checks)
    for c in checks:
        icon = "OK" if c["ok"] else "FAIL"
        print(f"  [{icon}] {c['check']}: {c['detail']}", file=sys.stderr)

    _json_out({"ok": all_ok, "command": "doctor", "checks": checks})
    if not all_ok:
        sys.exit(1)
# ── Setup wizard ─────────────────────────────────────────────────────
def cmd_setup(args: argparse.Namespace) -> None:
    """Guided first-run setup: checks prerequisites and authenticates Claude CLI."""
    def _print(msg: str) -> None:
        print(msg, file=sys.stderr)

    backend = os.environ.get("SUPAVISION_BACKEND", "claude_cli")
    _print(f"  Backend: {backend}")

    if backend != "claude_cli":
        _print("  OpenRouter backend selected — skipping Claude CLI auth check.")
        _print("  Ensure OPENROUTER_API_KEY is set, then run: supavision create-admin")
        return

    # Check binary
    claude_path = shutil.which("claude")
    if not claude_path:
        _print("  [FAIL] Claude CLI not found in PATH.")
        _print("         Install it: https://docs.anthropic.com/en/docs/claude-code")
        sys.exit(1)
    _print(f"  [OK]   Claude CLI: {claude_path}")

    # Check auth
    auth_ok, auth_detail = check_claude_auth()
    if auth_ok:
        _print(f"  [OK]   Claude CLI auth: {auth_detail}")
    else:
        _print(f"  [WARN] Claude CLI auth: {auth_detail}")

        # Docker: can't open a browser inside the container
        if Path("/.dockerenv").exists():
            _print("")
            _print("  Running inside Docker. Authenticate from outside the container:")
            _print("    docker exec -it <container-name> claude login")
            _print("")
            _print("  Or mount your host credentials at startup (see docker-compose.yml).")
            sys.exit(0)

        # Non-interactive environment
        if not sys.stdin.isatty():
            _print("")
            _print("  Non-interactive environment detected. Run manually:")
            _print("    claude login")
            _print("  Or set ANTHROPIC_API_KEY in your environment.")
            sys.exit(1)

        # Offer to authenticate interactively
        try:
            answer = input("  Authenticate now with 'claude login'? [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            _print("")
            sys.exit(1)

        if answer in ("", "y", "yes"):
            import subprocess
            _print("")
            result = subprocess.run([claude_path, "login"])
            _print("")
            if result.returncode == 0:
                auth_ok, auth_detail = check_claude_auth()
                if auth_ok:
                    _print(f"  [OK]   Claude CLI auth: {auth_detail}")
                else:
                    _print("  [WARN] Authentication may not have completed. Run 'supavision doctor' to verify.")
            else:
                _print("  [FAIL] 'claude login' exited with an error. Try running it manually.")
                sys.exit(1)
        else:
            _print("  Skipped. Run 'claude login' or set ANTHROPIC_API_KEY before triggering runs.")

    _print("")
    _print("  Setup complete.")
    _print("  Next steps:")
    _print("    supavision create-admin   # create your first admin user")
    _print("    supavision serve          # start the web dashboard")


# ── Notification commands ────────────────────────────────────────────
def cmd_notify_test(args: argparse.Namespace) -> None:
    store = _get_store(args)
    resource = store.get_resource(args.resource_id)
    if not resource:
        _error(f"Resource {args.resource_id} not found. Run 'supavision resource-list' to see available resources.")

    from .models import Evaluation, Report, RunType, Severity
    from .notifications import send_alert

    test_report = Report(
        resource_id=resource.id,
        run_type=RunType.HEALTH_CHECK,
        content="This is a test notification from Supavision. If you see this, notifications are working.",
    )
    test_eval = Evaluation(
        report_id=test_report.id,
        resource_id=resource.id,
        severity=Severity(args.severity),
        summary="Test notification — verifying webhook configuration",
        should_alert=True,
    )

    import asyncio

    channels, _ = asyncio.run(
        send_alert(resource, test_report, test_eval, skip_dedup=True)
    )
    if channels:
        _json_out({"ok": True, "command": "notify_test", "channels": channels})
    else:
        _error(
            "No notification channels configured or all failed. "
            "Set slack_webhook in resource config or SLACK_WEBHOOK env var."
        )
def cmd_notify_configure(args: argparse.Namespace) -> None:
    store = _get_store(args)
    resource = store.get_resource(args.resource_id)
    if not resource:
        _error(f"Resource {args.resource_id} not found. Run 'supavision resource-list' to see available resources.")

    updated = []

    if args.slack_webhook:
        from .notifications import validate_webhook_url

        try:
            validate_webhook_url(args.slack_webhook)
        except ValueError as e:
            _error(f"Invalid slack webhook URL: {e}")
        resource.config["slack_webhook"] = args.slack_webhook
        updated.append("slack_webhook")

    if args.webhook_url:
        from .notifications import validate_webhook_url

        try:
            validate_webhook_url(args.webhook_url)
        except ValueError as e:
            _error(f"Invalid webhook URL: {e}")
        resource.config["webhook_url"] = args.webhook_url
        updated.append("webhook_url")

    if args.clear:
        resource.config.pop("slack_webhook", None)
        resource.config.pop("webhook_url", None)
        resource.config.pop("_last_alert_key", None)
        updated.append("cleared_all")

    if not updated:
        _error("Nothing to update. Use --slack-webhook, --webhook-url, or --clear.")

    store.save_resource(resource)
    _json_out({
        "ok": True,
        "command": "notify_configure",
        "resource_id": resource.id,
        "updated": updated,
    })
def cmd_context_diff(args: argparse.Namespace) -> None:
    store = _get_store(args)
    history = store.get_context_history(args.resource_id, limit=2)
    if len(history) < 2:
        _error(f"Need at least 2 context versions to diff (found {len(history)})")

    from .discovery_diff import compute_diff

    current, previous = history[0], history[1]
    diff = compute_diff(current.content, previous.content)

    if diff.has_changes:
        print(diff.summary(), file=sys.stderr)
    else:
        print("No changes detected between versions.", file=sys.stderr)

    _json_out({
        "ok": True,
        "command": "context_diff",
        "resource_id": args.resource_id,
        "current_version": current.version,
        "previous_version": previous.version,
        "has_changes": diff.has_changes,
        "is_significant": diff.is_significant,
        "added": diff.total_added,
        "removed": diff.total_removed,
        "changed": diff.total_changed,
    })
def cmd_purge(args: argparse.Namespace) -> None:
    store = _get_store(args)
    days = args.days
    if args.dry_run:
        # Count what would be deleted
        from datetime import datetime, timedelta, timezone

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with store._lock:
            reports_count = store._conn.execute(
                "SELECT COUNT(*) FROM reports WHERE created_at < ?", (cutoff,)
            ).fetchone()[0]
            runs_count = store._conn.execute(
                "SELECT COUNT(*) FROM runs WHERE status IN ('completed', 'failed') AND created_at < ?",
                (cutoff,),
            ).fetchone()[0]
        msg = f"Dry run: would delete {reports_count} reports and {runs_count} runs older than {days} days"
        print(msg, file=sys.stderr)
        _json_out({
            "ok": True, "command": "purge", "dry_run": True,
            "reports": reports_count, "runs": runs_count, "days": days,
        })
    else:
        reports_deleted = store.purge_old_reports(days)
        runs_deleted = store.purge_old_runs(days)
        print(f"Purged {reports_deleted} reports and {runs_deleted} runs older than {days} days", file=sys.stderr)
        _json_out({
            "ok": True, "command": "purge", "dry_run": False,
            "reports_deleted": reports_deleted, "runs_deleted": runs_deleted, "days": days,
        })
def cmd_seed_demo(args: argparse.Namespace) -> None:
    """Populate the database with sample data for demo/evaluation."""
    from datetime import timedelta

    from .models import (
        Checklist,
        ChecklistItem,
        Evaluation,
        Report,
        Resource,
        Run,
        RunStatus,
        RunType,
        Schedule,
        Severity,
        SystemContext,
    )

    store = _get_store(args)

    # Check for existing data
    existing = store.list_resources()
    if existing and not args.force:
        _error(
            f"Database already has {len(existing)} resource(s). "
            "Use --force to add demo data anyway."
        )

    print("Seeding demo data...", file=sys.stderr)

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    # ── 1. Server resource with pre-baked baseline ──────────────────

    server = Resource(
        name="production-server",
        resource_type="server",
        config={"ssh_host": "10.0.1.5", "ssh_user": "ubuntu"},
        health_check_schedule=Schedule(cron="0 */6 * * *"),
        discovery_schedule=Schedule(cron="0 0 * * 0"),
        enabled=False,  # Paused — demo data only, no real SSH connections
    )
    store.save_resource(server)

    # Discovery baseline
    baseline_content = """# System Context: production-server

## Hardware & Resources
- **CPU**: 2x vCPU — Intel Xeon Platinum 8259CL @ 2.50GHz (2 threads, 1 core)
- **Memory**: 3.7 GB total | 1.7 GB used | 189 MB free | 2.2 GB buff/cache
- **Disk (root /)**: 16 GB total | 14 GB used (91%) — CRITICAL
- **Disk (/home)**: ~82 GB — primary consumer on root partition
- **Load average**: 0.35 / 0.24 / 0.21

## Operating System
- **Distro**: Ubuntu 24.04.3 LTS (Noble Numbat)
- **Kernel**: 6.17.0-1009-aws

## Services
| Service | Status | Port |
|---------|--------|------|
| nginx | active | 80, 443 |
| postgresql@16-main | active | 127.0.0.1:5432 |
| pm2-ubuntu | active | — |
| ssh | active | 0.0.0.0:22 |

## Applications
| App | Port | Stack | Status |
|-----|------|-------|--------|
| colab | 3000 | Node.js 20 / Express | Online (PM2) |
| epidemiorum | 3001 | Node.js 20 / Express | Online (PM2) |

## Databases
| Type | Version | Used By |
|------|---------|---------|
| PostgreSQL | 16 | colab, epidemiorum |
| SQLite | — | supervisor |
"""

    ctx = SystemContext(resource_id=server.id, content=baseline_content, version=1)
    store.save_context(ctx)

    checklist_items = [
        "Disk usage on / is below 90%",
        "All PM2 processes are online",
        "nginx is responding on ports 80 and 443",
        "PostgreSQL accepting connections on 5432",
        "SSH is accessible on port 22",
        "SSL certificates are valid and not expiring within 30 days",
        "No OOM killer activity in kernel logs",
        "System load average is below 2.0",
    ]
    checklist = Checklist(
        resource_id=server.id,
        items=[ChecklistItem(description=desc) for desc in checklist_items],
        version=1,
    )
    store.save_checklist(checklist)

    # Create 5 health check runs spanning 30 days
    severities = [
        (30, Severity.HEALTHY, "All systems operational. Disk at 78%."),
        (20, Severity.HEALTHY, "All services running. Memory usage normal."),
        (10, Severity.WARNING, "Disk usage at 88% on /. Cleanup recommended."),
        (5, Severity.HEALTHY, "Disk cleaned up to 72%. All services healthy."),
        (1, Severity.CRITICAL, "Disk at 91%. ENOSPC errors occurring. Immediate action required."),
    ]

    for days_ago, severity, summary in severities:
        ts = now - timedelta(days=days_ago)
        run = Run(
            resource_id=server.id,
            run_type=RunType.HEALTH_CHECK,
            status=RunStatus.COMPLETED,
            started_at=ts,
            completed_at=ts + timedelta(seconds=120),
        )

        report = Report(
            resource_id=server.id,
            run_type=RunType.HEALTH_CHECK,
            content=f"Health check completed.\n\n{summary}",
        )
        report.created_at = ts
        store.save_report(report)

        evaluation = Evaluation(
            report_id=report.id,
            resource_id=server.id,
            severity=severity,
            summary=summary,
            should_alert=severity != Severity.HEALTHY,
            strategy_used="keyword",
        )
        evaluation.created_at = ts
        store.save_evaluation(evaluation)

        run.report_id = report.id
        run.evaluation_id = evaluation.id
        store.save_run(run)

    print(f"  Created server resource: {server.name} ({server.id})", file=sys.stderr)

    # ── 2. Seed notifications (for Alerts page) ─────────────────────
    notification_data = [
        (server.id, "slack", "critical", "Disk at 91%. ENOSPC errors.", "sent", "", 1),
        (server.id, "webhook", "critical", "Disk at 91%. ENOSPC errors.", "sent", "", 1),
        (server.id, "slack", "warning", "Disk usage at 88%. Cleanup recommended.", "sent", "", 10),
        (server.id, "slack", "healthy", "All systems operational after cleanup.", "sent", "", 5),
        (server.id, "webhook", "warning", "High memory usage detected.", "failed", "Connection timeout", 3),
    ]
    for res_id, channel, severity, summary, status, error, days_ago in notification_data:
        store.log_notification(res_id, channel, severity, summary, status, error)
        # Backdate the notification
        notif_ts = (now - timedelta(days=days_ago)).isoformat()
        store._execute(
            "UPDATE notification_log SET created_at = ? WHERE resource_id = ? AND summary = ?",
            (notif_ts, res_id, summary),
        )
        store._commit()
    print("  Created 5 notification log entries", file=sys.stderr)

    # ── 3. Seed metrics (for Metrics page) ─────────────────────────
    import random
    from uuid import uuid4 as _uuid4

    for days_ago in range(30, 0, -1):
        ts = (now - timedelta(days=days_ago)).isoformat()
        report_id = str(_uuid4())
        cpu = 20 + random.uniform(-5, 30) + (40 if days_ago == 1 else 0)
        mem = 45 + random.uniform(-10, 20)
        disk = 72 + (days_ago * 0.6)  # Gradually increasing
        if disk > 91:
            disk = 91
        metrics_batch = [
            {"name": "cpu_percent", "value": round(cpu, 1), "unit": "%"},
            {"name": "memory_percent", "value": round(mem, 1), "unit": "%"},
            {"name": "disk_percent", "value": round(disk, 1), "unit": "%"},
        ]
        store.save_metrics(server.id, report_id, metrics_batch)
        # Backdate metrics
        store._execute(
            "UPDATE metrics SET created_at = ? WHERE report_id = ?",
            (ts, report_id),
        )
        store._commit()
    print("  Created 30 days of metrics (CPU, memory, disk)", file=sys.stderr)

    # ── 4. Seed auth events (for Activity page) ────────────────────
    store.log_auth_event("login_success", email="admin@localhost", ip_address="127.0.0.1")
    store.log_auth_event("login_failure", email="unknown@test.com", ip_address="192.168.1.50")
    store.log_auth_event("user_created", email="viewer@team.com", detail="role=viewer", ip_address="127.0.0.1")
    print("  Created 3 auth audit events", file=sys.stderr)

    # Summary
    resources = store.list_resources()

    print(f"Done! {len(resources)} resource(s) seeded.", file=sys.stderr)
    print("Run `supavision serve` to see the dashboard.", file=sys.stderr)

    _json_out({
        "ok": True,
        "command": "seed-demo",
        "resources": len(resources),
    })
def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    from .web.app import create_app

    app = create_app(db_path=args.db, template_dir=args.templates)
    print(f"Starting Supavision API on {args.host}:{args.port}", file=sys.stderr)
    print(f"OpenAPI docs: http://{args.host}:{args.port}/docs", file=sys.stderr)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
def cmd_api_key_create(args: argparse.Namespace) -> None:
    from .web.auth import generate_api_key

    store = _get_store(args)
    key_id, raw_key, key_hash = generate_api_key()
    store.save_api_key(key_id, key_hash, label=args.label)

    print("\nAPI Key created. Save this — it cannot be retrieved again:\n", file=sys.stderr)
    print(f"  {raw_key}\n", file=sys.stderr)
    _json_out({"ok": True, "command": "api_key_create", "key_id": key_id, "key": raw_key})
def cmd_api_key_list(args: argparse.Namespace) -> None:
    store = _get_store(args)
    keys = store.list_api_keys()
    for k in keys:
        status = "REVOKED" if k["revoked"] else "active"
        print(f"  {k['id']}  {k['label'] or '(no label)'}  {status}  {k['created_at']}", file=sys.stderr)
    _json_out({"ok": True, "command": "api_key_list", "keys": keys})
def cmd_api_key_revoke(args: argparse.Namespace) -> None:
    store = _get_store(args)
    if store.revoke_api_key(args.key_id):
        print(f"API key {args.key_id} revoked.", file=sys.stderr)
        _json_out({"ok": True, "command": "api_key_revoke", "key_id": args.key_id})
    else:
        _error(f"API key {args.key_id} not found or already revoked")
# ── MCP ──────────────────────────────────────────────────────────────
def cmd_mcp_config(args: argparse.Namespace) -> None:
    """Print MCP server configuration for Claude CLI."""
    import json as json_mod
    db_path = os.path.abspath(
        getattr(args, "db", None) or os.environ.get("SUPAVISION_DB_PATH", ".supavision/supavision.db")
    )
    config = {
        "mcpServers": {
            "supavision": {
                "command": sys.executable,
                "args": ["-m", "supavision.mcp"],
                "env": {"SUPAVISION_DB_PATH": db_path},
            }
        }
    }
    print(json_mod.dumps(config, indent=2))
    print(
        "\nCopy the above into your Claude CLI MCP config, or pass it with --mcp-config.",
        file=sys.stderr,
    )
def cmd_mcp_serve(args: argparse.Namespace) -> None:
    """Run MCP server (JSON-RPC over stdin/stdout)."""
    db_path = getattr(args, "db", None) or os.environ.get("SUPAVISION_DB_PATH", "")
    if not db_path:
        db_path = os.path.abspath(".supavision/supavision.db")
    os.environ["SUPAVISION_DB_PATH"] = os.path.abspath(db_path)
    from .mcp import main as mcp_main
    mcp_main()
# ── Auth ──────────────────────────────────────────────────────────────
def cmd_create_admin(args: argparse.Namespace) -> None:
    """Create the first admin user interactively."""
    import getpass

    from .models import User
    from .web.auth import hash_password, validate_password_strength

    store = _get_store(args)

    email = input("Email: ").strip()
    if not email or "@" not in email:
        _error("Invalid email address.")

    # Check if user already exists
    existing = store.get_user_by_email(email)
    if existing:
        _error(f"User with email '{email}' already exists.")

    name = input("Name: ").strip() or email.split("@")[0]

    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")

    if password != confirm:
        _error("Passwords do not match.")

    error = validate_password_strength(password)
    if error:
        _error(error)

    user = User(
        email=email,
        password_hash=hash_password(password),
        name=name,
        role="admin",
    )
    store.create_user(user)
    store.log_auth_event("user_created", user_id=user.id, email=email, detail="admin (CLI)")

    print(f"Admin user created: {email}", file=sys.stderr)
    _json_out({"ok": True, "command": "create-admin", "user_id": user.id, "email": email})
# ── Main ─────────────────────────────────────────────────────────────
def main() -> None:
    from . import __version__

    parser = argparse.ArgumentParser(prog="supavision", description="AI-powered infrastructure monitoring")
    parser.add_argument("--version", action="version", version=f"supavision {__version__}")
    parser.add_argument("--db", default=DB_PATH_DEFAULT, help="Database path")
    parser.add_argument("--templates", default=TEMPLATE_DIR_DEFAULT, help="Templates directory")
    parser.add_argument(
        "--format", default="auto", choices=["auto", "json", "table"],
        help="Output format: auto (table on TTY, JSON when piped), json, or table",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # resource add
    p = sub.add_parser("resource-add", help="Register a new resource to monitor")
    p.add_argument("name", help="Human-readable name for the resource")
    p.add_argument("--type", required=True, help="Resource type: server, aws_account, database, github_org")
    p.add_argument("--parent", default="", help="Parent resource ID for hierarchical grouping")
    p.add_argument("--config", nargs="*", default=[], help="Key=value pairs (e.g., ssh_host=10.0.1.5)")
    p.set_defaults(func=cmd_resource_add)

    # resource list
    sub.add_parser("resource-list", help="List all registered resources").set_defaults(func=cmd_resource_list)

    # resource show
    p = sub.add_parser("resource-show", help="Show details for a specific resource")
    p.add_argument("resource_id", help="Resource ID")
    p.set_defaults(func=cmd_resource_show)

    # resource set-schedule
    p = sub.add_parser("set-schedule", help="Set discovery and health-check schedules for a resource")
    p.add_argument("resource_id", help="Resource ID")
    p.add_argument(
        "--discovery", default="",
        help="Cron expression for discovery runs (e.g., '0 */6 * * *' for every 6 hours)",
    )
    p.add_argument(
        "--health-check", default="",
        help="Cron expression for health checks (e.g., '0 */6 * * *' for every 6 hours)",
    )
    p.set_defaults(func=cmd_resource_set_schedule)

    # resource add-credential
    p = sub.add_parser("add-credential", help="Attach a credential to a resource")
    p.add_argument("resource_id", help="Resource ID")
    p.add_argument("--name", required=True, help="Credential name (e.g., ssh_key, api_token)")
    p.add_argument("--env-var", required=True, help="Environment variable that holds the credential value")
    p.set_defaults(func=cmd_resource_add_credential)

    # run discovery
    p = sub.add_parser("run-discovery", help="Run a discovery scan on a resource")
    p.add_argument("resource_id", help="Resource ID")
    p.set_defaults(func=cmd_run_discovery)

    # run health-check
    p = sub.add_parser("run-health-check", help="Run a health check on a resource")
    p.add_argument("resource_id", help="Resource ID")
    p.set_defaults(func=cmd_run_health_check)

    # run status
    p = sub.add_parser("run-status", help="Show the status of a specific run")
    p.add_argument("run_id", help="Run ID")
    p.set_defaults(func=cmd_run_status)

    # report show
    p = sub.add_parser("report-show", help="Display a full report")
    p.add_argument("report_id", help="Report ID")
    p.set_defaults(func=cmd_report_show)

    # report list
    p = sub.add_parser("report-list", help="List recent reports for a resource")
    p.add_argument("resource_id", help="Resource ID")
    p.add_argument("--type", default="health_check", help="Run type filter: health_check or discovery")
    p.add_argument("--limit", type=int, default=10, help="Maximum number of reports to return")
    p.set_defaults(func=cmd_report_list)

    # context show
    p = sub.add_parser("context-show", help="Show the latest system context for a resource")
    p.add_argument("resource_id", help="Resource ID")
    p.set_defaults(func=cmd_context_show)

    # checklist show
    p = sub.add_parser("checklist-show", help="Show the monitoring checklist for a resource")
    p.add_argument("resource_id", help="Resource ID")
    p.set_defaults(func=cmd_checklist_show)

    # checklist add
    p = sub.add_parser("checklist-add", help="Add a custom monitoring request to a resource")
    p.add_argument("resource_id", help="Resource ID")
    p.add_argument("request", help="Monitoring request to add to the checklist")
    p.set_defaults(func=cmd_checklist_add)

    # template list
    sub.add_parser("template-list", help="List available prompt templates").set_defaults(func=cmd_template_list)

    # run-scheduler
    sub.add_parser("run-scheduler", help="Start the cron-based scheduler daemon").set_defaults(func=cmd_run_scheduler)

    # doctor
    sub.add_parser("doctor", help="Check system dependencies and configuration").set_defaults(func=cmd_doctor)

    # setup
    sub.add_parser(
        "setup", help="Guided first-run setup: check prerequisites and authenticate Claude CLI"
    ).set_defaults(func=cmd_setup)

    # notify-test
    p = sub.add_parser("notify-test", help="Send a test notification for a resource")
    p.add_argument("resource_id", help="Resource ID")
    p.add_argument(
        "--severity", choices=["healthy", "warning", "critical"], default="warning",
        help="Severity level for the test notification",
    )
    p.set_defaults(func=cmd_notify_test)

    # notify-configure
    p = sub.add_parser("notify-configure", help="Configure notification channels for a resource")
    p.add_argument("resource_id", help="Resource ID")
    p.add_argument("--slack-webhook", default="", help="Slack incoming webhook URL")
    p.add_argument("--webhook-url", default="", help="Generic webhook URL for notifications")
    p.add_argument("--clear", action="store_true", help="Remove all notification config")
    p.set_defaults(func=cmd_notify_configure)

    # context-diff
    p = sub.add_parser("context-diff", help="Show differences between the two latest context versions")
    p.add_argument("resource_id", help="Resource ID")
    p.set_defaults(func=cmd_context_diff)

    # seed-demo
    p = sub.add_parser("seed-demo", help="Populate database with sample data for demo")
    p.add_argument("--force", action="store_true", help="Add demo data even if database is not empty")
    p.set_defaults(func=cmd_seed_demo)

    # purge
    p = sub.add_parser("purge", help="Delete old reports and runs")
    p.add_argument("--days", type=int, default=90, help="Delete data older than N days (default: 90)")
    p.add_argument("--dry-run", action="store_true", help="Show what would be deleted without deleting")
    p.set_defaults(func=cmd_purge)

    # serve (API server)
    p = sub.add_parser("serve", help="Start the REST API server")
    p.add_argument("--port", type=int, default=8080, help="Port to listen on")
    p.add_argument("--host", default="0.0.0.0", help="Host address to bind to")
    p.set_defaults(func=cmd_serve)

    # api-key-create
    p = sub.add_parser("api-key-create", help="Generate a new API key")
    p.add_argument("--label", default="", help="Label for the key")
    p.set_defaults(func=cmd_api_key_create)

    # api-key-list
    sub.add_parser("api-key-list", help="List API keys").set_defaults(func=cmd_api_key_list)

    # api-key-revoke
    p = sub.add_parser("api-key-revoke", help="Revoke an API key")
    p.add_argument("key_id", help="API key ID to revoke")
    p.set_defaults(func=cmd_api_key_revoke)
    # ── MCP commands ─────────────────────────────────────────────────

    # mcp-config
    sub.add_parser("mcp-config", help="Print MCP server config for Claude CLI").set_defaults(func=cmd_mcp_config)

    # mcp-serve
    sub.add_parser("mcp-serve", help="Run the MCP server (stdin/stdout JSON-RPC)").set_defaults(func=cmd_mcp_serve)

    # ── Auth commands ────────────────────────────────────────────────

    # create-admin
    sub.add_parser("create-admin", help="Create an admin user (interactive)").set_defaults(func=cmd_create_admin)

    args = parser.parse_args()
    global _FORMAT  # noqa: PLW0603
    _FORMAT = getattr(args, "format", "json")
    try:
        args.func(args)
    except SystemExit:
        raise
    except Exception as e:
        _error(str(e))
if __name__ == "__main__":
    main()
