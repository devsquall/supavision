# Changelog

## Unreleased — 0.4.5.dev0 (in-flight release-gate follow-ups)

This is a pre-release marker (PEP 440 `.dev0`). The codebase contains all of v0.4.5's planned work plus the dev-team-review follow-ups that close the release gates. When all gates pass, drop `.dev0` and tag v0.4.5.

### Security
- **Complete the secrets policy.** GET `/api/v1/resources/{id}` no longer leaks legacy raw values (`aws_secret_key`, `github_token`, `db_password`, …) from rows created before v0.4.5 — the response filter now uses `secrets_policy.is_secret_key(k)` instead of a hard-coded 3-key set. `webhook_url`, `teams_webhook`, and `pagerduty_integration_key` are now `KNOWN_SECRET_KEYS`, so the write-boundary rejects them at the wizard, edit form, REST API, and CLI alike.
- **Frontend supply chain.** Dashboard now loads zero third-party JS/CSS by default. `htmx@2.0.4` and `xterm@5.5.0` are vendored to `web/static/vendor/` with provenance (source URL, version, license, SHA-256). Google Fonts is replaced by a system-font stack — no privacy concern, no off-host requests.
- **First-run lockdown.** Previously, when no users existed, the session middleware logged CRITICAL and served the dashboard with `is_admin=True` — so anyone hitting the port before `supavision create-admin` ran got open-access admin. Now: every dashboard URL redirects to `/landing?setup=1` (a setup-instructions banner) until at least one user exists. `/login` stays reachable but no credentials match an empty users table.
- **OpenAPI /docs admin-only.** `/docs`, `/redoc`, `/openapi.json` previously rendered the full API surface to any authenticated session user (viewers included). Now mounted behind an explicit admin role check; viewers see 403.
- **Log redaction for webhook URLs.** New `_log_redact.redact_url()` strips the path/query/fragment from Slack/Teams/PagerDuty/Opsgenie URLs in log lines, so a webhook secret token cannot accidentally exfiltrate through a `logger.warning("Webhook %s failed: ...", url)` style log. Hostname is preserved for debuggability; non-credential hosts (your internal API) are left alone.

### UX
- **Grouped CLI help.** `supavision --help` now groups its 28 subcommands into eight domain sections (resources, runs, reports & context, monitoring config, notifications, server & api keys, mcp integration, admin & setup) via a custom epilog. The flat alphabetical block argparse used to generate is suppressed.
- **Smarter empty states.** `/sessions`, `/schedules`, `/alerts`, `/activity` now branch on `resources_count`: a fresh install pushes users to "Add a Resource", while a user with existing resources gets page-specific guidance (trigger a run, configure a webhook, etc.).
- **Sidebar accessibility.** Active sidebar items carry `aria-current="page"`. Nav sections are wrapped in `role="group"` with `aria-labelledby` pointing at the section heading, so screen readers announce section context before listing items. Collapse-toggle has an explicit `aria-label`.

### Documentation
- **`docs/QUICKSTART.md`** — full zero-to-Slack-alert walkthrough (install, Claude login, create-admin, add resource, run discovery, run health check, wire alerts, run scheduler). Linked from the top of the README.
- **Credentials section in README** — table of `KNOWN_SECRET_KEYS`, three ways to attach (CLI / REST / wizard), notification credentials, legacy-row migration path.

### Internal
- **`SUPAVISION_DB_PATH` honored by `create_app()`** — previously `uvicorn supavision.web.app:create_app --factory` silently ignored the env var and used `.supavision/supavision.db`. Resolution order is now: explicit `db_path=` kwarg → `SUPAVISION_DB_PATH` → fallback default.
- **`Store.count_resources()`** — used by the global `_render` helper to inject `resources_count` into every template context for empty-state branching.

