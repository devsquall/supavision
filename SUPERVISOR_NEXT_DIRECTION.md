# Supervisor — Next Architecture Direction

## Problem Statement

Phase 1 of the Supervisor project is complete: we have a working AI-powered infrastructure monitoring engine that can discover servers, run health checks via scoped tools, evaluate findings, and alert via CLI. The execution model uses OpenRouter's tool_use API to give Claude controlled access to 7 security-hardened tools (get_system_metrics, check_service_status, read_file, list_directory, check_logs, run_diagnostic, query_database).

**The question is: what do we build next?**

The web UI has been explicitly deferred to Phase 3+. We need to decide the highest-value direction that:
1. Works without a UI (CLI/API interaction only)
2. Operates entirely within our existing cloud environment
3. Uses our existing OpenRouter + cloud code setup
4. Adds the most practical value to the monitoring system

---

## Current Architecture

```
User → CLI (20+ commands) → Engine (tool_use loop, 50 turns max)
                                ↓
                         OpenRouter API (Claude Sonnet)
                                ↓
                         7 Scoped Tools → SSH/Local Executor
                                ↓
                         Report → Evaluator (keyword/LLM/hybrid)
                                ↓
                         stdout alert (no integrations)
```

**What exists:**
- Resource CRUD with parent-child hierarchy and credential inheritance
- Discovery → baseline (system_context + checklist, versioned)
- Health checks → compare against baseline + recent reports
- Cron scheduling with per-resource file locking
- SQLite storage with versioned contexts, checklists, reports, evaluations, runs
- SSH multiplexing for remote servers, local execution for localhost
- .env auto-loading, server + example templates

**What does NOT exist:**
- No way to receive alerts (stdout only — no Slack, webhook, email)
- No HTTP API (CLI is the only interface)
- Only 2 resource types (server, example) — no AWS, database, GitHub
- No drift detection between discoveries
- No web dashboard (intentionally deferred)
- No auth/RBAC (single-user)

---

## Reference Architecture (What We're Benchmarking Against)

The reference project (`supervisor-main`) is a production TypeScript/Express system with:
- 6 resource types (server, aws_account, database, backup_policy, github_org, ec2_volume)
- Slack webhook alerts with per-resource configuration
- Discovery-diff: compares discovery versions, alerts on infrastructure changes
- Web dashboard with 30-day health grid, HTMX live updates
- RBAC with admin/developer roles, instance tokens, API keys
- EC2 management (start/stop/reboot/resize via API)
- Two-tier template system (base + resource-type override)
- Cron scheduling with deduplication and graceful shutdown
- Export scripts generated during discovery, diffed on health checks

Key pattern: **the LLM is used selectively** — for exploration and synthesis during discovery/health checks. The alert decision is made by a separate evaluator (either the LLM itself or an external cheap model). Infrastructure management (EC2 actions) is deterministic code, not LLM.

---

## Candidate Directions

### Direction A: Notifications (Slack + Webhook)

**What:** Add alert delivery so the system is actually useful for monitoring.

**Scope:**
- `notifications.py` — abstract NotificationChannel with two implementations:
  - **Slack**: POST to webhook URL with Block Kit payload (severity-colored sidebar, resource name, summary, expandable report section)
  - **Generic Webhook**: POST JSON to any URL (for PagerDuty, Discord, custom integrations)
- Per-resource notification config (webhook URL, channel, mention users)
- Global fallback (`SLACK_WEBHOOK` env var)
- CLI command: `supervisor notify-test <resource_id>` to verify webhook works
- Discovery drift alerts: when re-discovery finds significant changes vs previous baseline, send notification with diff summary

**Effort:** ~200 lines of new code. 1 new file. Minor CLI extension.

**Value:**
- **HIGH** — Without notifications, the system only works if someone manually runs CLI commands and reads output. With Slack alerts, it becomes a real monitoring tool that pushes findings to the team
- Discovery drift detection (comparing context versions) adds significant value for catching infrastructure changes between scheduled checks
- No UI dependency — works entirely via webhooks

**Dependencies:** None new. Uses existing `httpx` for HTTP POST.

**Risk:** Low. Self-contained feature. If Slack webhook fails, log warning and continue.

---

### Direction B: REST API Layer (FastAPI)

**What:** Add HTTP endpoints that expose all CLI functionality programmatically.

**Scope:**
- `web/app.py` — FastAPI application factory with lifespan manager
- `web/routes/api.py` — JSON API endpoints:
  - `GET /api/v1/resources` — list resources with latest status
  - `POST /api/v1/resources` — create resource
  - `GET /api/v1/resources/{id}` — detail with context, checklist, recent runs
  - `POST /api/v1/resources/{id}/discover` — trigger discovery (background task)
  - `POST /api/v1/resources/{id}/health-check` — trigger health check
  - `GET /api/v1/runs/{id}` — run status + report
  - `GET /api/v1/reports?resource_id=X` — report history
- `web/deps.py` — dependency injection (shared Store, Engine instances)
- `web/auth.py` — API key authentication (hash in DB, `x-api-key` header)
- Auto-generated OpenAPI docs at `/docs`

**Effort:** ~400 lines across 4 new files. New dependencies: `fastapi`, `uvicorn`.

**Value:**
- **MEDIUM-HIGH** — Enables programmatic access, future UI, external integrations, mobile apps
- Foundation for everything else (dashboard, CI/CD integration, third-party tools)
- Auto-generated API docs are immediately useful
- But: without notifications, API consumers still need to poll for results

