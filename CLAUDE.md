# CLAUDE.md

## Commands

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest tests/ -v
.venv/bin/ruff check src/ tests/
.venv/bin/uvicorn supavision.web.app:create_app --factory --port 8080
```

## Architecture

Two-lane design. See `ARCHITECTURE.md` for the full rationale.

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

Import rules enforced by `tests/test_lane_boundary.py` (AST-based).

## Key files

### Infrastructure domain (Lane 1)
- `engine.py` — Core run logic, Claude CLI integration, SSE output streaming
- `evaluator.py` — Rule-based severity assessment (zero LLM cost)
- `executor.py` — SSH command execution with multiplexing
- `tools.py` — Scoped read-only tools for infrastructure investigation
- `discovery_diff.py` — Drift detection between baselines

### Codebase domain (Lane 2)
- `codebase_engine.py` — Orchestrates scan, evaluate, implement, scout
- `scanner.py` — 81 regex security patterns across 9 languages (zero cost)
- `blocklist.py` — False-positive learning from rejection feedback
- `agent_runner.py` — Background thread job executor (Claude Code subprocess)
- `code_evaluator.py` — Evaluation prompt generation
- `prompt_builder.py` — Implementation prompt generation

### Shared
- `mcp.py` — MCP server (9 tools: 4 health + 5 work), JSON-RPC over stdio
- `scheduler.py` — Cron-based job scheduling with `asyncio.Semaphore(3)`
- `notifications.py` — Slack + webhook alerts with SSRF protection and dedup
- `db.py` — SQLite store with WAL mode, thread-safe via RLock
- `web/dashboard.py` — All dashboard routes (resources, findings, settings, SSE)
- `web/routes.py` — REST API (resources CRUD + codebase scan endpoint)

## Adding resource types

1. Create `templates/{type_name}/discovery.md` and `templates/{type_name}/health_check.md`
2. Add entry to `resource_types.py`
3. Templates use `{{resource_name}}`, `{{ssh_host}}`, etc. as placeholders

## Codebase CLI commands

```bash
supavision scan <resource_id>          # Run regex scan
supavision findings <resource_id>      # List findings
supavision evaluate <work_item_id>     # Create evaluation job
supavision implement <work_item_id>    # Create implementation job
supavision scout <resource_id>         # Launch scout agent
supavision approve <work_item_id>      # Approve for implementation
supavision reject <work_item_id>       # Reject work item
supavision blocklist                   # List blocklist entries
```

## Testing

Tests use real SQLite databases in tmp_path. No mocking of the store layer.
Engine and CLI tests mock the Claude CLI subprocess.
Lane boundary tests (`test_lane_boundary.py`) verify import isolation via AST parsing.
Run a single test: `.venv/bin/pytest tests/test_evaluator.py -v`

## Code Style

- Use ruff for linting
- Pydantic models for all data structures
- Type hints on all public functions
- Infrastructure domain imports from `models.core` + `models.health` only
- Codebase domain imports from `models.core` + `models.work` only