### Bug fixes
- **Engine no longer writes ghost FAILED runs on lock contention.** When the scheduler or dashboard triggers a run, the engine acquires the per-resource lock *before* persisting the Run row. If another run is in flight, the new trigger raises without leaving a noise row. (API triggers still mark the pre-existing PENDING row FAILED so the API caller has a terminal state to poll.)
- **Scheduler skips resources with PENDING or RUNNING runs.** Previously `get_latest_runs_batch()` returned only COMPLETED runs, so a resource whose only run was still in flight looked "never run before" to `_get_due_jobs` and got queued for a duplicate.
- **Dashboard trigger buttons share the same atomic flow as the API.** Both dashboard handlers now go through a new `web/run_triggers.py` helper, return 202 with a `run_id` on success and 409 with `active_run_id` on conflict. No more silent double-clicks.
- **Resource-detail workflow state is correct on busy resources.** `workflow_step` / `recommended_action` were derived from the first page of the run history, so a resource with >10 health-check runs since its last discovery was wrongly marked "needs discovery". Now derived from `store.get_latest_run(resource_id, run_type)`.
- **PagerDuty no longer reports "sent" for healthy/info events.** Severity gating moved out of `PagerDutyChannel.send` and into `send_alert`, so the notification log only records actual delivery attempts.

### API
- **Trigger endpoints now return 202 Accepted** (previously 200). The work is queued in the background; 202 is the canonical "accepted, not yet done" status. The 409 conflict shape is unchanged.

### UX
- **Sidebar nav cleanup.** "Incidents & Runs" renamed to "Runs" — the incidents REST API is a power-user surface for now (no auto-create on severity transitions, no dashboard page).

### Internal
- **Incident API validation tightened.** `CreateIncidentRequest.severity` is now an enum (`critical | warning | info`); `snoozed_until` must be future-dated; `evaluation_id` must exist and belong to the named resource; `owner_user_id` must reference an existing user.
- **Notification adapters covered.** `TeamsChannel` and `PagerDutyChannel` now have unit tests for payload shape, severity mapping, and failure handling. Both adapters remain env-var-configured (no settings UI surface).

## 0.4.5 (2026-05-18)

### Security
- **No raw secrets stored in resource config.** AWS keys, GitHub tokens, DB passwords, and Slack webhook URLs must now be referenced by environment-variable name (`Resource.credentials`) instead of stored as plaintext values (`Resource.config`). Enforced at every write boundary: dashboard wizard, edit form, slack-webhook update, REST `POST/PUT /api/v1/resources`, `supavision resource-add`, and `supavision notify-configure`. Existing rows with legacy raw values continue to load and run; a deprecation warning is logged on every Slack send until the credential is migrated by re-saving via the notifications form.
- **Command palette / `/api/v1/search` auth fixed (three layers).** (1) Handler read `request.state.user` where middleware sets `request.state.current_user`; (2) `/api/v1/*` is skipped by session middleware so session users never had `current_user` populated on this endpoint; (3) `x-api-key` was a header-presence check, not a DB validation. Introduces `AuthContext` + `get_auth_context(request)` which validates either cookie or API key against the DB. A bogus API key no longer falls through to session auth.

### Bug fixes
- **API trigger endpoints return the actual executing run.** `POST /api/v1/runs`, `POST /api/v1/resources/{id}/discover`, and `POST /api/v1/resources/{id}/health-check` previously pre-created a PENDING run and returned its ID, then the engine created a *second* RUNNING run — leaving the returned ID pointing at an orphan. The engine now accepts an optional `run_id` and updates that row in place. The two resource-scoped endpoints additionally now pre-create + return a `run_id` (parity with `/api/v1/runs`).
- **Atomic in-flight check.** `Store.create_pending_run_if_no_active()` uses `BEGIN IMMEDIATE` within the existing connection lock to close the TOCTOU window between "check no active run" and "insert pending row." All three trigger endpoints return `409 {"error": "run_in_flight", "active_run_id": ...}` on conflict.

## 0.4.4 (2026-04-23)

### Features
- **`supavision setup`** — guided first-run wizard: checks Claude CLI binary, detects auth state, and offers to run `claude login` interactively. Docker-aware (prints `docker exec` instructions instead of opening a browser). Non-TTY safe (prints manual instructions and exits cleanly in CI/scripted environments)
- **`supavision doctor` auth check** — new `claude_auth` check alongside the binary check. Reports `[OK] authenticated (OAuth)` or `[FAIL] not authenticated — run 'supavision setup' or 'claude login'`. File-based detection (no subprocess hang)
- **`supavision serve` startup warning** — logs a warning at startup if the Claude CLI backend is active but not authenticated, catching the case where users skip `doctor`/`setup` and go straight to `serve`

