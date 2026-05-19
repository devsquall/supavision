# Architecture

Supavision monitors infrastructure resources (servers, AWS accounts, databases, GitHub orgs). Each resource goes through a simple, single-pipeline lifecycle: **Run → Report → Evaluation → Alert**.

## The Pipeline

```
                  Resource
                     │
                     ▼
                    Run            (Discovery or Health Check)
                     │
                     ▼
                  Report           (Aggregate narrative + optional structured payload)
                     │
                     ▼
                Evaluation         (Severity: healthy / warning / critical)
                     │
                     ▼
                   Alert           (Slack / webhook — only on issue set-diffs)
```

## Rules

- **Run** = a single invocation of Claude against a resource (discovery or health check). Tracks status, token usage, cost, duration.
- **Report** = the aggregate narrative output of a Run. One Report per Run. May include a structured `ReportPayload` (typed issues + metrics) for resources that opted into the `submit_report` tool.
- **Evaluation** = severity assessment of a Report. Answers "how healthy is this resource right now?" Uses `Severity` (healthy / warning / critical). Stored in the `evaluations` table.
- **Alert** = outbound notification (Slack or generic webhook). Triggered on severity transitions or new issues surfaced by the issue set-diff vs the previous run.

## Reports: Prose + Optional Structured Payload

Every Report has a `content` field (markdown narrative from Claude). Some resource types also produce a structured `payload` via the `submit_report` tool at the end of a health check. The payload contains:

- **Issues** — typed objects with severity, category, human description, affected entity
- **Metrics** — schema-validated numeric measurements (CPU, disk %, cost, count, etc.)
- **Actions taken** — agent-initiated remediations or diagnostics

When a payload is present, downstream consumers (evaluator, alert dispatcher, dashboard) use the structured data; otherwise they fall back to parsing the prose narrative (legacy path). The dual-mode evaluator (`evaluator.py`) handles both transparently.

### Anti-pattern

**Do not add lifecycle stages to Reports.** Reports are snapshots, not workflows. "High CPU on host X" is a Report with severity=warning and an Issue inside its payload, not a long-lived WorkItem. The history comes from the sequence of Reports over time, not from per-issue lifecycle columns.

## Models Package

```
models/
├── core.py    ← Shared: Resource, Run, RunStatus, RunType, Credential,
│                 Schedule, User, Session
└── health.py  ← Report, ReportPayload, IssueSeverity, Evaluation,
                 Severity, SystemContext, Checklist, Metric, RunMetadata,
                 IssueDiff, compute_issue_diff
```

| Domain | File | Imports |
|--------|------|---------|
| Engine / evaluator / executor / tools | `engine.py`, `evaluator.py`, `executor.py`, `tools.py`, `discovery_diff.py` | `models.core` + `models.health` |
| Storage | `db.py` | All models (it persists everything) |
| Web | `web/` | All models |
| CLI | `cli.py` | All models |
| Scheduler | `scheduler.py` | `models.core` |
| MCP server | `mcp.py` | `models.health` (read-only) |

Enforced by `tests/test_lane_boundary.py` (AST-based import verification — the test ensures no module accidentally introduces a dependency on a removed submodule).

## Issue Set-Diffs Across Runs

Between consecutive Runs of the same resource, Supavision computes an `IssueDiff` (`compute_issue_diff` in `models.health`):

- **New** — issues that appeared in this run but not the previous one
- **Persisting** — issues present in both runs
- **Resolved** — issues from the previous run that are no longer present

This drives two behaviors:

1. **Smart alerts**: Slack notifications fire on *new* and *persisting-escalated* issues only, not on the full issue list every time. Prevents alert fatigue.
2. **Severity streak**: dashboard action items show "critical for N consecutive runs" so users can distinguish a one-off blip from a sustained incident.

## Tech Stack