**Dependencies:** `fastapi`, `uvicorn[standard]` (new). Both well-maintained, free.

**Risk:** Medium. API design decisions are hard to change later. Need to get resource model and auth right from the start.

---

### Direction C: Additional Resource Types (aws_account, database)

**What:** Expand monitoring coverage beyond Linux servers.

**Scope:**
- `templates/aws_account/discovery.md` — discover AWS services, regions, spend
- `templates/aws_account/health_check.md` — check EC2 status, Lambda errors, cost spikes, IAM key age
- `templates/database/discovery.md` — discover schema, tables, data freshness baselines
- `templates/database/health_check.md` — check data freshness, connection health, replication, storage
- New tool: `aws_cli(service, action, params)` — allowlist of read-only AWS CLI commands
- Enhanced `query_database` tool — support PostgreSQL and MySQL connection strings from credentials

**Effort:** ~300 lines of templates + ~150 lines of new tools. Mostly prompt engineering.

**Value:**
- **MEDIUM** — Broader coverage is valuable, but only if someone is actively checking reports
- AWS monitoring is high-value for cost optimization (catching idle resources, spend spikes)
- Database freshness monitoring catches ETL failures early
- But: without notifications, these extra resource types still require manual CLI checks

**Dependencies:** AWS CLI must be installed on the server (already is). No new Python deps.

**Risk:** Medium. AWS templates need careful testing across different account configurations. Database templates need to handle connection failures gracefully.

---

### Direction D: Operational Hardening

**What:** Make the existing system more reliable and observable before adding features.

**Scope:**
- **Scheduler crash recovery**: on startup, mark any "running" runs older than 1 hour as "failed"
- **Data retention**: auto-archive reports older than 90 days, configurable per resource
- **Cost tracking**: log model, tokens, cost per run in `llm_usage` table, CLI command `supervisor costs` to show spend by resource/day
- **Discovery drift**: compare new context version to previous, generate diff summary, store as special report type
- **Test suite**: pytest for executor (mock subprocess), tools (validate allowlists), engine (mock OpenRouter), evaluator (all 3 strategies)
- **Baseline changelog**: append-only log of infrastructure changes detected across health checks

**Effort:** ~500 lines across multiple files. No new dependencies.

**Value:**
- **MEDIUM** — Makes the foundation solid, but doesn't add user-facing features
- Cost tracking prevents surprise API bills (important as usage scales)
- Crash recovery prevents "stuck" runs from blocking resources forever
- Test suite enables confident refactoring for future features
- Discovery drift is high-value but overlaps with Direction A (notifications needed to deliver drift alerts)

**Dependencies:** None new.

**Risk:** Low. All changes are internal improvements. No API surface changes.

---

## Evaluation Criteria

| Criterion | Weight | A (Notify) | B (API) | C (Types) | D (Harden) |
|-----------|--------|------------|---------|-----------|-------------|
| Value without UI | High | **5** — push alerts | 3 — programmatic | 2 — still manual | 3 — internal |
| Effort | Medium | **5** — minimal | 3 — moderate | 3 — moderate | 2 — spread thin |
| Foundation for future | Medium | 3 — standalone | **5** — enables all | 3 — templates only | 4 — reliability |
| Immediate user impact | High | **5** — team gets alerts | 2 — devs get API | 2 — more coverage | 1 — invisible |
| Risk | Low | **5** — isolated | 3 — API design lock-in | 3 — AWS complexity | **5** — safe |

**Weighted scores (rough):** A: 23, B: 16, C: 13, D: 15

---

## Proposed Approach: A First, Then B

**Phase 2a: Notifications + Drift Detection (Direction A)**
- Build Slack + webhook notifications
- Add discovery drift comparison
- Makes the system immediately useful as a real monitoring tool
- ~1 week effort

**Phase 2b: REST API (Direction B)**
- Build FastAPI API layer with auth
- Exposes all functionality programmatically
- Foundation for future UI (Phase 3)
- ~1-2 weeks effort

**Phase 2c: Hardening (cherry-pick from Direction D)**
- Scheduler crash recovery (quick win)
- Cost tracking (important for API usage)
- Test suite (needed before API is stable)

**Phase 3: Web UI** (deferred — only after 2a+2b are stable)

---

## Constraints

1. **Single server** — everything runs on one EC2 instance (t3.small, Ubuntu)
2. **Python only** — existing codebase is Python with Pydantic, SQLite, httpx
3. **OpenRouter** — single API key for all LLM calls (Claude Sonnet for investigation, Haiku for evaluation)
4. **No paid dependencies** — only free/open-source libraries
5. **No UI for now** — CLI and API only, web dashboard is Phase 3+
6. **Security model** — scoped tools with allowlists, no arbitrary command execution, read-only database queries

---

## Questions for Reviewers

1. Is A→B→D the right sequencing, or should we front-load the API (B) since notifications (A) could be delivered through the API later?
2. Should discovery drift detection be part of Direction A (notifications), or split into Direction D (hardening)?
3. Is there a Direction E we're missing? (e.g., MCP server integration, scheduled reports via email, integration with existing review-loop system)
4. For the API layer (B): should we use API keys only, or add session-based auth from the start (for future UI)?
5. How important is the test suite? Should we block on it before building new features, or treat it as parallel work?