### Fixes
- Engine now detects auth-related errors in Claude CLI stderr and surfaces a human-readable message ("Claude CLI is not authenticated. Run 'claude login'...") instead of a raw exit code dump
- Docker `docker-compose.yml`: mounts `${HOME}/.claude:/root/.claude:rw` so OAuth credentials persist across container restarts — users no longer need to re-authenticate after every `docker compose down/up`

### Documentation
- README Docker section updated: documents the `~/.claude` volume mount and clarifies that `claude login` is a one-time step

## 0.4.3 (2026-04-22)

### Features
- **Retry button** on failed runs in resource detail — one click to re-run the same `discovery` or `health_check` from the failure context (no navigation required)
- **Export report as Markdown** via `GET /reports/{id}/export.md` — clean, self-contained .md file with summary, metrics table, payload diff, full issues, and raw report. Sanitized filename, viewer-allowed
- **Copy report summary to clipboard** — Slack-pasteable short format (severity + summary + top 5 issues + URL); reuses existing `copyText()` helper
- **Freshness color coding** on resource cards — 6px dot signals fresh (<1h, green) / aging (<24h, neutral) / stale (>24h, yellow) / never (red); a11y-safe (relative text already conveys the same info)

### Security
- Input validation on all resource form and API endpoints (name ≤200 chars, config values ≤500 chars, monitoring requests capped at 50 items / 500 chars each); enforced in API request models and form handlers
- `SUPAVISION_PASSWORD` boot-time admin auto-creation now logs a WARNING when the supplied password fails strength checks (no longer silently accepts weak credentials; deprecated path still works)
- 26 prompt-injection regression tests guard against attempts to override the system prompt, exfiltrate credentials, or escalate to write commands
- README "Deploying SSH access safely" section with concrete guidance: dedicated read-only user, no sudo, `authorized_keys` command restriction, per-environment keys, network-scoped SSH port

