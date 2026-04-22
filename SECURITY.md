# Security

Supavision runs AI agents that execute commands on your infrastructure. This document explains the security model, what's protected, and how to deploy safely.

## Reporting Vulnerabilities

If you discover a security vulnerability, please report it responsibly:

- **Email:** security@devsquall.com
- **GitHub:** Open a [Security Advisory](https://github.com/devsquall/supavision/security/advisories/new) (private by default)

Do not open public issues for security vulnerabilities.

## Threat Model

### What Supavision Does

Supavision uses Claude Code to investigate your infrastructure via SSH and produce health check reports. This means:

1. **Infrastructure agents** connect to your monitored hosts via SSH and run read-only diagnostic commands
2. **Reports are stored in the SQLite database** and may contain server configuration details

There are no code-modification features. Supavision is read-only by design.

### Credential Management

**Credentials are never stored in the database.** The `Credential` model stores only environment variable names (e.g., `AWS_ACCESS_KEY_ID`). Actual secret values are resolved at runtime from the process environment.

This means:
- Database backups don't contain secrets
- SQLite files can be copied without exposing credentials
- Credential rotation only requires changing environment variables

### Agent Tool Scoping

Supavision supports two LLM backends. Tool scoping differs between them — read this carefully.

#### OpenRouter backend (`SUPAVISION_BACKEND=openrouter`)

Agents are exposed to a strict custom toolbox defined in `tools.py`:

| Resource Type | Operation | Tool Access |
|--------------|-----------|-------------|
| Server | Discovery, Health Check | `get_system_metrics`, `check_service_status`, `read_file`, `list_directory`, `check_logs`, `run_diagnostic` (pre-approved commands only) |
| Database | Discovery, Health Check | `query_database` (SELECT only), `get_system_metrics`, `run_diagnostic` |
| AWS Account | Discovery, Health Check | `run_diagnostic` limited to read-only AWS CLI commands |
| GitHub Org | Discovery, Health Check | `run_diagnostic` limited to `gh` CLI read-only queries |

`run_diagnostic` enforces an allowlist in `tools.py` (`_DIAGNOSTIC_ALLOWLIST` and `_DIAGNOSTIC_PREFIX_ALLOWLIST`). Arbitrary shell commands are rejected.

#### Claude Code CLI backend (default, `SUPAVISION_BACKEND=claude_cli`)

Agents run via the Claude Code CLI subprocess with `--allowedTools "Bash(*) Read Glob Grep"` and `--permission-mode auto`. **This means:**

- The LLM can execute any Bash command on the Supavision host, including `ssh` to reach your monitored servers
- The `run_diagnostic` allowlist in `tools.py` is **not enforced** on this path — it only applies to the OpenRouter backend
- The Supavision host's filesystem, environment, and network are therefore the effective security boundary for the CLI subprocess
- The SSH user on each monitored host is the effective security boundary for what the agent can do on that host

**If you deploy the default Claude CLI backend, the SSH scoping guidance in the [main README](README.md#deploying-ssh-access-safely) is load-bearing, not optional.** A dedicated read-only SSH user with no sudo and an `authorized_keys` `command="..."` restriction is what keeps blast radius small on the target host.

Infrastructure agents — on either backend — are designed to be read-only and will not be prompted to modify files, alter services, or escalate privileges. Prompt injection via attacker-influenced free-text fields (resource names, `monitoring_requests`) remains a risk. The `tests/test_prompt_injection.py` file locks in the current regression guards.

### Prompt Injection Mitigation

Natural-language input flows into agent prompts via:
- Resource `name` and `config` values (admin-set)
- Resource `monitoring_requests` (admin-set, free-text)
- Resource `resource_type` (validated against `^[a-zA-Z0-9_-]+$` — no shell metachars, no path traversal)

All `monitoring_requests` write paths require admin role server-side (`_require_admin` in `web/dashboard/resources.py`). Viewers cannot submit free-text that reaches the agent. The create-resource API schema does not accept `monitoring_requests` at all — the field is only reachable through the dashboard, which is session-auth-gated.

Regression tests in `tests/test_prompt_injection.py` lock in:
- The `--allowedTools` string cannot silently widen to include network/pivot commands (`curl`, `wget`, `nc`, `ssh:*`, `scp`)
- The API create-resource schema never accepts `monitoring_requests`
- Length limits remain enforced (500 char / 50 item caps)
- `Resource.resource_type` rejects path traversal and shell metachars

A 19-payload adversarial corpus is committed in the same file and grows over time. This is regression protection, not guaranteed defense — the Claude CLI backend's `Bash(*)` allowlist is the primary real limit, and the SSH user's OS-level permissions are the secondary. See the two-backend table above.

### Network Security

**SSRF Protection:** All webhook URLs are validated against blocked IP ranges before HTTP requests are sent:
- Private ranges: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`
- Loopback: `127.0.0.0/8`
- Link-local + AWS metadata: `169.254.0.0/16`
- IPv6 private: `fc00::/7`, `fe80::/10`

DNS resolution is performed at both configuration time and send time to prevent DNS rebinding attacks.

Optional domain allowlist via `WEBHOOK_ALLOWED_DOMAINS` environment variable.

## Authentication & Authorization

### Session-Based Auth (Dashboard)

The web dashboard uses session-based authentication:

- **Password hashing:** scrypt (n=16384, r=8, p=1) with random 16-byte salt and constant-time comparison
- **Session cookies:** Cryptographically random session IDs stored server-side
- **CSRF protection:** Per-session CSRF tokens validated on all POST/PUT/DELETE/PATCH requests (accepts `x-csrf-token` header or form field)
- **Session timeouts:** Configurable absolute expiry (`SUPAVISION_SESSION_HOURS`, default 8h) and idle timeout (`SUPAVISION_SESSION_IDLE_MINUTES`, default 120min)
- **Secure cookies:** `Secure` flag enabled by default (`SUPAVISION_COOKIE_SECURE=true`); set to `false` only for local HTTP development

Bootstrap authentication with `supavision create-admin`. The legacy `SUPAVISION_PASSWORD` environment variable is deprecated and only used for backward-compatible auto-migration of existing deployments.

### Role-Based Access Control

Two roles exist: **admin** and **viewer**.

| Action | Admin | Viewer |
|--------|-------|--------|
| View dashboards, reports, metrics | Yes | Yes |
| View activity, sessions, schedules, alerts | Yes | Yes |
| Create, edit, delete resources | Yes | No |
| Trigger discovery and health checks | Yes | No |
| Edit resource `monitoring_requests` (natural-language input to the agent) | Yes | No |
| Manage schedules and notifications | Yes | No |
| Create and revoke API keys | Yes | No |
| Manage users (create, deactivate, change roles) | Yes | No |

RBAC is enforced server-side on both dashboard routes and REST API endpoints. UI elements are also hidden for viewer users, but the server-side check is the authoritative gate.

### API Authentication

All `/api/v1/*` endpoints (except `/api/v1/health`) require an API key in the `x-api-key` header. Keys are stored as SHA-256 hashes in the database.

- **Admin API keys** can perform all operations (create, delete, trigger)
- **Viewer API keys** can only read data (list resources, get reports, get metrics)
- Generate keys via `supavision api-key-create` (CLI, always admin) or the Settings page (inherits creating user's role)

### MCP Server

The MCP server opens the database in read-only mode. It cannot modify any data.

### Rate Limiting

Per-IP rate limits are enforced on sensitive endpoints:
- Login: 5 requests/minute
- Trigger operations (discover, health-check, evaluate): 10 requests/minute

Rate limits are in-memory and reset on server restart.

## Database

- SQLite with WAL (Write-Ahead Logging) mode for safe concurrent access
- All queries use parameterized statements (no SQL injection)
- No credentials stored (only env var references)
- Database file permissions set to 0600 (owner read/write only)
- Database directory permissions set to 0700 (owner only)
- Reports and evaluations contain server configuration details — treat the database file as sensitive

## Deployment Recommendations

1. **Create an admin user:** `supavision create-admin`
2. **Bind to localhost:** Run `supavision serve --host 127.0.0.1` and use a reverse proxy (nginx) for TLS termination
3. **Enable secure cookies:** Keep `SUPAVISION_COOKIE_SECURE=true` (default) when serving over HTTPS
4. **Configure session timeouts:** Adjust `SUPAVISION_SESSION_HOURS` and `SUPAVISION_SESSION_IDLE_MINUTES` for your security requirements
5. **Restrict database permissions:** `chmod 600 .supavision/supavision.db`
6. **Harden SSH access** per the [Deploying SSH access safely](README.md#deploying-ssh-access-safely) section of the README — dedicated read-only user, no sudo, optional `authorized_keys` `command="..."` restriction. **This is load-bearing on the default Claude CLI backend.**
7. **Review agent output periodically:** Health check reports describe your server state; treat them as sensitive
8. **Use API keys for automation:** Generate keys via CLI or Settings, store them securely
9. **Monitor rate limits:** The dashboard surfaces rate-limit hits in the activity log — unusual login failures should prompt an IP review

## What's NOT Protected

- **Reports contain server details.** Health check reports describe your server configuration, running services, open ports, and disk usage. Anyone with database access can read this.
- **No encryption at rest.** The SQLite database is not encrypted. Use filesystem-level encryption if needed.
- **Rate limits are in-memory.** They reset on server restart and don't work across multiple instances.
