# Changelog

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
