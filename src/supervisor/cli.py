"""CLI entry point for Supervisor.

JSON output to stdout. Human-readable messages to stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


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

DB_PATH_DEFAULT = ".supervisor/supervisor.db"


def _json_out(data: dict) -> None:
    json.dump(data, sys.stdout, default=str, ensure_ascii=False)
    sys.stdout.write("\n")


def _error(msg: str) -> None:
    _json_out({"ok": False, "error": msg})
    sys.exit(1)


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
        _error(f"Resource {args.resource_id} not found")
    _json_out({"ok": True, "command": "resource_show", "resource": resource.model_dump(mode="json")})


def cmd_resource_set_schedule(args: argparse.Namespace) -> None:
    store = _get_store(args)
    resource = store.get_resource(args.resource_id)
    if not resource:
        _error(f"Resource {args.resource_id} not found")

    if args.discovery:
        resource.discovery_schedule = Schedule(cron=args.discovery)
    if args.health_check:
        resource.health_check_schedule = Schedule(cron=args.health_check)

    store.save_resource(resource)
    _json_out({"ok": True, "command": "set_schedule", "resource_id": resource.id})


def cmd_resource_add_credential(args: argparse.Namespace) -> None:
    store = _get_store(args)
    resource = store.get_resource(args.resource_id)
    if not resource:
        _error(f"Resource {args.resource_id} not found")

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
        _error(f"Run {args.run_id} not found")
    _json_out({"ok": True, "command": "run_status", "run": run.model_dump(mode="json")})


# ── Report commands ──────────────────────────────────────────────────


def cmd_report_show(args: argparse.Namespace) -> None:
    store = _get_store(args)
    report = store.get_report(args.report_id)
    if not report:
        _error(f"Report {args.report_id} not found")
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
        _error(f"Resource {args.resource_id} not found")

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

    # OPENROUTER_API_KEY
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
    checks.append({"check": "croniter", "ok": has_croniter, "detail": "importable" if has_croniter else "not installed"})

    all_ok = all(c["ok"] for c in checks)
    for c in checks:
        icon = "OK" if c["ok"] else "FAIL"
        print(f"  [{icon}] {c['check']}: {c['detail']}", file=sys.stderr)

    _json_out({"ok": all_ok, "command": "doctor", "checks": checks})
    if not all_ok:
        sys.exit(1)


# ── Notification commands ────────────────────────────────────────────


def cmd_notify_test(args: argparse.Namespace) -> None:
    store = _get_store(args)
    resource = store.get_resource(args.resource_id)
    if not resource:
        _error(f"Resource {args.resource_id} not found")

    from .models import Evaluation, Report, RunType, Severity
    from .notifications import send_alert

    test_report = Report(
        resource_id=resource.id,
        run_type=RunType.HEALTH_CHECK,
        content="This is a test notification from Supervisor. If you see this, notifications are working.",
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
        _error(f"Resource {args.resource_id} not found")

    updated = []

    if args.slack_webhook:
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
        print(f"Dry run: would delete {reports_count} reports and {runs_count} runs older than {days} days", file=sys.stderr)
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


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    from .web.app import create_app

    app = create_app(db_path=args.db, template_dir=args.templates)
    print(f"Starting Supervisor API on {args.host}:{args.port}", file=sys.stderr)
    print(f"OpenAPI docs: http://{args.host}:{args.port}/docs", file=sys.stderr)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


def cmd_api_key_create(args: argparse.Namespace) -> None:
    from .web.auth import generate_api_key

    store = _get_store(args)
    key_id, raw_key, key_hash = generate_api_key()
    store.save_api_key(key_id, key_hash, label=args.label)

    print(f"\nAPI Key created. Save this — it cannot be retrieved again:\n", file=sys.stderr)
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


# ── Main ─────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(prog="supervisor", description="AI-powered infrastructure monitoring")
    parser.add_argument("--db", default=DB_PATH_DEFAULT, help="Database path")
    parser.add_argument("--templates", default=TEMPLATE_DIR_DEFAULT, help="Templates directory")
    sub = parser.add_subparsers(dest="command", required=True)

    # resource add
    p = sub.add_parser("resource-add")
    p.add_argument("name")
    p.add_argument("--type", required=True)
    p.add_argument("--parent", default="")
    p.add_argument("--config", nargs="*", default=[])
    p.set_defaults(func=cmd_resource_add)

    # resource list
    sub.add_parser("resource-list").set_defaults(func=cmd_resource_list)

    # resource show
    p = sub.add_parser("resource-show")
    p.add_argument("resource_id")
    p.set_defaults(func=cmd_resource_show)

    # resource set-schedule
    p = sub.add_parser("set-schedule")
    p.add_argument("resource_id")
    p.add_argument("--discovery", default="")
    p.add_argument("--health-check", default="")
    p.set_defaults(func=cmd_resource_set_schedule)

    # resource add-credential
    p = sub.add_parser("add-credential")
    p.add_argument("resource_id")
    p.add_argument("--name", required=True)
    p.add_argument("--env-var", required=True)
    p.set_defaults(func=cmd_resource_add_credential)

    # run discovery
    p = sub.add_parser("run-discovery")
    p.add_argument("resource_id")
    p.set_defaults(func=cmd_run_discovery)

    # run health-check
    p = sub.add_parser("run-health-check")
    p.add_argument("resource_id")
    p.set_defaults(func=cmd_run_health_check)

    # run status
    p = sub.add_parser("run-status")
    p.add_argument("run_id")
    p.set_defaults(func=cmd_run_status)

    # report show
    p = sub.add_parser("report-show")
    p.add_argument("report_id")
    p.set_defaults(func=cmd_report_show)

    # report list
    p = sub.add_parser("report-list")
    p.add_argument("resource_id")
    p.add_argument("--type", default="health_check")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_report_list)

    # context show
    p = sub.add_parser("context-show")
    p.add_argument("resource_id")
    p.set_defaults(func=cmd_context_show)

    # checklist show
    p = sub.add_parser("checklist-show")
    p.add_argument("resource_id")
    p.set_defaults(func=cmd_checklist_show)

    # checklist add
    p = sub.add_parser("checklist-add")
    p.add_argument("resource_id")
    p.add_argument("request")
    p.set_defaults(func=cmd_checklist_add)

    # template list
    sub.add_parser("template-list").set_defaults(func=cmd_template_list)

    # run-scheduler
    sub.add_parser("run-scheduler").set_defaults(func=cmd_run_scheduler)

    # doctor
    sub.add_parser("doctor").set_defaults(func=cmd_doctor)

    # notify-test
    p = sub.add_parser("notify-test")
    p.add_argument("resource_id")
    p.add_argument("--severity", choices=["healthy", "warning", "critical"], default="warning")
    p.set_defaults(func=cmd_notify_test)

    # notify-configure
    p = sub.add_parser("notify-configure")
    p.add_argument("resource_id")
    p.add_argument("--slack-webhook", default="")
    p.add_argument("--webhook-url", default="")
    p.add_argument("--clear", action="store_true", help="Remove all notification config")
    p.set_defaults(func=cmd_notify_configure)

    # context-diff
    p = sub.add_parser("context-diff")
    p.add_argument("resource_id")
    p.set_defaults(func=cmd_context_diff)

    # purge
    p = sub.add_parser("purge", help="Delete old reports and runs")
    p.add_argument("--days", type=int, default=90, help="Delete data older than N days (default: 90)")
    p.add_argument("--dry-run", action="store_true", help="Show what would be deleted without deleting")
    p.set_defaults(func=cmd_purge)

    # serve (API server)
    p = sub.add_parser("serve", help="Start the REST API server")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--host", default="0.0.0.0")
    p.set_defaults(func=cmd_serve)

    # api-key-create
    p = sub.add_parser("api-key-create", help="Generate a new API key")
    p.add_argument("--label", default="", help="Label for the key")
    p.set_defaults(func=cmd_api_key_create)

    # api-key-list
    sub.add_parser("api-key-list", help="List API keys").set_defaults(func=cmd_api_key_list)

    # api-key-revoke
    p = sub.add_parser("api-key-revoke", help="Revoke an API key")
    p.add_argument("key_id")
    p.set_defaults(func=cmd_api_key_revoke)

    args = parser.parse_args()
    try:
        args.func(args)
    except SystemExit:
        raise
    except Exception as e:
        _error(str(e))


if __name__ == "__main__":
    main()
