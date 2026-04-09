# Supavision

[![CI](https://github.com/devsquall/supavision/actions/workflows/ci.yml/badge.svg)](https://github.com/devsquall/supavision/actions)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://github.com/devsquall/supavision/blob/main/LICENSE)

**Point it at a server, and it figures out the rest.**

Supavision uses Claude to explore your infrastructure, understand what's running, and monitor it — without you writing check scripts or defining metrics. It also scans codebases for security and quality issues, using AI to assess severity and impact.

## Why Supavision?

Traditional monitoring requires you to define every check, threshold, and alert rule upfront. Supavision flips this:

- **Discovery, not configuration.** Point it at a server via SSH. Claude explores the system, finds running services, databases, and configs, and builds a baseline of what "normal" looks like.
- **Drift detection, not threshold alerts.** Instead of "CPU > 90%", Supavision detects "this service wasn't running yesterday" or "the config file changed since last check."
- **Zero LLM cost.** Uses Claude Code CLI (included with your Claude subscription). No per-token API charges.
- **Codebase scanning included.** 92 security patterns across 9 languages, with AI-powered evaluation that separates real issues from false positives.

## Two Capabilities

### Infrastructure Monitoring

```
Add server → Discovery (Claude explores via SSH) → Baseline → Scheduled health checks → Slack alerts
```

Supports: **Servers** (SSH), **AWS Accounts**, **Databases**, **GitHub Orgs** — extensible via templates.

### Codebase Scanning

```
Add codebase → Scan (92 regex patterns) → AI Evaluate (severity + impact) → Review → Dismiss or monitor
```

Findings are scanned, then AI-evaluated for real exploitability. False positives are learned and auto-dismissed in future scans.

## Quick Start

```bash
pip install supavision
supavision doctor
supavision create-admin        # Create your first admin user
supavision seed-demo           # Populate with sample data
supavision serve --port 8080
```

Open `http://localhost:8080`, sign in, and you'll see a dashboard with sample resources, health history, and code findings.

**Prerequisites:** Python 3.12+ and [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (for infrastructure monitoring). Codebase scanning works without Claude CLI.

**For development:**

```bash
git clone https://github.com/devsquall/supavision.git
cd supavision
pip install -e ".[dev]"
```

## Features

| Feature | Details |
|---------|---------|
| **Web dashboard** | Real-time status, live SSE streaming, 30-day health grid, system status banner |
| **User auth** | Session-based login, role-based access (admin/viewer), profile page, user management |
| **6 resource types** | Server, AWS Account, Database (PG + MySQL), GitHub Org, Codebase |
| **Codebase scanner** | 92 regex patterns across 9 languages with false-positive learning |
| **AI evaluation** | Claude analyzes findings for real exploitability, not just pattern matches |
| **Structured metrics** | Schema-validated per-resource metrics with time-series history |
| **Cross-resource correlation** | Detects related issues across parent/child resources |
| **REST API** | 23 endpoints with API key auth, findings CRUD, metrics, incidents |
| **MCP server** | 11 tools for querying resources, reports, findings, metrics from Claude CLI |
| **Slack alerts** | Smart dedup, SSRF-protected webhooks, rate limiting |
| **Security** | CSRF protection, session management, DB permissions, audit logging |

## CLI

### Infrastructure

```bash
supavision resource-add prod-web --type server \
  --config ssh_host=10.0.1.5 ssh_user=ubuntu
supavision run-discovery <resource_id>
supavision run-health-check <resource_id>
supavision set-schedule <resource_id> --health-check "0 */6 * * *"
supavision notify-configure <resource_id> --slack-webhook https://hooks.slack.com/...
```

### Codebase

```bash
supavision resource-add my-app --type codebase --config path=/home/user/myapp
supavision scan <resource_id>
supavision findings <resource_id>
supavision evaluate <work_item_id>
supavision scout <resource_id> --focus security
```

### Operations

```bash
supavision serve --port 8080       # Web dashboard + API
supavision run-scheduler           # Cron-based scheduling
supavision doctor                  # Health check
supavision seed-demo               # Sample data for evaluation
supavision purge --days 90         # Cleanup old data
```

## Using as a Library

Supavision can be used programmatically. The scanner works with zero external dependencies:

```python
from supavision import scan_directory, Finding

findings = scan_directory(resource_id="my-app", directory="/path/to/project")
for f in findings:
    print(f"{f.severity}: {f.file_path}:{f.line_number} — {f.category}")
```

For infrastructure monitoring (requires Claude Code CLI):

```python
from supavision import Store, Engine, Resource

store = Store(".supavision/supavision.db")
resource = Resource(name="prod-web", resource_type="server",
                    config={"ssh_host": "10.0.1.5", "ssh_user": "ubuntu"})
store.save_resource(resource)

engine = Engine(store=store)
run = engine.run_discovery(resource.id)
print(f"Status: {run.status}")
```

## Docker

```bash
docker compose up -d
```

Dashboard at `http://localhost:8080`. Data persists in the `supavision-data` volume.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SUPAVISION_PASSWORD` | *(none)* | Bootstrap: auto-creates admin user on first start (use `create-admin` CLI instead) |
| `SUPAVISION_BACKEND` | `claude_cli` | Backend: `claude_cli` (free) or `openrouter` (API) |
| `OPENROUTER_API_KEY` | *(none)* | Required if backend is `openrouter` |
| `SLACK_WEBHOOK` | *(none)* | Global fallback Slack webhook URL |
| `SUPAVISION_MODEL` | `anthropic/claude-sonnet-4` | Model for investigation |
| `SUPAVISION_CHECK_INTERVAL` | `60` | Scheduler check interval (seconds) |
| `SUPAVISION_CLI_TIMEOUT` | `900` | Claude CLI timeout (seconds) |
| `SUPAVISION_SESSION_HOURS` | `8` | Session expiry (hours) |
| `SUPAVISION_SESSION_IDLE_MINUTES` | `120` | Idle timeout (minutes) |
| `SUPAVISION_COOKIE_SECURE` | `true` | Set `false` for local HTTP dev (no HTTPS) |
| `SUPAVISION_EXECUTION_ENABLED` | `false` | Enable code modification features (approve, implement). Disabled by default in v1. |
| `WEBHOOK_ALLOWED_DOMAINS` | *(none)* | Comma-separated webhook domain allowlist |

## MCP Server

Supavision includes an MCP server that lets Claude CLI query your monitoring data in conversations.

```bash
supavision mcp-config  # Print config for Claude CLI
```

**11 tools available:**

| Tool | Description |
|------|-------------|
| `supavision_list_resources` | All resources with current severity |
| `supavision_get_latest_report` | Latest health check report |
| `supavision_get_baseline` | Discovery baseline + checklist |
| `supavision_get_run_history` | Recent runs with status |
| `supavision_get_metrics` | Latest structured metrics for a resource |
| `supavision_get_metrics_trend` | Metric history over time (time-series) |
| `supavision_list_findings` | Codebase findings with filters |
| `supavision_get_finding` | Full finding details |
| `supavision_get_project_stats` | Finding counts by stage |
| `supavision_list_blocklist` | Known false-positive patterns |
| `supavision_search_findings` | Search across all findings |

## Architecture

```
                          Resource
                         /        \
              Lane 1: Health       Lane 2: Work
              (infrastructure)     (codebase)
                   |                    |
         Engine → Report →       Scanner → Findings →
         Evaluation → Alert      AI Evaluation → Review
```

Two parallel data pipelines sharing a common Resource model. Infrastructure monitoring produces aggregate health reports. Codebase scanning produces per-issue findings with individual lifecycles. See [ARCHITECTURE.md](https://github.com/devsquall/supavision/blob/main/ARCHITECTURE.md) for details.

**Tech stack:** Python 3.12+, FastAPI, HTMX, SQLite (WAL), Claude Code CLI.

## Adding Resource Types

Create `templates/{type_name}/discovery.md` and `health_check.md` with `{{placeholder}}` syntax, then add an entry to `resource_types.py`. See [ARCHITECTURE.md](https://github.com/devsquall/supavision/blob/main/ARCHITECTURE.md) for placeholders.

## Security

Supavision runs AI agents on your infrastructure. Read [SECURITY.md](https://github.com/devsquall/supavision/blob/main/SECURITY.md) for the full threat model, tool scoping, and deployment recommendations.

**Key points:**
- Session-based auth with role-based access (admin/viewer), CSRF protection, idle timeout
- Credentials stored as env var references, never the actual secrets
- Infrastructure agents use allowlisted read-only commands
- Codebase analysis agents use read-only tools (Read, Glob, Grep)
- SSRF-protected webhook dispatch with rate limiting
- Database files restricted to owner-only permissions (0600)

## Contributing

See [CONTRIBUTING.md](https://github.com/devsquall/supavision/blob/main/CONTRIBUTING.md) for setup, testing, and the two-lane architecture rules.

```bash
pip install -e ".[dev]"
pytest tests/ -v          # 616 tests
ruff check src/ tests/    # Linting
```

## License

[MIT](https://github.com/devsquall/supavision/blob/main/LICENSE)
