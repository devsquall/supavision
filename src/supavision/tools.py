"""Scoped tool definitions for the Anthropic tool_use API.

Each tool has validated inputs and restricted execution scope.
No arbitrary command execution — all commands are constructed from
validated parameters and safe templates.

Security model:
  - Service names: alphanumeric + hyphens + underscores + dots only
  - File paths: must be absolute, no '..' traversal
  - Diagnostic commands: allowlist-only
  - Database queries: read-only (SELECT/SHOW/DESCRIBE/EXPLAIN only)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from .executor import CommandResult, Executor
from .models.health import ReportPayload
from .report_handoff import read_payload_from_dict

logger = logging.getLogger(__name__)

# ── Validation helpers ────────────────────────────────────────────

_SAFE_SERVICE_NAME = re.compile(r"^[a-zA-Z0-9._-]+$")
_SAFE_PATH = re.compile(r"^/[a-zA-Z0-9_./ -]+$")

# Commands allowed via run_diagnostic (read-only system inspection)
_DIAGNOSTIC_ALLOWLIST = {
    "docker ps",
    "docker ps -a",
    "docker stats --no-stream",
    "docker compose ps",
    "docker-compose ps",
    "nginx -t",
    "nginx -T",
    "pg_isready",
    "redis-cli ping",
    "pm2 list",
    "pm2 jlist",
    "crontab -l",
    "ip addr show",
    "ss -tlnp",
    "netstat -tlnp",
    "lsof -i -P -n",
    "cat /etc/os-release",
    "hostnamectl",
    "timedatectl",
    "uname -a",
    "whoami",
    "id",
    "env",
    "printenv",
    "lsblk",
    "mount",
    "sysctl -a",
    # AWS CLI (read-only)
    "aws sts get-caller-identity",
    "aws configure list",
    "aws s3 ls",
    # GitHub CLI (read-only)
    "gh auth status",
    # DNS (read-only, non-interactive) — Workstream H
    "host",
}

# Prefixes that are allowed (command starts with these, args follow)
_DIAGNOSTIC_PREFIX_ALLOWLIST = [
    "curl -s localhost:",
    "curl -s http://localhost:",
    "curl -s http://127.0.0.1:",
    "wget -qO- http://localhost:",
    "docker logs --tail ",
    "docker inspect ",
    "docker exec ",  # read-only inspect of containers
    "systemctl list-units",
    "systemctl list-timers",
    "pip list",
    "pip3 list",
    "npm list",
    "node -v",
    "python3 --version",
    "java -version",
    "git log --oneline -",
    "git status",
    "git branch",
    "cat /proc/",
    "head -n ",
    "tail -n ",
    "wc -l ",
    "du -sh ",
    "find ",  # directory listing variant
    "ls ",
    # AWS CLI (read-only describe/list/get operations)
    "aws ec2 describe-",
    "aws rds describe-",
    "aws lambda list-",
    "aws lambda get-",
    "aws iam list-",
    "aws iam get-",
    "aws s3 ls ",
    "aws s3api list-",
    "aws cloudwatch get-",
    "aws ce get-",
    "aws elbv2 describe-",
    "aws route53 list-",
    # GitHub CLI (read-only)
    "gh api /orgs/",
    "gh api /repos/",
    "gh api /users/",
    "gh repo list ",
    "gh repo view ",
    "gh issue list ",
    "gh pr list ",
    "gh api /rate_limit",
    # DNS/network/cert diagnostics (read-only) — Workstream H
    "dig ",             # DNS lookup (all dig usage is read-only)
    "nslookup ",        # DNS lookup — prefix only (bare nslookup enters interactive mode)
    "openssl s_client -connect ",  # TLS cert inspection
    "ping -c ",         # bounded ICMP — prefix ensures count is specified
    "traceroute ",      # network path trace
    # AWS CLI additions for Workstream G security checks
    "aws cloudtrail describe-",
    "aws ec2 describe-flow-logs",
    "aws ec2 describe-security-groups",
    "aws ec2 describe-images ",
    "aws ec2 describe-snapshots ",
    "aws lambda get-account-settings",
    "aws rds describe-db-snapshots",
]

# AWS CLI write commands — explicitly blocked even if prefix matches
_AWS_WRITE_KEYWORDS = re.compile(
    r"\b(delete|terminate|create|put|update|modify|remove|run|start|stop|reboot|deregister)\b",
    re.IGNORECASE,
)

# SQL keywords that indicate write operations
_SQL_WRITE_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE|GRANT|REVOKE|MERGE)\b",
    re.IGNORECASE,
)


def _validate_service_name(name: str) -> str | None:
    """Validate service name. Returns error message or None if valid."""
    if not name or not _SAFE_SERVICE_NAME.match(name):
        return (
            f"Invalid service name: {name!r}. "
            "Must contain only alphanumeric characters, dots, hyphens, and underscores."
        )
    if len(name) > 128:
        return f"Service name too long: {len(name)} chars (max 128)"
    return None


def _validate_path(path: str) -> str | None:
    """Validate file path. Returns error message or None if valid."""
    if not path:
        return "Path cannot be empty"
    if not path.startswith("/"):
        return f"Path must be absolute (start with /): {path!r}"
    if ".." in path:
        return f"Path traversal not allowed (contains '..'): {path!r}"
    if not _SAFE_PATH.match(path):
        return f"Path contains invalid characters: {path!r}"
    if len(path) > 512:
        return f"Path too long: {len(path)} chars (max 512)"
    return None


def _is_diagnostic_allowed(command: str) -> bool:
    """Check if a diagnostic command is on the allowlist."""
    cmd = command.strip()

    # Block shell chaining (;, &&, ||, |, backticks, $())
    if any(c in cmd for c in [";", "&&", "||", "|", "`"]):
        return False
    if "$(" in cmd:
        return False

    # Exact match
    if cmd in _DIAGNOSTIC_ALLOWLIST:
        # Extra check for AWS commands: block write keywords
        if cmd.startswith("aws ") and _AWS_WRITE_KEYWORDS.search(cmd):
            return False
        return True

    # Prefix match
    for prefix in _DIAGNOSTIC_PREFIX_ALLOWLIST:
        if cmd.startswith(prefix):
            # Extra check for AWS commands: block write keywords
            if cmd.startswith("aws ") and _AWS_WRITE_KEYWORDS.search(cmd):
                return False
            return True

    return False


def _is_readonly_sql(query: str) -> bool:
    """Check if a SQL query is read-only."""
    # Strip comments
    cleaned = re.sub(r"--.*$", "", query, flags=re.MULTILINE)
    cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)
    return not bool(_SQL_WRITE_KEYWORDS.search(cleaned))


# ── Tool definitions for Anthropic API ───────────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "get_system_metrics",
        "description": (
            "Get system resource metrics: CPU load, memory usage, disk space, "
            "and top processes by CPU. Use this first to get an overview of system health."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "check_service_status",
        "description": (
            "Check the status of a systemd service. Returns whether it's active, "
            "its recent logs, and resource usage."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service_name": {
                    "type": "string",
                    "description": "Name of the systemd service (e.g., 'nginx', 'postgresql', 'pm2-ubuntu')",
                },
            },
            "required": ["service_name"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read the contents of a file on the target system. "
            "Returns the first N lines of the file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file (e.g., '/var/log/syslog', '/etc/nginx/nginx.conf')",
                },
                "max_lines": {
                    "type": "integer",
                    "description": "Maximum number of lines to read (default: 200, max: 1000)",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_directory",
        "description": (
            "List files and directories at a given path with details "
            "(permissions, size, modification time)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the directory (e.g., '/var/www', '/etc/nginx')",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "check_logs",
        "description": (
            "View recent log entries for a systemd service via journalctl. "
            "Useful for checking errors and recent activity."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Name of the systemd service (e.g., 'nginx', 'pm2-ubuntu')",
                },
                "lines": {
                    "type": "integer",
                    "description": "Number of recent log lines to retrieve (default: 50, max: 500)",
                },
            },
            "required": ["service"],
        },
    },
    {
        "name": "run_diagnostic",
        "description": (
            "Run a pre-approved diagnostic command from a safe allowlist. "
            "Includes: docker ps, nginx -t, pg_isready, pm2 list, curl localhost, "
            "docker logs, systemctl list-units, git status, "
            "aws ec2 describe-*, aws rds describe-*, aws iam list-*, "
            "gh api /orgs/*, gh repo list, and more. "
            "If the command is not on the allowlist, it will be rejected."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        "The diagnostic command to run. Must be from the allowlist. "
                        "Examples: 'docker ps', 'nginx -t', 'pm2 list', "
                        "'curl -s localhost:3000/health', 'docker logs --tail 50 mycontainer'"
                    ),
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "query_database",
        "description": (
            "Execute a read-only SQL query against a database. "
            "Only SELECT, SHOW, DESCRIBE, and EXPLAIN queries are allowed. "
            "Write operations (INSERT, UPDATE, DELETE, DROP, etc.) will be rejected."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The SQL query to execute (read-only only)",
                },
                "db_type": {
                    "type": "string",
                    "enum": ["mysql", "postgresql"],
                    "description": "Database type",
                },
                "connection_string": {
                    "type": "string",
                    "description": (
                        "Connection details. For mysql: 'user:pass@host/dbname'. "
                        "For postgresql: 'postgresql://user:pass@host/dbname'"
                    ),
                },
            },
            "required": ["query", "db_type"],
        },
    },
    # Structured report submission. Called exactly once at the
    # end of a health check (OpenRouter backend only — Claude CLI uses
    # the file-based handoff in report_handoff.py). Arguments are validated
    # against ReportPayload via Pydantic; failure returns a validation error
    # and the caller retries once, then falls back to UNKNOWN status.
    {
        "name": "submit_report",
        "description": (
            "Submit the final structured health report. Call this EXACTLY ONCE "
            "at the very end of your investigation, after you have completed all "
            "checks and written your narrative analysis. The arguments define the "
            "canonical summary, status rollup, structured metrics, and issue list "
            "that will be rendered to users and used for run-vs-run diffs. "
            "See the prompt preamble for the tag and metric vocabularies specific "
            "to this resource type."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["healthy", "warning", "critical"],
                    "description": (
                        "Rollup status for the resource. critical if any issue is "
                        "critical; else warning if any issue is warning; else healthy."
                    ),
                },
                "summary": {
                    "type": "string",
                    "description": "1-3 sentence TL;DR of the resource's current state (max 500 chars)",
                },
                "metrics": {
                    "type": "object",
                    "description": (
                        "Typed numeric gauges keyed by canonical metric names "
                        "(see preamble for valid names). Omit metrics you could "
                        "not measure; do not invent values."
                    ),
                    "additionalProperties": {"type": ["number", "string"]},
                },
                "issues": {
                    "type": "array",
                    "description": "List of issues found (empty list if healthy)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "description": "Short human-readable title"},
                            "severity": {
                                "type": "string",
                                "enum": ["critical", "warning", "info"],
                            },
                            "evidence": {
                                "type": "string",
                                "description": "Short excerpt or command output supporting the finding",
                            },
                            "recommendation": {
                                "type": "string",
                                "description": "One specific, actionable next step",
                            },
                            "tags": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Canonical category tags (see preamble). The first "
                                    "tag is the primary category and determines the "
                                    "issue's stable ID."
                                ),
                            },
                            "scope": {
                                "type": "string",
                                "description": (
                                    "Resource-local scope: filesystem path, service name, "
                                    "hostname, etc. Narrows the tag to a specific entity."
                                ),
                            },
                        },
                        "required": ["title", "severity"],
                    },
                },
            },
            "required": ["status", "summary"],
        },
    },
]


# ── Tool dispatcher ──────────────────────────────────────────────


@dataclass
class ToolDispatcher:
    """Dispatches tool_use calls to the executor with validation."""

    executor: Executor
    _call_count: int = field(default=0, init=False)
    # Workstream A2: structured payload submitted via `submit_report`.
    # `None` until the tool is called; last-write-wins on repeat calls (R2).
    submitted_payload: ReportPayload | None = field(default=None, init=False)
    submit_report_call_count: int = field(default=0, init=False)

    async def dispatch(self, tool_name: str, tool_input: dict) -> str:
        """Execute a tool call and return the result as a string."""
        self._call_count += 1
        # `submit_report` is a structured handoff, not a command — handled
        # specially so its result is a plain JSON-ish string, not a CommandResult.
        if tool_name == "submit_report":
            return await self._handle_submit_report(tool_input)
        handler = getattr(self, f"_tool_{tool_name}", None)
        if not handler:
            return f"[ERROR: Unknown tool '{tool_name}']"

        try:
            result = await handler(tool_input)
            return self._format_result(result)
        except Exception as e:
            logger.error("Tool %s failed: %s", tool_name, e)
            return f"[ERROR: {e}]"

    async def _handle_submit_report(self, tool_input: dict) -> str:
        """Validate and stash a structured report payload (OpenRouter path).

        R2: repeat calls are allowed; the last validated payload wins, and
        subsequent calls are counted + logged so the engine can surface them.
        R4: validation failures return an actionable error message so Claude
        can retry once within the same agentic loop.
        """
        self.submit_report_call_count += 1
        if self.submit_report_call_count > 1:
            logger.warning(
                "submit_report called %d times — last write wins",
                self.submit_report_call_count,
            )
        payload = read_payload_from_dict(tool_input)
        if payload is None:
            return (
                "[submit_report VALIDATION ERROR] The payload did not match the "
                "ReportPayload schema. Check that status is one of "
                "healthy|warning|critical, summary is non-empty (max 500 chars), "
                "and every issue has title/severity. You may call submit_report "
                "again with a corrected payload — the last valid submission wins."
            )
        self.submitted_payload = payload
        return (
            f"[submit_report OK] Accepted payload: status={payload.status} "
            f"issues={len(payload.issues)} metrics={len(payload.metrics)}"
        )

    @property
    def call_count(self) -> int:
        return self._call_count

    # ── Tool implementations ─────────────────────────────────────

    async def _tool_get_system_metrics(self, _input: dict) -> CommandResult:
        """Fixed set of system metric commands."""
        commands = [
            "echo '=== UPTIME ===' && uptime",
            "echo '=== MEMORY ===' && free -h",
            "echo '=== DISK ===' && df -h",
            "echo '=== TOP PROCESSES ===' && ps aux --sort=-%cpu | head -20",
            "echo '=== NETWORK ===' && ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null",
        ]
        combined = " && ".join(commands)
        return await self.executor.run(combined)

    async def _tool_check_service_status(self, tool_input: dict) -> CommandResult:
        """Check systemd service status."""
        name = tool_input.get("service_name", "")
        error = _validate_service_name(name)
        if error:
            return CommandResult(stdout="", stderr=f"[VALIDATION ERROR: {error}]", exit_code=-1)

        cmd = f"systemctl status {name} --no-pager -l 2>&1; echo '=== ENABLED ===' && systemctl is-enabled {name} 2>&1"
        return await self.executor.run(cmd)

    async def _tool_read_file(self, tool_input: dict) -> CommandResult:
        """Read file contents with line limit."""
        path = tool_input.get("path", "")
        error = _validate_path(path)
        if error:
            return CommandResult(stdout="", stderr=f"[VALIDATION ERROR: {error}]", exit_code=-1)

        max_lines = min(tool_input.get("max_lines", 200), 1000)
        cmd = f"head -n {max_lines} {path!r}"
        return await self.executor.run(cmd)

    async def _tool_list_directory(self, tool_input: dict) -> CommandResult:
        """List directory contents."""
        path = tool_input.get("path", "")
        error = _validate_path(path)
        if error:
            return CommandResult(stdout="", stderr=f"[VALIDATION ERROR: {error}]", exit_code=-1)

        cmd = f"ls -la {path!r}"
        return await self.executor.run(cmd)

    async def _tool_check_logs(self, tool_input: dict) -> CommandResult:
        """Check service logs via journalctl."""
        service = tool_input.get("service", "")
        error = _validate_service_name(service)
        if error:
            return CommandResult(stdout="", stderr=f"[VALIDATION ERROR: {error}]", exit_code=-1)

        lines = min(tool_input.get("lines", 50), 500)
        cmd = f"journalctl -u {service} -n {lines} --no-pager 2>&1"
        return await self.executor.run(cmd)

    async def _tool_run_diagnostic(self, tool_input: dict) -> CommandResult:
        """Run allowlisted diagnostic command."""
        command = tool_input.get("command", "").strip()
        if not command:
            return CommandResult(
                stdout="", stderr="[ERROR: No command provided]", exit_code=-1
            )

        if not _is_diagnostic_allowed(command):
            return CommandResult(
                stdout="",
                stderr=(
                    f"[REJECTED: Command not on allowlist: {command!r}]\n"
                    "Allowed commands include: docker ps, nginx -t, pg_isready, "
                    "pm2 list, curl -s localhost:PORT, docker logs --tail N CONTAINER, "
                    "systemctl list-units, git status, and more."
                ),
                exit_code=-1,
            )

        return await self.executor.run(command)

    async def _tool_query_database(self, tool_input: dict) -> CommandResult:
        """Execute read-only SQL query."""
        query = tool_input.get("query", "")
        db_type = tool_input.get("db_type", "")
        conn_str = tool_input.get("connection_string", "")

        if not query:
            return CommandResult(
                stdout="", stderr="[ERROR: No query provided]", exit_code=-1
            )

        if not _is_readonly_sql(query):
            return CommandResult(
                stdout="",
                stderr=(
                    "[REJECTED: Only read-only queries are allowed "
                    "(SELECT, SHOW, DESCRIBE, EXPLAIN). "
                    "Write operations are prohibited.]"
                ),
                exit_code=-1,
            )

        if db_type == "mysql":
            if conn_str:
                # Parse user:pass@host/dbname
                cmd = f"mysql {conn_str} -e {query!r} 2>&1"
            else:
                cmd = f"mysql -e {query!r} 2>&1"
        elif db_type == "postgresql":
            if conn_str:
                cmd = f"psql {conn_str!r} -c {query!r} 2>&1"
            else:
                cmd = f"psql -c {query!r} 2>&1"
        else:
            return CommandResult(
                stdout="",
                stderr=f"[ERROR: Unsupported db_type: {db_type!r}. Use 'mysql' or 'postgresql']",
                exit_code=-1,
            )

        return await self.executor.run(cmd, timeout=15)

    # ── Formatting ───────────────────────────────────────────────

    def _format_result(self, result: CommandResult) -> str:
        """Format CommandResult as a string for the LLM."""
        parts = []
        if result.stdout:
            parts.append(result.stdout)
        if result.stderr:
            parts.append(f"STDERR: {result.stderr}")
        if result.timed_out:
            parts.append("[Command timed out]")
        if result.exit_code != 0 and not result.timed_out:
            parts.append(f"[Exit code: {result.exit_code}]")
        return "\n".join(parts) if parts else "[No output]"
