# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest tests/ -v
.venv/bin/pytest tests/test_evaluator.py -v          # single test file
.venv/bin/pytest tests/test_scanner.py -k "test_sql" # single test by name
.venv/bin/ruff check src/ tests/
.venv/bin/ruff format --check src/ tests/
.venv/bin/uvicorn supavision.web.app:create_app --factory --port 8080
```

Set `SUPAVISION_COOKIE_SECURE=false` in `.env` for local HTTP dev (no HTTPS).

## Architecture

Two-lane design. See `ARCHITECTURE.md` for the full rationale and anti-patterns.

**Lane 1 (Health):** Resource → Run → Report → Evaluation → Alert
  Infrastructure monitoring. CLI: `engine.py` → Claude CLI subprocess.

**Lane 2 (Work):** Resource → WorkItem (Finding | ManualTask) → AgentJob
  Codebase improvement. CLI: `codebase_engine.py` → scanner + agent_runner.

Both lanes share: Resource, Run, Store (SQLite WAL), Scheduler, Notifications, MCP.

### Models package
```
models/
├── core.py    — Shared: Resource, Run, Credential, Schedule
├── health.py  — Lane 1: Report, Evaluation, Severity, SystemContext, Checklist
└── work.py    — Lane 2: Finding, ManualTask, AgentJob, FindingStage, BlocklistEntry
```

Import rules enforced by `tests/test_lane_boundary.py` (AST-based):
- Infrastructure domain (`engine.py`, `evaluator.py`, `executor.py`, `tools.py`, `discovery_diff.py`) imports `models.core` + `models.health` only
- Codebase domain (`scanner.py`, `blocklist.py`, `agent_runner.py`, `code_evaluator.py`) imports `models.core` + `models.work` only
- Shared code (`db.py`, `web/`, `cli.py`, `scheduler.py`, `mcp.py`) may import all models

## Key files

### Infrastructure domain (Lane 1)
- `engine.py` — Core run logic, Claude CLI integration, SSE output streaming
- `evaluator.py` — Rule-based severity assessment (zero LLM cost)
- `executor.py` — SSH command execution with multiplexing
- `tools.py` — Scoped read-only tools for infrastructure investigation
- `discovery_diff.py` — Drift detection between baselines

### Codebase domain (Lane 2)
- `codebase_engine.py` — Orchestrates scan, evaluate, implement, scout
- `scanner.py` — Regex security patterns across 9 languages (zero cost)
- `blocklist.py` — False-positive learning from rejection feedback
- `agent_runner.py` — Background thread job executor (Claude Code subprocess)
- `code_evaluator.py` — Evaluation prompt generation
- `prompt_builder.py` — Implementation prompt generation

### Shared
- `mcp.py` — MCP server (JSON-RPC over stdio)
- `scheduler.py` — Cron-based job scheduling with `asyncio.Semaphore(3)`
- `notifications.py` — Slack + webhook alerts with SSRF protection and dedup
- `db.py` — SQLite store with WAL mode, thread-safe via RLock
- `cli.py` — CLI entry point. JSON output to stdout, human messages to stderr. Loads `.env` before imports.
- `web/app.py` — FastAPI factory, session auth middleware, lifespan (store + scheduler + agent runner)
- `web/dashboard/` — Jinja2 dashboard routes (HTMX-driven)
- `web/routes.py` — REST API (JSON, API key auth)
- `web/auth.py` — Session/login logic, CSRF tokens

## Adding resource types

1. Create `templates/{type_name}/discovery.md` and `templates/{type_name}/health_check.md`
2. Add entry to `resource_types.py`
3. Templates use `{{resource_name}}`, `{{ssh_host}}`, etc. as placeholders

## Security model

- **RBAC:** Two roles — admin (full access) and viewer (read-only). Enforced server-side via `_require_admin()` on dashboard POST routes and `require_api_key_admin` dependency on API mutation endpoints. See `web/dashboard/__init__.py` and `web/auth.py`.
- **Execution gate:** `SUPAVISION_EXECUTION_ENABLED` (default `false`) gates approve/reject/implement in routes.py, cli.py, and dashboard. Must be enforced on any new code-modification endpoints.
- **API keys** have a `role` column (`admin` or `viewer`). Keys inherit the role of the creating user.

## Codebase CLI commands

```bash
supavision scan <resource_id>          # Run regex scan
supavision findings <resource_id>      # List findings
supavision evaluate <work_item_id>     # Create evaluation job
supavision implement <work_item_id>    # Create implementation job (requires EXECUTION_ENABLED=true)
supavision scout <resource_id>         # Launch scout agent
supavision approve <work_item_id>      # Approve for implementation (requires EXECUTION_ENABLED=true)
supavision reject <work_item_id>       # Reject work item (requires EXECUTION_ENABLED=true)
supavision blocklist                   # List blocklist entries
```

## Testing

Tests use real SQLite databases in `tmp_path`. No mocking of the store layer.
Engine and CLI tests mock the Claude CLI subprocess.
Lane boundary tests (`test_lane_boundary.py`) verify import isolation via AST parsing.
`asyncio_mode = "auto"` is set in pyproject.toml — async test functions run without `@pytest.mark.asyncio`.

## Code style

- Ruff: line-length 120, target Python 3.12, select `E,F,I,W`
- Pydantic models for all data structures
- Infrastructure domain must never import from `models.work`; codebase domain must never import from `models.health`
