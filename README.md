# Supavision

[![CI](https://github.com/devsquall/supavision/actions/workflows/ci.yml/badge.svg)](https://github.com/devsquall/supavision/actions)
[![PyPI](https://img.shields.io/pypi/v/supavision.svg)](https://pypi.org/project/supavision/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://github.com/devsquall/supavision/blob/main/LICENSE)

**Point it at a server, and it figures out the rest.**

Supavision uses Claude to explore your infrastructure, understand what's running, and monitor it — without you writing check scripts or defining metrics.

## Why Supavision?

Traditional monitoring requires you to define every check, threshold, and alert rule upfront. Supavision flips this:

- **Discovery, not configuration.** Point it at a server via SSH. Claude explores the system, finds running services, databases, and configs, and builds a baseline of what "normal" looks like.
- **Drift detection, not threshold alerts.** Instead of "CPU > 90%", Supavision detects "this service wasn't running yesterday" or "the config file changed since last check."
- **Live transparency.** Watch Claude work in real time — each SSH command, each tool call, each finding — via structured streaming to an xterm terminal in the dashboard.
- **Zero LLM cost.** Uses Claude Code CLI (included with your Claude subscription). No per-token API charges.

## How It Works

```
Add server → Discovery (Claude explores via SSH) → Baseline → Scheduled health checks → Slack alerts
```

Supports: **Servers** (SSH), **AWS Accounts**, **Databases**, **GitHub Orgs** — extensible via prompt templates.

## Quick Start

```bash
pip install supavision
supavision doctor
supavision create-admin        # Create your first admin user
supavision seed-demo           # Populate with sample infrastructure data
supavision serve --port 8080
```

Open `http://localhost:8080`, sign in, and you'll see a dashboard with sample resources, health history, and live activity.

**Prerequisites:** Python 3.12+ and [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code).

**For development:**

```bash
git clone https://github.com/devsquall/supavision.git
cd supavision
pip install -e ".[dev]"
```

## Features

| Feature | Details |
|---------|---------|
| **Web dashboard** | Real-time status, live xterm streaming of Claude's tool calls, 30-day health grid, sparklines |
| **Command palette** | `Cmd+K` (or `Ctrl+K`) global search + keyboard shortcuts (`g d`, `g r`, `?` for help) |
| **User auth** | Session-based login, role-based access (admin/viewer), profile editing, user management |
| **4 resource types** | Server, AWS Account, Database, GitHub Organization |
| **Structured reports** | Optional typed-issue payload via `submit_report` tool — enables set-diff across runs, smart alerts, per-metric trends |
| **Live session transparency** | Stream-json parsing of Claude CLI — see each SSH command, tool result, and reasoning step live |
| **Structured metrics** | Schema-validated per-resource metrics with time-series history |
| **REST API** | 15 endpoints with API key auth (role-scoped: admin keys for mutations, viewer keys read-only) |
| **MCP server** | 7 tools for querying resources, reports, metrics, and severity trends from Claude CLI |
| **Slack alerts** | Driven by issue set-diffs, smart dedup, SSRF-protected webhooks, rate limiting |
| **Security** | CSRF protection, scrypt passwords, session management, RBAC enforcement, rate limiting |
| **Public landing page** | Standalone `/landing` for sharing without requiring login |

## CLI

### Infrastructure

```bash
supavision resource-add prod-web --type server \
  --config ssh_host=10.0.1.5 ssh_user=ubuntu
supavision run-discovery <resource_id>
supavision run-health-check <resource_id>
supavision resource-set-schedule <resource_id> --health-check "0 */6 * * *"
supavision notify-configure <resource_id> --slack-webhook https://hooks.slack.com/...
```

### Operations

```bash
supavision serve --port 8080       # Web dashboard + API
supavision run-scheduler           # Cron-based scheduling
supavision doctor                  # Health check (config + dependencies)
supavision seed-demo               # Sample data for evaluation
supavision purge --days 90         # Cleanup old runs + reports
supavision create-admin            # Bootstrap first admin user
supavision api-key-create          # Generate an API key
```

## Using as a Library

```python
from supavision import Store, Engine, Resource

store = Store(".supavision/supavision.db")
resource = Resource(
    name="prod-web",
    resource_type="server",
    config={"ssh_host": "10.0.1.5", "ssh_user": "ubuntu"},
)
store.save_resource(resource)

engine = Engine(store=store)
run = engine.run_discovery(resource.id)
print(f"Status: {run.status}")
```

## Docker

```bash
# 1. Start the container
docker compose up -d

# 2. Authenticate Claude Code (one-time — opens browser)
docker exec -it supavision-supavision-1 claude login

# 3. Create your admin account (one-time)
docker exec -it supavision-supavision-1 supavision create-admin
```

Dashboard at `http://localhost:8080`. Data persists in the `supavision-data` volume.

**Claude auth persists across restarts.** `docker-compose.yml` mounts `~/.claude` from your host into the container (`${HOME}/.claude:/root/.claude:rw`), so you only need to run `claude login` once. If you prefer API key auth, set `ANTHROPIC_API_KEY` before `docker compose up` and the mount is unused.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SUPAVISION_BACKEND` | `claude_cli` | Backend: `claude_cli` (free, uses CLI subscription) or `openrouter` (API, per-token) |
| `OPENROUTER_API_KEY` | *(none)* | Required if backend is `openrouter` |
| `SLACK_WEBHOOK` | *(none)* | Global fallback Slack webhook URL |
| `SUPAVISION_MODEL` | `anthropic/claude-sonnet-4` | Model for investigation (openrouter backend only) |
| `SUPAVISION_CHECK_INTERVAL` | `60` | Scheduler check interval (seconds) |
| `SUPAVISION_CLI_TIMEOUT` | `180` | Claude CLI timeout (seconds) |
| `SUPAVISION_SESSION_HOURS` | `8` | Session expiry (hours) |
| `SUPAVISION_SESSION_IDLE_MINUTES` | `120` | Idle timeout (minutes) |
| `SUPAVISION_COOKIE_SECURE` | `true` | Set `false` for local HTTP dev (no HTTPS) |
| `SUPAVISION_RATE_LIMIT_LOGIN` | `5` | Login attempts per IP per minute |
| `SUPAVISION_RATE_LIMIT_ASK` | `30` | Ask-page queries per IP per minute |
| `SUPAVISION_RATE_LIMIT_DEFAULT` | `10` | Default rate limit for mutating routes |
| `SUPAVISION_SSH_MUX_DIR` | `/tmp/supavision-ssh-mux` | SSH multiplexing socket directory |
| `WEBHOOK_ALLOWED_DOMAINS` | *(none)* | Comma-separated webhook domain allowlist |

## Backup

The database lives at `.supavision/supavision.db` (override with `SUPAVISION_DB_PATH`). SQLite's `.backup` command produces a consistent snapshot even while Supavision is running:

```bash
sqlite3 .supavision/supavision.db ".backup backup-$(date +%F).db"
```

To restore: stop Supavision, replace the live DB file with the backup, restart.

## MCP Server

Supavision includes an MCP server that lets Claude CLI query your monitoring data in conversations.

```bash
supavision mcp-config  # Print config for Claude CLI
```

**7 tools available:**

| Tool | Description |
|------|-------------|
| `supavision_list_resources` | All resources with current severity |
| `supavision_get_latest_report` | Latest health check report for a resource |
| `supavision_get_baseline` | Discovery baseline + checklist |
| `supavision_get_run_history` | Recent runs with status and timings |
| `supavision_get_metrics` | Latest structured metrics for a resource |
| `supavision_get_metrics_trend` | Metric history over time (time-series) |
| `supavision_get_severity_trend` | Resource-level severity streak + transitions |

## Architecture

```
Resource
   |
   v
Run → Report (+ optional structured payload) → Evaluation → Alert
```

Single-pipeline design. Each scheduled run produces an aggregate health Report with an Evaluation (`healthy` / `warning` / `critical`). Reports can optionally include a typed `ReportPayload` with structured issues, enabling set-diffs across runs and smart Slack alerts that only fire on new/escalating problems. See [ARCHITECTURE.md](https://github.com/devsquall/supavision/blob/main/ARCHITECTURE.md) for details.

**Tech stack:** Python 3.12+, FastAPI, HTMX, xterm.js, SQLite (WAL), Claude Code CLI.

## Adding Resource Types

Create `src/supavision/prompt_templates/{type_name}/discovery.md` and `health_check.md` with `{{placeholder}}` syntax (e.g. `{{resource_name}}`, `{{ssh_host}}`), then add an entry to `resource_types.py`. See [ARCHITECTURE.md](https://github.com/devsquall/supavision/blob/main/ARCHITECTURE.md) for the full list of placeholders and template conventions.

## Security

Supavision runs AI agents that execute read-only commands on your infrastructure. Read [SECURITY.md](https://github.com/devsquall/supavision/blob/main/SECURITY.md) for the full threat model, tool scoping, and deployment recommendations.

**Key points:**
- Session-based auth with role-based access (admin/viewer); RBAC enforced server-side on every mutating route
- Credentials stored as env var references, never the actual secrets
- Infrastructure agents use allowlisted read-only commands (`docker ps`, `systemctl status`, SELECT-only SQL, DNS lookups, TLS cert inspection, bounded ping/traceroute, etc.)
- Scrypt password hashing, CSRF on all mutations, session idle timeout, audit logging
- SSRF-protected webhook dispatch with rate limiting and domain allowlist
- Database files restricted to owner-only permissions (0600)
- API keys inherit the creating user's role; `last_used_at` tracked for auditability

### Deploying SSH access safely

Supavision connects to monitored servers over SSH. The SSH user you configure is the blast radius if anything goes wrong — scope it tightly:

- **Create a dedicated read-only user** on the monitored host (e.g. `supavision-monitor`). Do not reuse a user with sudo, admin, or shell-script execution privileges.
- **Disable sudo for that user.** No `NOPASSWD`, no sudoers entry.
- **Pin the SSH key to a restricted command set** (optional but strongly recommended for production). Prepend the user's `~/.ssh/authorized_keys` entry with `command="..."` to limit which commands the key can run. Example allowlisting only inspection commands:

  ```
  command="/usr/local/bin/supavision-readonly-shell",no-port-forwarding,no-X11-forwarding,no-agent-forwarding ssh-ed25519 AAAA... supavision
  ```

  where `supavision-readonly-shell` is a small wrapper that rejects any command not in your approved set.
- **Separate keys per environment.** One keypair for staging, one for prod. Rotate on breach.
- **Network-scope the SSH port** — restrict TCP/22 on the monitored host to the Supavision host's IP only.

The combined effect: even if an attacker manages to influence the Claude CLI prompt, the SSH channel can only execute what the target host's OS allows that user to run.

## Contributing

See [CONTRIBUTING.md](https://github.com/devsquall/supavision/blob/main/CONTRIBUTING.md) for setup, testing, and architecture conventions.

```bash
pip install -e ".[dev]"
pytest tests/ -v          # 817 tests
ruff check src/ tests/    # Linting
```

## License

[MIT](https://github.com/devsquall/supavision/blob/main/LICENSE)
