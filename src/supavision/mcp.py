"""MCP server for Supavision — exposes read-only tools for Claude CLI.

Speaks JSON-RPC over stdin/stdout (MCP stdio protocol).
Run: SUPAVISION_DB_PATH=/path/to/db python -m supavision.mcp

Lane 1 (Health) tools:
  supavision_list_resources     — List all resources with current severity
  supavision_get_latest_report  — Latest health check report for a resource
  supavision_get_baseline       — Discovery baseline (system context + checklist)
  supavision_get_run_history    — Recent runs with status and duration

"""

from __future__ import annotations

import json
import os
import sqlite3
import sys

from . import __version__

# ── Tool definitions ──────────────────────────────────────────────

TOOLS = [
    {
        "name": "supavision_list_resources",
        "description": (
            "List all monitored resources with their current severity status. "
            "Returns id, name, resource_type, and latest severity."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "supavision_get_latest_report",
        "description": (
            "Get the most recent health check report for a resource. "
            "Returns report content, severity, and summary."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "resource_id": {
                    "type": "string",
                    "description": "The resource ID to fetch the report for.",
                },
            },
            "required": ["resource_id"],
        },
    },
    {
        "name": "supavision_get_baseline",
        "description": (
            "Get the discovery baseline for a resource. "
            "Returns system context and checklist from the most recent discovery."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "resource_id": {
                    "type": "string",
                    "description": "The resource ID to fetch the baseline for.",
                },
            },
            "required": ["resource_id"],
        },
    },
    {
        "name": "supavision_get_run_history",
        "description": (
            "Get recent run history for a resource. "
            "Returns run type, status, severity, timestamps, and duration."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "resource_id": {
                    "type": "string",
                    "description": "The resource ID to fetch run history for.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of runs to return (default 5, max 20).",
                },
            },
            "required": ["resource_id"],
        },
    },
    {
        "name": "supavision_get_metrics",
        "description": (
            "Get the latest structured metrics for a resource. "
            "Returns metric names and values (e.g., cpu_percent, disk_percent, monthly_cost_usd)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "resource_id": {"type": "string", "description": "The resource ID."},
            },
            "required": ["resource_id"],
        },
    },
    {
        "name": "supavision_get_metrics_trend",
        "description": (
            "Get time-series history for a specific metric on a resource. "
            "Returns values over time for trending and capacity planning."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "resource_id": {"type": "string", "description": "The resource ID."},
                "metric_name": {"type": "string", "description": "Metric name (e.g., cpu_percent, disk_percent)."},
                "days": {"type": "integer", "description": "Number of days of history (default 30, max 90)."},
            },
            "required": ["resource_id", "metric_name"],
        },
    },
    # Workstream E5: severity trend tool
    {
        "name": "supavision_get_severity_trend",
        "description": (
            "Get the severity history for a resource — the last N evaluations with "
            "severity, summary, and timestamp. Use this to answer 'how has this "
            "resource been trending?' or 'how long has it been critical?'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "resource_id": {"type": "string", "description": "The resource ID."},
                "limit": {
                    "type": "integer",
                    "description": "Number of recent evaluations to return (default 10, max 50).",
                },
            },
            "required": ["resource_id"],
        },
    },
]

# ── Tool handlers ─────────────────────────────────────────────────


def _handle_list_resources(conn: sqlite3.Connection, _args: dict) -> str:
    rows = conn.execute(
        "SELECT id, data FROM resources ORDER BY created_at"
    ).fetchall()

    resources = []
    for row in rows:
        data = json.loads(row[1])
        resource_id = row[0]

        # Get latest evaluation severity
        ev = conn.execute(
            "SELECT data FROM evaluations WHERE resource_id = ? ORDER BY created_at DESC LIMIT 1",
            (resource_id,),
        ).fetchone()
        severity = None
        if ev:
            ev_data = json.loads(ev[0])
            severity = ev_data.get("severity")

        resources.append({
            "id": resource_id,
            "name": data.get("name"),
            "resource_type": data.get("resource_type"),
            "severity": severity,
            "enabled": data.get("enabled", True),
        })

    return json.dumps(resources, indent=2)


