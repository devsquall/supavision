"""Metric schemas per resource type — strictly versioned and validated.

Every metric name, unit, and range is defined here. The engine validates
Claude's output against these schemas before saving to the metrics table.
Unknown metric names are rejected. Missing required metrics are logged.

Bump SCHEMA_VERSION when adding/removing/changing metric definitions.
"""

from __future__ import annotations

import logging
from typing import TypedDict

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


class MetricDef(TypedDict, total=False):
    name: str
    unit: str
    required: bool
    min: float | None
    max: float | None


METRIC_SCHEMAS: dict[str, list[MetricDef]] = {
    "server": [
        {"name": "cpu_percent", "unit": "%", "required": True, "min": 0, "max": 100},
        {"name": "memory_percent", "unit": "%", "required": True, "min": 0, "max": 100},
        {"name": "disk_percent", "unit": "%", "required": True, "min": 0, "max": 100},
        {"name": "swap_percent", "unit": "%", "required": False, "min": 0, "max": 100},
        {"name": "load_average_1m", "unit": "", "required": False, "min": 0, "max": 1000},
        {"name": "services_running", "unit": "count", "required": False, "min": 0, "max": 10000},
        {"name": "services_failed", "unit": "count", "required": False, "min": 0, "max": 10000},
        {"name": "open_ports", "unit": "count", "required": False, "min": 0, "max": 65535},
        {"name": "ssh_failures_24h", "unit": "count", "required": False, "min": 0, "max": None},
    ],
    "aws_account": [
        {"name": "monthly_cost_usd", "unit": "USD", "required": True, "min": 0, "max": None},
        {"name": "ec2_running", "unit": "count", "required": True, "min": 0, "max": None},
        {"name": "ec2_stopped", "unit": "count", "required": False, "min": 0, "max": None},
        {"name": "rds_instances", "unit": "count", "required": False, "min": 0, "max": None},
        {"name": "s3_bucket_count", "unit": "count", "required": False, "min": 0, "max": None},
        {"name": "lambda_function_count", "unit": "count", "required": False, "min": 0, "max": None},
        {"name": "iam_users", "unit": "count", "required": False, "min": 0, "max": None},
        {"name": "old_access_keys", "unit": "count", "required": False, "min": 0, "max": None},
        {"name": "unattached_volumes", "unit": "count", "required": False, "min": 0, "max": None},
        {"name": "security_groups_open", "unit": "count", "required": False, "min": 0, "max": None},
    ],
    "database": [
        {"name": "active_connections", "unit": "count", "required": True, "min": 0, "max": None},
        {"name": "max_connections", "unit": "count", "required": True, "min": 0, "max": None},
        {"name": "long_running_queries", "unit": "count", "required": False, "min": 0, "max": None},
        {"name": "db_size_gb", "unit": "GB", "required": True, "min": 0, "max": None},
        {"name": "dead_tuples_percent", "unit": "%", "required": False, "min": 0, "max": 100},
        {"name": "replication_lag_seconds", "unit": "seconds", "required": False, "min": 0, "max": None},
        {"name": "unused_indexes", "unit": "count", "required": False, "min": 0, "max": None},
        {"name": "slow_queries_24h", "unit": "count", "required": False, "min": 0, "max": None},
        {"name": "cache_hit_ratio", "unit": "%", "required": False, "min": 0, "max": 100},
    ],
    "github_org": [
        {"name": "total_repos", "unit": "count", "required": True, "min": 0, "max": None},
        {"name": "repos_unprotected", "unit": "count", "required": False, "min": 0, "max": None},
        {"name": "dependabot_alerts_critical", "unit": "count", "required": False, "min": 0, "max": None},
        {"name": "dependabot_alerts_high", "unit": "count", "required": False, "min": 0, "max": None},
        {"name": "workflow_failures_7d", "unit": "count", "required": False, "min": 0, "max": None},
        {"name": "members_without_2fa", "unit": "count", "required": False, "min": 0, "max": None},
    ],
    "codebase": [
        {"name": "total_findings", "unit": "count", "required": True, "min": 0, "max": None},
        {"name": "critical_findings", "unit": "count", "required": False, "min": 0, "max": None},
        {"name": "high_findings", "unit": "count", "required": False, "min": 0, "max": None},
        {"name": "files_scanned", "unit": "count", "required": False, "min": 0, "max": None},
    ],
}


def get_schema(resource_type: str) -> list[MetricDef]:
    """Get the metric schema for a resource type. Returns empty list if unknown."""
    # Database subtypes (database_postgresql, database_mysql) use the base 'database' schema
    effective_type = resource_type
    if resource_type.startswith("database_"):
        effective_type = "database"
    return METRIC_SCHEMAS.get(effective_type, [])


def get_allowed_names(resource_type: str) -> set[str]:
    """Get the set of allowed metric names for a resource type."""
    return {m["name"] for m in get_schema(resource_type)}


def validate_metrics(
    resource_type: str, raw_metrics: dict[str, float]
) -> tuple[list[dict], list[str]]:
    """Validate raw metrics against the schema for a resource type.

    Returns:
        (valid_metrics, warnings) where valid_metrics is list of {name, value, unit}
        and warnings is list of issues (unknown names, missing required, out of range).
    """
    schema = get_schema(resource_type)
    if not schema:
        return [], [f"No metric schema defined for resource type '{resource_type}'"]

    allowed = {m["name"]: m for m in schema}
    valid: list[dict] = []
    warnings: list[str] = []

    # Validate submitted metrics
    for name, value in raw_metrics.items():
        if name not in allowed:
            warnings.append(f"Rejected unknown metric '{name}' (not in {resource_type} schema)")
            continue

        defn = allowed[name]

        # Type check
        if not isinstance(value, (int, float)):
            warnings.append(f"Metric '{name}' value {value!r} is not numeric, skipping")
            continue

        # Range check
        min_val = defn.get("min")
        max_val = defn.get("max")
        if min_val is not None and value < min_val:
            warnings.append(f"Metric '{name}' value {value} below minimum {min_val}")
            continue
        if max_val is not None and value > max_val:
            warnings.append(f"Metric '{name}' value {value} above maximum {max_val}")
            continue

        valid.append({"name": name, "value": float(value), "unit": defn.get("unit", "")})

    # Check for missing required metrics
    for defn in schema:
        if defn.get("required") and defn["name"] not in raw_metrics:
            warnings.append(f"Missing required metric '{defn['name']}' for {resource_type}")

    return valid, warnings