### Fixes
- CLI error messages: 6 instances of `supavisionresource-list` (missing space) → `supavision resource-list`. Copy-pasting from doctor/discover errors no longer fails with "command not found"
- Password strength hint surfaced in CLI on weak input
- Docker `compose.yml`: `ANTHROPIC_API_KEY` and `SUPAVISION_COOKIE_SECURE` now pass through from host env (Claude CLI couldn't auth in container without it)

### Documentation
- README **Backup** section with WAL-safe `sqlite3 .backup` command and restore steps
- README **Docker** section expanded from 1 line to a 3-step first-time setup (`up` → `claude login` → `create-admin`) plus `ANTHROPIC_API_KEY` note for key-based auth

### Infrastructure
- `Dockerfile` pins `@anthropic-ai/claude-code@^2` (was `@latest`) — reproducible builds, comment notes maintenance bump

### Accessibility
- `aria-hidden="true"` on 49 decorative SVGs across 15 templates (empty-state icons, sparklines, sidebar/topbar icons, landing-page card icons, send button) — screen readers no longer announce noise

### Tests
- 778 → 817 tests (input validation, scheduler, trigger_run, prompt injection, report export, resource freshness, additional coverage)

## 0.4.2 (2026-04-17)

### Bug Fixes
- Scheduler: health check runs were silently dropped — RunType.HEALTH_CHECK branch
  never matched in dispatch (only DISCOVERY ran); discovery also incorrectly triggered
  a health check afterward
- Command center: two duplicate class= HTML attributes caused mb-4/mt-3 spacing
  classes to be ignored by browsers
- Wizard: cancel button linked to /resources/new instead of /resources
- profile.html alert class names fixed (alert-error → alert--danger)
- Audit log badge fallback fixed (badge--type → badge--unknown)

### Security
- JS confirm modal: apply _esc() to message parameter (defense-in-depth)
- Removed unauthenticated /api/v1/search endpoint exposure

### Tests
- Added test_scheduler.py: 18 tests covering dispatch correctness, due-job
  scheduling logic, and stale run recovery (zero coverage previously)
- Added TestTriggerRun: 6 tests for POST /api/v1/runs endpoint
- Total: 754 → 778 tests

### Accessibility
- ask.html: added aria-label to send button and textarea

### JavaScript
- Fixed window resize listener memory leak in initLiveTerminal()
- Replaced javascript: href with proper click event handler

### CSS
- Added 480px breakpoint for wizard step bar (overflow-x scroll on mobile)
- Reduced wizard card padding on narrow screens

### Security (continued)
- Input validation on all resource form and API endpoints (name ≤200 chars, config values ≤500 chars, monitoring requests capped at 50 items / 500 chars each)
- API rate limiting on mutating endpoints (60 req/min per IP)
- Lock files created with `0o600` permissions; previously world-readable
- Temp files created with `mkstemp()`, replacing a `mktemp()` TOCTOU race condition

### Cleanup
- Removed stale blocklist CREATE TABLE (zero references in codebase)
- Removed 7 dead CSS badge classes (badge--webhook, badge--discovery, etc.)
- Removed stale _glossary.html run type labels for removed run types
- Lane 2 / codebase-scanning subsystem fully removed (templates, CSS classes, JS functions, test helpers, prompt templates)
- Dead `codebase/` and `example/` prompt template directories deleted

### Improved
- Self-documenting UI: inline descriptions and tooltips added to all 15 major pages (dashboard, resources, reports, sessions, metrics, schedules, activity, alerts, command center, ask)
- MCP server: `supavision_get_severity_trend` tool added
- MCP server: `datetime.now()` fixed to `datetime.now(timezone.utc)` in metrics trend handler

## 0.4.1 (2026-04-15)

Docs-only patch release. No code changes.

- **README.md**: rewritten to reflect current single-pipeline architecture. Removed stale "Two Capabilities / Codebase Scanning" section, "6 resource types" claim (now 5), "23 REST endpoints" claim (now 15), "11 MCP tools" claim (now 7), `scan_directory` / `Finding` library example, and references to deleted CLI commands.
- **ARCHITECTURE.md**: rewritten as single-pipeline (Run → Report → Evaluation → Alert). Removed Lane 1 / Lane 2 framing, WorkItem / Finding / ManualTask references, and dead anti-patterns. Added coverage of the structured `ReportPayload` + issue set-diff behavior that actually ships.
- Added `[![PyPI]()]` badge to README.

## 0.4.0 (2026-04-15)

Major release. Infrastructure-monitoring-only identity solidified. Findings / codebase-scanning subsystem fully removed from the UI, REST API, MCP tools, and package public API. Significant hardening across security, UX, terminal transparency, and code health.

### Breaking Changes
- **Public API**: `supavision.Finding` and `supavision.scan_directory` are no longer exported from the package. Users importing them will need to pin to `0.3.x` or remove the imports.
- **REST API**: 11 endpoints removed — `GET/POST /api/v1/findings/*`, `POST /api/v1/codebase/{id}/scan`, `POST /api/v1/resources/{id}/scout`, `GET /api/v1/jobs/{id}`, `GET /api/v1/blocklist`.
- **CLI**: 8 commands removed — `scan`, `findings`, `evaluate`, `implement`, `scout`, `approve`, `reject`, `blocklist`.
- **MCP server**: 5 tools removed — `supavision_list_findings`, `supavision_get_finding`, `supavision_get_project_stats`, `supavision_list_blocklist`, `supavision_search_findings`.

### Security Hardening
- **RBAC enforced**: Admin-only gate added to 21+ dashboard mutation routes and 11 REST API mutation endpoints (previously a cosmetic-only role check).
- **`SUPAVISION_EXECUTION_ENABLED` flag now actually enforced** on `approve`, `reject`, `implement` endpoints (previously dead code).
- API keys gained a `role` column with migration for existing databases.
- `SECURITY.md` rewritten to accurately describe session-based auth, RBAC matrix, rate limiting, execution gate.
- Rate limits made operator-tunable via `SUPAVISION_RATE_LIMIT_LOGIN`, `_ASK`, `_DEFAULT` env vars.

### Live Terminal Transparency
- Claude CLI invocation switched to `--output-format stream-json --verbose`. Users now see live tool calls, results, and reasoning during health checks (previously just "Connecting..." then a dump).
- Real stats (turns, tokens, cost) now populated from the structured `result` event.

### UX Elevation
- **Command palette** (`Cmd+K` / `Ctrl+K`) with fuzzy search across resources and findings, plus `?` help overlay and `g d / g r / g s` go-to shortcuts.
- **Mobile table responsiveness**: opt-in `.table--stack` class converts tables to card-stacks below 768px on reports, sessions, schedules.
- **Profile name editing**: inline editable name field with race-condition-safe DB re-fetch.
- **Error page recovery CTAs** per status code with Cmd+K hint for 400/404.
- **Schedules empty state icon** added (calendar + clock).
- **Onboarding**: dashboard welcome card + "Load Demo Data" button via new `POST /dashboard/seed-demo` endpoint.
- **Public landing page** at `/landing` — standalone, dark-first, no-auth marketing page.
- Sparklines, trend arrows, confidence gauge, 30-day health grid on resource detail.
- Typography: Inter font, tighter heading letter-spacing, 1.6 body line-height.
- CSS polish: resting shadows, button microinteractions, form focus glow, dropdown animations, toast icons.
- HTMX error handling: user-facing toast on server errors (previously silent).
- Mobile sidebar toggle fixed (was toggling wrong CSS class).
- Terminal UX: copy-output button, 80-column default with FitAddon, inner-shadow blend, no disruptive hard-reload on job completion.

### Structured Reports (Workstreams A–E)
- Reports now carry optional structured `ReportPayload` with typed issues, metrics, actions.
- Dual-mode evaluator: structured-issue path + legacy regex prose path, feature-flagged per resource type.
- Issue set-diff vs previous run: new/persisting/resolved issues.
- Smarter Slack alerts driven by issue diffs.
- CLI report formatting + API pagination + `supavision_get_severity_trend` MCP tool.
- Severity streak indicator on dashboard action items.

### Template Depth + Tool Allowlist (Workstreams F–I)
- `github_org` and `aws_account` prompt templates rewritten with concrete CLI commands, thresholds, and permission-denied handling.
- Agent tool allowlist expanded: `host`, `dig`, `nslookup`, `openssl s_client`, `ping -c`, `traceroute`.
- API keys gained `last_used_at` tracking, displayed in settings.
- Command Center empty state upgraded.

### Dead Code Cleanup
- ~5,300 lines removed: 7 backend modules (scanner, codebase_engine, blocklist, code_evaluator, prompt_builder, agent_runner, models/work), 17 Store methods, 5 MCP tools, 8 CLI commands, scheduler codebase branch, ask.py dead composers, Agent Jobs tab from sessions.

### Test Coverage + CI
- 754 tests passing.
- Added test suites: REST API endpoints, MCP Lane 2 tools, security edge cases, CLI coverage, dashboard routes, stream-json formatter.
- RBAC enforcement tests (viewer → 403 on mutations).
- CI lint cleaned: 25 accumulated pre-existing errors fixed.

### Documentation
- `README.md` rewritten: single-lane (infrastructure only), accurate endpoint/tool counts, Findings references removed.
- `CLAUDE.md` and `ARCHITECTURE.md` updated to reflect single-lane design.
- `CONTRIBUTING.md` adds two-lane architecture rules and execution gate notes.
- New `.env.example` entries for session config.

### Production Reliability
- Fixed dashboard 500 (stale agent_jobs iteration crashing on production data).
- Added `try/except` + `logger.exception()` wrapper around `dashboard_overview` handler — no more blind 500s.
- Background-tab polling pause via `visibilityState` check on dashboard HTMX triggers.
- Silent `except Exception: pass` on `last_used_at` DB update replaced with `logger.debug`.

## 0.3.0 (2026-04-08)

### Scope Refinement
- **Monitoring-only identity**: Supavision is now positioned as a decision-first monitoring and intelligence system
- Execution features (approve, implement, auto-fix) hidden from UI — backend preserved behind `SUPAVISION_EXECUTION_ENABLED` feature flag
- "Decision" renamed to "AI Assessment", "Recommended Fix" to "Suggested Approach"
- Execution stages (approved, implementing, completed) filtered from all stage pills, filters, and action columns
- Finding lifecycle simplified to: Scanned → Evaluated → (Dismiss or monitor)

### In-Product Documentation
- Tooltips on all action buttons (Scan, Scout, Diagnose, AI Evaluate, Dismiss)
- Section descriptions for Findings, Recent Activity, Schedule, Monitoring Requests, Agent Work, Auth Activity
- Wizard form field hints (SSH Host, SSH User, schedule frequencies, role descriptions)
- Contextual error pages for 400, 403, 404, 429, 500 status codes
- Updated empty states with actionable guidance

### Resource Cards Redesign
- Resource list page redesigned from flat table to actionable card grid
- Each card shows: problem (what's wrong), impact (why it matters), action (what to do)
- Type-aware impact strings (server vs database vs AWS vs codebase)
- Severity-colored left stripe (critical/warning/healthy/unknown)
- Responsive grid layout with hover effects
- HTMX integration for trigger buttons (Run Check, Scan)

### Design System Cleanup
- Defined `--color-primary` token (light + dark themes)
- Removed duplicate CSS definitions (resource-card, btn-danger)
- Replaced hardcoded colors with design tokens (stat-card, danger-zone)
- Removed duplicate scan/scout route definitions from findings.py

## 0.2.2 (2026-04-07)

- Fix PyPI documentation inconsistencies (MCP tool count, auth messaging, install flow)
- Fix reports table missing `.table-wrap` styling
- Fix sidebar collapse toggle partially hidden (overflow + positioning)

## 0.2.1 (2026-04-07)

### Session-Based Authentication
- Full user model with email, password (scrypt), roles (admin/viewer)
- Session management with CSRF, idle timeout, audit logging
- Login page, profile page, user management (admin-only)
- Auto-creates admin from SUPAVISION_PASSWORD on first start
- Rate limiting on login (5/min) and trigger endpoints (10/min)

### Structured Metrics
- Schema-validated per-resource metrics (38 definitions across 5 types)
- Metrics extraction from health check output (`=== METRICS ===` section)
- Cross-resource correlation for root cause analysis
- MCP tools for metrics queries

### Deep Analysis Templates
- 16 prompt templates across server, AWS, database (PG + MySQL), GitHub
- Database engine routing (PostgreSQL vs MySQL specific queries)
- AWS: CloudWatch, cost intelligence, security posture, networking
- Server: security audit, performance trending, certificates

### Dashboard Improvements
- Sidebar user card with avatar and topbar dropdown
- Live output SSE streaming during runs
- 92 scanner patterns (was 81), 11 MCP tools (was 9)

## 0.2.0 (2026-04-06)

- Renamed from `supervisor` to `supavision` (PyPI name available)
- New package structure under `src/supavision/`
- Package data (scanner patterns, prompt templates) properly included in wheel

## 0.1.2 (2026-04-06)

- Fix broken relative links in README (404 on PyPI)
- Update CHANGELOG to reflect full feature set

## 0.1.1 (2026-04-06)

First working PyPI release. v0.1.0 was yanked due to missing package data.

### Two-Lane Architecture
- **Lane 1 (Health):** Infrastructure monitoring via Claude Code CLI
- **Lane 2 (Work):** Codebase scanning + AI-powered evaluation and fixes

### Infrastructure Monitoring
- 5 resource types: Server (SSH), AWS Account, Database, GitHub Org, Codebase
- Discovery, health checks, drift detection, scheduled runs, Slack alerts
- Rule-based severity evaluation (zero LLM cost)
- Engine retry logic (2 attempts, configurable timeout)
- SSE live output streaming during runs
- 30-day health grid and system status banner

### Codebase Scanning
- 81 security patterns across 9 languages (Python, JS/TS, Go, Rust, Java, C/C++, PHP, Ruby)
- Finding lifecycle: Scanned → Evaluated → Approved → Implementing → Completed
- AI evaluation, automated fix generation, scout agent
- False-positive learning via blocklist

### Web Dashboard
- Dark theme, resource management, findings workflow
- Dashboard auth via SUPAVISION_PASSWORD
- Settings page with API key management

### REST API + MCP Server
- Full resource and findings CRUD at `/api/v1/*`
- 9 MCP tools for Claude CLI integration

### Infrastructure
- 529 tests with AST-enforced lane boundaries
- CI with GitHub Actions (Python 3.12 + 3.13)
- Docker support

## 0.1.0 (2026-04-06)

Yanked — missing package data (scanner patterns, monitoring templates).