def _handle_get_latest_report(conn: sqlite3.Connection, args: dict) -> str:
    resource_id = args.get("resource_id")
    if not resource_id:
        return json.dumps({"error": "resource_id is required"})

    report = conn.execute(
        "SELECT data FROM reports WHERE resource_id = ? ORDER BY created_at DESC LIMIT 1",
        (resource_id,),
    ).fetchone()
    if not report:
        return json.dumps({"error": "No reports found for this resource."})

    report_data = json.loads(report[0])

    # Get matching evaluation
    ev = conn.execute(
        "SELECT data FROM evaluations WHERE resource_id = ? ORDER BY created_at DESC LIMIT 1",
        (resource_id,),
    ).fetchone()
    eval_data = json.loads(ev[0]) if ev else None

    return json.dumps({
        "report_content": report_data.get("content", ""),
        "run_type": report_data.get("run_type"),
        "created_at": report_data.get("created_at"),
        "severity": eval_data.get("severity") if eval_data else None,
        "summary": eval_data.get("summary") if eval_data else None,
    }, indent=2)


def _handle_get_baseline(conn: sqlite3.Connection, args: dict) -> str:
    resource_id = args.get("resource_id")
    if not resource_id:
        return json.dumps({"error": "resource_id is required"})

    ctx = conn.execute(
        "SELECT data FROM system_contexts WHERE resource_id = ? ORDER BY created_at DESC LIMIT 1",
        (resource_id,),
    ).fetchone()
    if not ctx:
        return json.dumps({"error": "No baseline found. Run discovery first."})

    ctx_data = json.loads(ctx[0])

    checklist = conn.execute(
        "SELECT data FROM checklists WHERE resource_id = ? ORDER BY created_at DESC LIMIT 1",
        (resource_id,),
    ).fetchone()
    checklist_data = json.loads(checklist[0]) if checklist else None

    return json.dumps({
        "system_context": ctx_data.get("content", ""),
        "version": ctx_data.get("version"),
        "checklist_items": [
            item.get("description", "")
            for item in (checklist_data.get("items", []) if checklist_data else [])
        ],
    }, indent=2)


def _handle_get_run_history(conn: sqlite3.Connection, args: dict) -> str:
    resource_id = args.get("resource_id")
    if not resource_id:
        return json.dumps({"error": "resource_id is required"})

    limit = min(max(int(args.get("limit", 5)), 1), 20)

    rows = conn.execute(
        "SELECT data FROM runs WHERE resource_id = ? ORDER BY created_at DESC LIMIT ?",
        (resource_id, limit),
    ).fetchall()

    if not rows:
        return json.dumps({"error": "No run history found."})

    runs = []
    for row in rows:
        data = json.loads(row[0])
        runs.append({
            "run_type": data.get("run_type"),
            "status": data.get("status"),
            "started_at": data.get("started_at"),
            "completed_at": data.get("completed_at"),
            "error": data.get("error"),
        })

    return json.dumps(runs, indent=2)


def _handle_get_metrics(conn: sqlite3.Connection, args: dict) -> str:
    resource_id = args.get("resource_id")
    if not resource_id:
        return json.dumps({"error": "resource_id is required"})

    # Get the most recent value for each metric name
    rows = conn.execute(
        """SELECT name, value, unit, created_at FROM metrics
           WHERE resource_id = ? AND created_at = (
               SELECT MAX(m2.created_at) FROM metrics m2
               WHERE m2.resource_id = metrics.resource_id AND m2.name = metrics.name
           )""",
        (resource_id,),
    ).fetchall()

    if not rows:
        return json.dumps({"error": "No metrics found. Run a health check first."})

    metrics = {row[0]: {"value": row[1], "unit": row[2], "as_of": row[3]} for row in rows}
    return json.dumps(metrics, indent=2)


