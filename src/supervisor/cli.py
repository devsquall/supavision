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

    # ANTHROPIC_API_KEY
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    has_key = bool(key and not key.startswith("sk-ant-your"))
    checks.append({"check": "anthropic_api_key", "ok": has_key, "detail": "set" if has_key else "missing"})

    # Template directory
    tdir = Path(getattr(args, "templates", TEMPLATE_DIR_DEFAULT))
    has_templates = tdir.exists()
    checks.append({"check": "template_dir", "ok": has_templates, "detail": str(tdir.resolve())})

    # SQLite writable
    db_dir = Path(DB_PATH_DEFAULT).parent
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

    args = parser.parse_args()
    try:
        args.func(args)
    except SystemExit:
        raise
    except Exception as e:
        _error(str(e))


if __name__ == "__main__":
    main()
