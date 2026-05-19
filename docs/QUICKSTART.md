# Supavision Quickstart

Get from zero to a monitored server with alerts in about ten minutes. This walkthrough assumes a Linux/macOS host, an SSH-reachable target, and a Claude CLI subscription. If you're using Docker, jump to [Docker mode](#docker-mode).

If anything in this guide diverges from what you see on screen, the README and CLAUDE.md are the source of truth — open an issue.

## 1. Install (2 minutes)

```bash
git clone https://github.com/devsquall/supavision
cd supavision
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Confirm:

```bash
.venv/bin/supavision --version
.venv/bin/supavision doctor
```

`doctor` checks for Claude CLI, the database path, and the templates directory. Resolve any red items before continuing.

## 2. Authenticate Claude CLI (3 minutes)

Supavision drives Claude Code via the CLI subprocess, so the CLI needs to be logged in:

```bash
claude login
```

This opens a browser. Sign in with the Claude account that owns your subscription. The credential is cached in `~/.claude/` and reused across Supavision runs.

For non-interactive environments (CI, headless servers), set `ANTHROPIC_API_KEY` instead of using `claude login`. The Claude CLI picks up that env var automatically.

## 3. Create the admin user (1 minute)

The dashboard is locked down until at least one admin user exists. Bootstrap one:

```bash
.venv/bin/supavision create-admin
```

You'll be prompted for an email, a name, and a password (min 12 chars, mixed case, number, symbol). The user lands in the local SQLite store at `.supavision/supavision.db`.

## 4. Start the server (30 seconds)

```bash
.venv/bin/supavision serve --port 8080
```

Open `http://localhost:8080`. Log in with the credentials you just created.

> **Local HTTP dev?** Set `SUPAVISION_COOKIE_SECURE=false` in `.env` first, otherwise the session cookie is HTTPS-only and your browser will refuse it.

## 5. Add your first resource (2 minutes)

From the dashboard, click **+ Add Resource** (top-right). The wizard walks through:

1. **Type & name** — pick `server` for a Linux box you can SSH to.
2. **SSH connection** — host, user, port, key path. Use **Test connection** to verify before continuing.
3. **Credentials** — for an SSH-only resource you can usually skip this. For AWS/DB/GitHub resources, supply env-var names (not raw secrets — see below).
4. **Schedule** — cron expressions. A sensible default for a new resource:
   - Discovery: `@daily` (full baseline once a day)
   - Health check: `0 */6 * * *` (every six hours)
5. **Confirm** — review and save.

You'll land back on the resource detail page. The workflow tracker at the top shows **Discovery needed** — that's your next step.

## 6. Run your first discovery (3-5 minutes)

Click **Run discovery** on the resource detail page. The session viewer opens and streams Claude's output live as it inventories the host (services, ports, mounted disks, installed packages, etc.).

When it finishes, the workflow tracker advances to **Run health check**. The report appears under the resource's run history with a structured baseline you'll see referenced in future runs.

## 7. Run a health check (1-2 minutes)

Click **Run health check**. Claude compares the live host against the discovery baseline, runs targeted probes, and produces a report with:

- **Severity** — healthy, info, warning, critical.
- **Issues** — structured list with evidence and recommended actions.
- **Metrics** — anything numeric worth tracking over time.

The dashboard `/` view now shows this resource on the action queue if anything's wrong, with one-click jump-to-issue.

## 8. Wire up alerts (2 minutes)

Pick a notification channel. Slack is the fastest:

```bash
# 1. Export the webhook URL in the Supavision process's environment.
export OPS_SLACK_WEBHOOK="https://hooks.slack.com/services/T.../B.../..."
# 2. Tell Supavision to find it there.
.venv/bin/supavision notify-configure <resource_id> \
  --slack-webhook-env-var OPS_SLACK_WEBHOOK
# 3. Send a test alert to verify.
.venv/bin/supavision notify-test <resource_id>
```

The next health check that goes from healthy → warning/critical will fire a Slack message. Subsequent runs that stay in the same severity are deduplicated.

For Teams / PagerDuty, set `resource.credentials["teams_webhook"]` or `["pagerduty_integration_key"]` to the env var name via REST API (no CLI surface yet).

## 9. Schedule it for real (1 minute)

The scheduler runs as a daemon. Start it in another terminal:

```bash
.venv/bin/supavision run-scheduler
```

It ticks every 60 seconds (configurable via `SUPAVISION_CHECK_INTERVAL`), launches jobs whose cron expressions are due, and respects per-resource locks so no resource has two concurrent runs.

If you only want one process, the scheduler also runs in-process when you use `supavision serve` — but `run-scheduler` is what you'd put in a systemd unit for production.

## Docker mode

If you'd rather not manage Python and SQLite locally:

```bash
docker compose up -d
docker exec -it supavision-supavision-1 claude login
docker exec -it supavision-supavision-1 supavision create-admin
```

Dashboard is at `http://localhost:8080`. Data persists in the `supavision-data` volume. Claude credentials persist via the `~/.claude` mount (see README).

## What to read next

- `README.md` — full configuration reference, environment variables, credentials section.
- `ARCHITECTURE.md` — pipeline design (Resource → Run → Report → Evaluation → Alert) and why it's a single pipeline.
- `SECURITY.md` — threat model, tool scoping, SSH best practices.
- `CONTRIBUTING.md` — running tests, ruff, the dev loop.

## Troubleshooting

**"Claude CLI not authenticated" in `doctor`** — run `claude login` again, or set `ANTHROPIC_API_KEY`.

**Dashboard redirects to `/landing?setup=1`** — no admin user exists yet. Run `supavision create-admin`.

**`Session cookie blocked by browser`** — you're on HTTP locally; set `SUPAVISION_COOKIE_SECURE=false` in `.env`.

**`Webhook SSRF blocked at send time`** — the webhook host doesn't resolve, resolves to a private IP, or isn't in `WEBHOOK_ALLOWED_DOMAINS`. Set the allowlist or fix DNS.

**Discovery hangs at "Connecting via SSH..."** — try `supavision doctor` first, then `ssh -v <user>@<host>` from the same shell. The Supavision SSH user needs to be able to log in non-interactively with the configured key.

**Raw secret rejected with `400 raw_secrets_in_config`** — you passed a raw value where Supavision expects an env-var name. See the Credentials section in the README.