def _handle_get_metrics_trend(conn: sqlite3.Connection, args: dict) -> str:
    resource_id = args.get("resource_id")
    metric_name = args.get("metric_name")
    if not resource_id or not metric_name:
        return json.dumps({"error": "resource_id and metric_name are required"})

    days = min(max(int(args.get("days", 30)), 1), 90)
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    rows = conn.execute(
        "SELECT value, created_at FROM metrics "
        "WHERE resource_id = ? AND name = ? AND created_at >= ? ORDER BY created_at",
        (resource_id, metric_name, cutoff),
    ).fetchall()

    if not rows:
        return json.dumps({"error": f"No data for metric '{metric_name}' in the last {days} days."})

    data_points = [{"value": row[0], "timestamp": row[1]} for row in rows]
    return json.dumps({"metric": metric_name, "days": days, "points": len(data_points), "data": data_points}, indent=2)


def _handle_get_severity_trend(conn: sqlite3.Connection, args: dict) -> str:
    """Return the last N evaluations for a resource with severity + timestamp (E5)."""
    resource_id = args.get("resource_id")
    if not resource_id:
        return json.dumps({"error": "resource_id is required"})

    limit = min(max(int(args.get("limit", 10)), 1), 50)
    rows = conn.execute(
        "SELECT data FROM evaluations WHERE resource_id = ? ORDER BY created_at DESC LIMIT ?",
        (resource_id, limit),
    ).fetchall()

    if not rows:
        return json.dumps({"error": "No evaluations found for this resource."})

    entries = []
    for row in rows:
        ev = json.loads(row[0])
        entries.append({
            "severity": ev.get("severity"),
            "summary": (ev.get("summary") or "")[:200],
            "timestamp": ev.get("created_at"),
            "report_id": ev.get("report_id"),
            "should_alert": ev.get("should_alert", False),
        })
    return json.dumps({"resource_id": resource_id, "count": len(entries), "evaluations": entries}, indent=2)


_HANDLERS = {

    "supavision_list_resources": _handle_list_resources,
    "supavision_get_latest_report": _handle_get_latest_report,
    "supavision_get_baseline": _handle_get_baseline,
    "supavision_get_run_history": _handle_get_run_history,

    "supavision_get_metrics": _handle_get_metrics,
    "supavision_get_metrics_trend": _handle_get_metrics_trend,
    "supavision_get_severity_trend": _handle_get_severity_trend,
}

# ── JSON-RPC protocol handler ────────────────────────────────────


def handle_jsonrpc(conn: sqlite3.Connection, line: str) -> dict | None:
    """Handle a single JSON-RPC message. Returns response dict or None for notifications."""
    msg = json.loads(line)
    method = msg.get("method")
    msg_id = msg.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "supavision", "version": __version__},
            },
        }

    if method == "notifications/initialized":
        return None  # Notification — no response

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "tools": [
                    {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
                    for t in TOOLS
                ],
            },
        }

    if method == "tools/call":
        tool_name = msg.get("params", {}).get("name", "")
        tool_args = msg.get("params", {}).get("arguments", {})

        handler = _HANDLERS.get(tool_name)
        if not handler:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
            }

        try:
            result = handler(conn, tool_args)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": result}]},
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32603, "message": f"Tool error: {e}"},
            }

    # Unknown method
    if msg_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Unknown method: {method}"},
        }
    return None


# ── Entry point ───────────────────────────────────────────────────


def main():
    db_path = os.environ.get("SUPAVISION_DB_PATH")
    if not db_path:
        sys.stderr.write(
            "Error: SUPAVISION_DB_PATH environment variable not set.\n"
            "Run: supavision mcp-config\n"
            "to generate the correct MCP configuration.\n"
        )
        sys.exit(1)

    if not os.path.exists(db_path):
        sys.stderr.write(f"Error: Database not found at {db_path}\n")
        sys.exit(1)

    # Open DB in read-only mode
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            response = handle_jsonrpc(conn, line)
            if response:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
        except json.JSONDecodeError:
            pass  # Silently ignore malformed JSON
        except Exception as e:
            sys.stderr.write(f"[mcp-supavision] Error: {e}\n")

    conn.close()


if __name__ == "__main__":
    main()
