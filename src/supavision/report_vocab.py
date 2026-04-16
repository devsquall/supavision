"""Canonical vocabularies for Lane 1 structured report payloads (Workstream A).

Each resource type that opts into structured reports declares:
- `tags`: the canonical issue-tag vocabulary (used to derive stable issue IDs
  for run-vs-run set-diff). Claude MUST pick tags from this list — rolled out
  to template preambles in A2 (server) and A8 (others).
- `template_version`: stamped on every run's RunMetadata so reports remain
  traceable across template edits.

Metric names are pulled from `metric_schemas.py` directly — single source of
truth; don't duplicate them here.

A resource type that is NOT in `REPORT_VOCAB` does not get the structured
payload preamble and continues on the legacy prose-only path. This is the
per-resource-type gate mentioned in A2 (server-first) and A8 (rollout).
"""

from __future__ import annotations

from dataclasses import dataclass

from .metric_schemas import get_allowed_names


@dataclass(frozen=True)
class ReportVocabulary:
    """Canonical vocabulary for a resource type's structured report payload."""

    tags: tuple[str, ...]
    template_version: str

    @property
    def tag_list(self) -> list[str]:
        return list(self.tags)


# ── Per-resource-type vocabularies ───────────────────────────────────
#
# Add an entry here to opt a resource type into structured reports.
# A2 ships with `server` only; A8 adds aws_account, github_org,
# database_postgresql, database_mysql.

REPORT_VOCAB: dict[str, ReportVocabulary] = {
    "server": ReportVocabulary(
        tags=(
            "disk",           # filesystem capacity, inode pressure, mount issues
            "memory",         # RAM, swap, OOM events
            "cpu",            # load, saturation, steal time
            "service",        # systemd unit state, crash loops, restarts
            "network",        # listener changes, DNS, connectivity
            "security",       # firewall, sudo, unknown listeners, auth events
            "brute-force",    # SSH / auth brute-force attempts
            "cert-expiry",    # TLS certificate expiration
            "package",        # available updates, kernel, unattended-upgrades
            "logs",           # log rotation, error spikes, log shipping
            "container",      # docker/pm2 health, restart counts
            "other",          # last-resort bucket; discourage but allow
        ),
        template_version="server/v1",
    ),
    "aws_account": ReportVocabulary(
        tags=(
            "cost",           # spend spikes, idle resources, untagged charges
            "idle",            # stopped EC2 with attached volumes, unused EIPs
            "security",        # CloudTrail/GuardDuty gaps, public buckets
            "iam",             # old keys, missing MFA, overbroad policies
            "encryption",      # unencrypted EBS/RDS/S3
            "network",         # security-group 0.0.0.0, VPC flow logs
            "backup",          # RDS backup retention, snapshots
            "capacity",        # service quotas, Lambda concurrency limits
            "ec2",             # instance-level health
            "rds",             # RDS-specific
            "s3",              # S3-specific
            "lambda",          # Lambda-specific
            "other",
        ),
        template_version="aws_account/v1",
    ),
    "github_org": ReportVocabulary(
        tags=(
            "secret-scan",     # secret-scanning alerts
            "dependabot",      # open dependency CVEs
            "branch-protection",  # missing/weak protection rules
            "codeowners",      # missing or invalid CODEOWNERS
            "workflow",        # CI failures, deprecated actions
            "access",          # members without 2FA, stale tokens
            "stale",           # inactive repos / archived candidates
            "security",        # general security posture
            "other",
        ),
        template_version="github_org/v1",
    ),
    "database_postgresql": ReportVocabulary(
        tags=(
            "connections",     # saturation, pool exhaustion
            "query",           # slow queries, pg_stat_statements outliers
            "index",            # unused/missing indexes, bloat
            "maintenance",     # vacuum lag, dead tuples, XID wraparound risk
            "replication",     # replica lag, slot health
            "capacity",        # disk usage, table bloat
            "backup",          # wal-e / pgbackrest status
            "logs",            # error spikes, fatal messages
            "security",        # auth failures, exposed roles
            "other",
        ),
        template_version="database_postgresql/v1",
    ),
    "database_mysql": ReportVocabulary(
        tags=(
            "connections",     # max_connections, wait events
            "query",           # slow query log, long-running transactions
            "innodb",          # buffer pool, row locks, deadlocks
            "replication",     # replica lag, binlog health
            "binlog",          # binlog disk usage, purge lag
            "capacity",        # disk, table growth
            "backup",          # mysqldump/xtrabackup status
            "logs",            # error log spikes
            "security",        # auth, privileges
            "other",
        ),
        template_version="database_mysql/v1",
    ),
}


def supports_structured_payload(resource_type: str) -> bool:
    """Whether this resource type has opted into the structured-report preamble."""
    return resource_type in REPORT_VOCAB


def get_vocabulary(resource_type: str) -> ReportVocabulary | None:
    """Get the vocabulary for a resource type, or None if not opted in."""
    return REPORT_VOCAB.get(resource_type)


def get_metric_names(resource_type: str) -> list[str]:
    """Get canonical metric names for a resource type (from metric_schemas)."""
    return sorted(get_allowed_names(resource_type))
