# Supervisor

AI-powered infrastructure monitoring that discovers what's running on your servers and watches it intelligently.

Unlike traditional monitoring tools that require you to define what to check, Supervisor uses an LLM agent with security-hardened tools to explore your infrastructure, establish baselines, and detect changes — then alerts you when something is wrong.

## How It Works

1. **Add a resource** — point Supervisor at a server, AWS account, database, or GitHub org
2. **Discovery** — Claude investigates using scoped, read-only tools. Produces a structured baseline of what "normal" looks like
3. **Health checks** — on schedule, compares current state against the baseline. Detects new issues, tracks trends
4. **Alerts** — sends Slack notifications when something is wrong. Smart dedup prevents spam

## Features

- **Web dashboard** at `https://your-domain.com` — status overview, resource management, reports
- **5 resource types** — Server, AWS Account, Database, GitHub Organization, custom
- **REST API** with API key auth and OpenAPI docs at `/docs`
- **CLI** for scripting and automation
- **Zero LLM cost** — uses Claude Code CLI (covered by Claude subscription)
- **Rule-based evaluation** — no additional API calls for severity assessment
- **Slack notifications** with smart dedup (same issue re-alerts once per day)
- **Responsive dashboard** — works on desktop and mobile

## Installation

**Prerequisites:** [Claude Code CLI](https://claude.ai/code) (included with Claude subscription)

```bash
git clone https://github.com/your-org/supervisor-ai.git
cd supervisor
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

That's it. If you have Claude Code installed, no API keys or additional configuration needed.

```bash
supervisor doctor
```

**Alternative backend:** If you prefer OpenRouter (pay-per-token), set `SUPERVISOR_BACKEND=openrouter` and `OPENROUTER_API_KEY` in `.env`.

## Quickstart

### Web UI

```bash
supervisor serve --port 8080
```

Open `http://localhost:8080` → Click **Add Resource** → choose type → fill in details → Create.

Then click **Run Discovery** on the resource page. Claude will explore the server (1-3 min) and produce a baseline. Set a schedule and Slack webhook in the Settings section.

### CLI

```bash
# Add a server
supervisor resource-add prod-web --type server \
  --config ssh_host=10.0.1.5 ssh_user=ubuntu ssh_key_path=~/.ssh/id_ed25519

# Discover what's running
supervisor run-discovery <resource_id>

# Run a health check
supervisor run-health-check <resource_id>

# Set schedule + alerts
supervisor set-schedule <resource_id> --health-check "0 */6 * * *"
supervisor notify-configure <resource_id> --slack-webhook https://hooks.slack.com/services/xxx

# Start the scheduler
supervisor run-scheduler
```

### Docker

```bash
docker compose up -d
```

Dashboard at `http://localhost:8080`. Data persists in the `supervisor-data` volume.

## CLI Reference

### Resource Management

```bash
supervisor resource-add NAME --type TYPE [--parent ID] [--config key=val ...]
supervisor resource-list
supervisor resource-show RESOURCE_ID
supervisor set-schedule RESOURCE_ID [--discovery CRON] [--health-check CRON]
supervisor add-credential RESOURCE_ID --name NAME --env-var ENV_VAR
```

### Execution

```bash
supervisor run-discovery RESOURCE_ID
supervisor run-health-check RESOURCE_ID
supervisor run-status RUN_ID
```

### Reports & Context

```bash
supervisor report-show REPORT_ID
supervisor report-list RESOURCE_ID [--type TYPE] [--limit N]
supervisor context-show RESOURCE_ID
supervisor context-diff RESOURCE_ID
supervisor checklist-show RESOURCE_ID
supervisor checklist-add RESOURCE_ID "Check X is below Y"
```

### Notifications

```bash
supervisor notify-configure RESOURCE_ID --slack-webhook URL [--webhook-url URL] [--clear]
supervisor notify-test RESOURCE_ID [--severity warning]
```

### Operations

```bash
supervisor run-scheduler          # Start cron-based scheduling
supervisor doctor                 # Health check (API key, DB, templates)
supervisor template-list          # Available resource types
supervisor purge [--days 90]      # Delete old reports/runs
```

## Adding Resource Types

Create a directory under `templates/` with two Markdown files:

```
templates/
  my_type/
    discovery.md      # Instructions for initial exploration
    health_check.md   # Instructions for recurring health checks
```

Templates use `{{placeholder}}` syntax. Available placeholders:

| Placeholder | Available In | Description |
|------------|-------------|-------------|
| `{{resource_name}}` | Both | Resource name |
| `{{resource_type}}` | Both | Resource type |
| `{{system_context}}` | Health check | Baseline from discovery |
| `{{checklist}}` | Health check | Items to verify |
| `{{recent_reports}}` | Health check | Last 3 reports for trends |
| `{{previous_context}}` | Discovery | Previous baseline (for drift) |
| `{{monitoring_requests}}` | Both | Team-added check requests |

Discovery templates must output two sections:

```
=== SYSTEM CONTEXT ===
(structured baseline goes here)

=== CHECKLIST ===
- Item 1
- Item 2
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SUPERVISOR_BACKEND` | `claude_cli` | Backend: `claude_cli` (free) or `openrouter` (API key) |
| `OPENROUTER_API_KEY` | (none) | Only needed if `SUPERVISOR_BACKEND=openrouter` |
| `SLACK_WEBHOOK` | (none) | Global fallback Slack webhook |
| `SUPERVISOR_MODEL` | `anthropic/claude-sonnet-4` | Model for investigation (openrouter only) |
| `SUPERVISOR_CHECK_INTERVAL` | `60` | Scheduler check interval (seconds) |
| `WEBHOOK_ALLOWED_DOMAINS` | (none) | Comma-separated domain allowlist for webhooks |

## License

MIT
