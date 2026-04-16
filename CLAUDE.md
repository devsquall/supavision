# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest tests/ -v
.venv/bin/pytest tests/test_evaluator.py -v          # single test file
.venv/bin/pytest tests/test_store.py -k "test_save"  # single test by name
.venv/bin/ruff check src/ tests/
.venv/bin/ruff format --check src/ tests/
.venv/bin/uvicorn supavision.web.app:create_app --factory --port 8080
```

Set `SUPAVISION_COOKIE_SECURE=false` in `.env` for local HTTP dev (no HTTPS).

## Architecture

Single-pipeline design. See `ARCHITECTURE.md` for the full rationale and anti-patterns.

**Pipeline:** Resource → Run → Report → Evaluation → Alert

Infrastructure monitoring via Claude Code CLI subprocess (`engine.py`).

### Models package
```
models/
├── core.py    — Resource, Run, RunStatus, RunType, Credential, Schedule, User, Session
└── health.py  — Report, ReportPayload, IssueSeverity, Evaluation, Severity,
                 SystemContext, Checklist, Metric, RunMetadata, IssueDiff
```

Import rules enforced by `tests/test_lane_boundary.py` (AST-based import verification).

## Key files

- `engine.py` — Core run logic, Claude CLI integration, SSE output streaming
- `evaluator.py` — Rule-based severity assessment (zero LLM cost)
- `executor.py` — SSH command execution with multiplexing
- `tools.py` — Scoped read-only tools for infrastructure investigation
- `discovery_diff.py` — Drift detection between baselines
- `mcp.py` — MCP server (JSON-RPC over stdio)
- `scheduler.py` — Cron-based job scheduling with `asyncio.Semaphore(3)`
- `notifications.py` — Slack + webhook alerts with SSRF protection and dedup
- `db.py` — SQLite store with WAL mode, thread-safe via RLock
- `cli.py` — CLI entry point. JSON output to stdout, human messages to stderr. Loads `.env` before imports.
- `web/app.py` — FastAPI factory, session auth middleware, lifespan (store + scheduler)
- `web/dashboard/` — Jinja2 dashboard routes (HTMX-driven)
- `web/routes.py` — REST API (JSON, API key auth)
- `web/auth.py` — Session/login logic, CSRF tokens

## Adding resource types

1. Create `src/supavision/prompt_templates/{type_name}/discovery.md` and `health_check.md`
2. Add entry to `resource_types.py`
3. Templates use `{{resource_name}}`, `{{ssh_host}}`, etc. as placeholders

## Security model

- **RBAC:** Two roles — admin (full access) and viewer (read-only). Enforced server-side via `_require_admin()` on dashboard POST routes and `require_api_key_admin` dependency on API mutation endpoints. See `web/dashboard/__init__.py` and `web/auth.py`.
- **API keys** have a `role` column (`admin` or `viewer`). Keys inherit the role of the creating user.

## Testing

Tests use real SQLite databases in `tmp_path`. No mocking of the store layer.
Engine and CLI tests mock the Claude CLI subprocess.
Lane boundary tests (`test_lane_boundary.py`) verify import isolation via AST parsing.
`asyncio_mode = "auto"` is set in pyproject.toml — async test functions run without `@pytest.mark.asyncio`.

## Code style

- Ruff: line-length 120, target Python 3.12, select `E,F,I,W`
- Pydantic models for all data structures
