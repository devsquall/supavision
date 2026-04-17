# Changelog

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