- **Python 3.12+**
- **FastAPI** — async web framework
- **HTMX** — dashboard interactivity without a JS framework
- **xterm.js** — live terminal streaming of Claude's tool calls
- **SQLite (WAL mode)** — single-file DB, safe for concurrent reads
- **Claude Code CLI** — primary backend (zero-cost, uses your Claude subscription). OpenRouter API backend also supported for per-token billing.

## Adding a Resource Type

1. Create `src/supavision/prompt_templates/{type_name}/discovery.md` + `health_check.md` using `{{placeholder}}` syntax.
2. Add an entry to `RESOURCE_TYPES` in `resource_types.py` with `label`, `icon`, `description`, `config_schema`, `connection_test` (optional).
3. If the type has distinct metrics, add a schema to `metric_schemas.py`.
4. If the type should emit structured `ReportPayload`, add it to `supports_structured_payload()` in `report_vocab.py`.

No backend code changes required for most types — the engine, dashboard, CLI, and MCP surface all read from the generic Resource/Run/Report/Evaluation models.

## Security Model

See [SECURITY.md](SECURITY.md). Key points:

- Session-based auth; RBAC (admin / viewer) enforced server-side on every mutating route
- Agent tool allowlist in `tools.py` — no arbitrary shell execution
- Credentials stored as env-var references, not raw secrets
- SSRF protection on webhook dispatch, CSRF on mutations, scrypt password hashing

## Scale planning (P2 #18 — deferred, scope-only)

Supavision's SQLite store comfortably handles low-thousand-resource deployments. When a deployment outgrows it, here is the path we'll take — none of this is implemented today, and the specific thresholds will be set by real-world benchmarks rather than speculation.

### Indexed projections

The current `evaluations` and `reports` tables store the structured payload as a JSON blob inside `data`. Queries that filter or aggregate on payload fields (`payload.issues[*].severity`, `payload.metrics[<name>]`) currently load every row and filter in Python. Two projection tables would cover this:

- `issues_projection (issue_id, resource_id, run_id, report_id, severity, tag1, scope, status, opened_at, resolved_at)` — populated on `save_report`; indexed by `(resource_id, status)` and `(severity, opened_at DESC)`. Supports "show open critical issues across the fleet" without a table scan.
- `metrics_projection (resource_id, run_id, metric_name, metric_value, observed_at)` — populated on `save_report`; indexed by `(resource_id, metric_name, observed_at)`. Supports metric trend charts without payload deserialization.

The `Report.payload` JSON blob remains the source of truth; projections are derived and recomputable.

### Durable job state

Today, `POST /api/v1/runs` schedules a background task in the process. If the process crashes mid-run, the PENDING/RUNNING row is orphaned. A small `job_queue` table (status, attempt_count, payload, claimed_by, claimed_at) plus a worker loop would let the scheduler recover after crashes and would unlock horizontal scaling. The existing `create_pending_run_if_no_active` atomic helper is the model — extend it with a leasing primitive.

### Configurable concurrency

`scheduler.py` currently uses `asyncio.Semaphore(3)`. Expose this via config (env var + dashboard) and emit metrics for queue depth and per-resource queueing.

### Persistent rate limiting

`notifications.py` `_DedupCache` is in-memory and resets on restart. The persisted dedup-key shortcut on `resource.config["_last_alert_key"]` covers the single-resource case but not global per-channel rate limits. A small `rate_limits (key, window_start, count)` table would persist Slack/Teams/PagerDuty per-channel throttling across restarts.

### Postgres migration shape

When SQLite write contention becomes the bottleneck, move to Postgres. The two invariants that need attention:

- `create_pending_run_if_no_active`: replace `BEGIN IMMEDIATE` with either `SELECT ... FOR UPDATE` on a sentinel row or a unique partial index `(resource_id) WHERE status IN ('pending', 'running')`. Either preserves the "one in-flight run per resource" guarantee under multi-writer.
- Everything serialized as JSON blobs in `data` columns: keep as `JSONB`. The lookups by indexed scalar columns (`resource_id`, `status`, etc.) are already shape-compatible with Postgres.

No engine code should need to change — Store is a thin abstraction and most callers read whole models, not specific columns.
