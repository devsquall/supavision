# Supervisor

AI-powered infrastructure monitoring that discovers what's running on your servers and watches it intelligently.

Unlike traditional monitoring tools that require you to define what to check, Supervisor uses an LLM agent with security-hardened tools to explore your infrastructure, establish baselines, and detect changes — then alerts you when something is wrong.

## How It Works

```
You → supervisor resource-add prod-server --type server
    → supervisor run-discovery <id>          # AI explores the server
    → supervisor set-schedule <id> --health-check "0 */6 * * *"
    → supervisor notify-configure <id> --slack-webhook <url>
    → supervisor run-scheduler               # Runs health checks every 6h
```

**Discovery:** Claude investigates your server using scoped, read-only tools — gets system metrics, checks services, reads configs, tails logs. Produces a structured baseline of what "normal" looks like.

**Health checks:** Compares current state against the baseline. Detects new issues, tracks trends across runs, and evaluates severity (healthy/warning/critical).

**Alerts:** Sends Slack or webhook notifications when issues are found. Smart dedup prevents alert spam — same recurring issue only re-alerts once per day.

## Architecture

```
CLI / API → Engine (agentic tool_use loop)
                ↓
          OpenRouter API (Claude)
                ↓
          7 Scoped Tools → SSH or Local Executor
                ↓
          Report → Evaluator (keyword/LLM/hybrid)
                ↓
          Slack / Webhook Alert (if needed)
```

**Security model:** The AI agent cannot run arbitrary commands. It has access to 7 read-only tools with strict validation:

| Tool | What It Does | Safety |
|------|-------------|--------|
| `get_system_metrics` | CPU, memory, disk, processes, ports | No args needed |
| `check_service_status` | systemctl status for a service | Name validated |
| `read_file` | Read file contents (max 1000 lines) | Path validated, no `..` |
| `list_directory` | List files in a directory | Path validated |
| `check_logs` | journalctl for a service (max 500 lines) | Name validated |
| `run_diagnostic` | Allowlisted commands only | Hard allowlist |
| `query_database` | Read-only SQL queries | Write keywords blocked |

## Installation

```bash
git clone https://github.com/yourusername/supervisor.git
cd supervisor
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

Copy `.env.example` to `.env` and add your [OpenRouter API key](https://openrouter.ai/keys):

```bash
cp .env.example .env
# Edit .env with your key
```

Verify:

```bash
supervisor doctor
```

## Quickstart

### 1. Add a resource

```bash
# Monitor this server (local)
supervisor resource-add my-server --type server

# Monitor a remote server via SSH
supervisor resource-add prod-web --type server \
  --config ssh_host=10.0.1.5 ssh_user=ubuntu ssh_key_path=~/.ssh/id_ed25519
```

### 2. Run discovery

```bash
supervisor run-discovery <resource_id>
```

This takes 1-3 minutes. Claude investigates the server and produces:
- **System context** — structured baseline (services, apps, databases, ports, disk usage)
- **Checklist** — specific items to verify on every health check

View the baseline:

```bash
supervisor context-show <resource_id>
supervisor checklist-show <resource_id>
```

### 3. Run a health check

```bash
supervisor run-health-check <resource_id>
```

View the report:

```bash
supervisor report-list <resource_id>
supervisor report-show <report_id>
```

### 4. Set up alerts

```bash
# Configure Slack webhook
supervisor notify-configure <resource_id> --slack-webhook https://hooks.slack.com/services/xxx

# Test it
supervisor notify-test <resource_id>
```

### 5. Schedule automated checks

```bash
# Health check every 6 hours, discovery weekly
supervisor set-schedule <resource_id> \
  --health-check "0 */6 * * *" \
  --discovery "0 3 * * 0"

# Start the scheduler
supervisor run-scheduler
```

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
| `OPENROUTER_API_KEY` | (required) | OpenRouter API key |
| `SLACK_WEBHOOK` | (none) | Global fallback Slack webhook |
| `SUPERVISOR_MODEL` | `anthropic/claude-sonnet-4` | Model for investigation |
| `SUPERVISOR_EVAL_MODEL` | `anthropic/claude-3.5-haiku` | Model for evaluation |
| `SUPERVISOR_CHECK_INTERVAL` | `60` | Scheduler check interval (seconds) |
| `WEBHOOK_ALLOWED_DOMAINS` | (none) | Comma-separated domain allowlist for webhooks |

## License

MIT
