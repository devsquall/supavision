"""Resource type definitions — metadata for the Add Resource wizard."""

from __future__ import annotations

RESOURCE_TYPES: dict[str, dict] = {
    "server": {
        "label": "Server",
        "description": "Monitor server infrastructure health via SSH.",
        "how_it_works": (
            "Connects to your server via SSH, explores the application stack, "
            "then runs scheduled health checks examining logs, processes, databases, "
            "disk, memory, and CPU. Alerts you when issues are found."
        ),
        "connection": "ssh",
        "fields": ["ssh_host", "ssh_port", "ssh_user", "ssh_key_path"],
    },
    "aws_account": {
        "label": "AWS Account",
        "description": "Monitor AWS services — CloudWatch, Lambda, EC2, costs.",
        "how_it_works": (
            "Uses your AWS credentials locally (no SSH needed) to inventory EC2, Lambda, "
            "RDS, S3, IAM, and cost trends. Alerts on alarm states, error spikes, "
            "and cost anomalies."
        ),
        "connection": "local",
        "fields": [],
    },
    "database": {
        "label": "Database",
        "description": "Monitor database health, data integrity, and freshness.",
        "how_it_works": (
            "Connects to your database (via SSH tunnel or direct endpoint) to map schemas, "
            "track data freshness, and monitor ETL pipelines. Runs read-only SQL queries "
            "to check connection health, data freshness, and performance."
        ),
        "connection": "ssh",
        "fields": ["ssh_host", "ssh_port", "ssh_user", "ssh_key_path"],
    },
    "github_org": {
        "label": "GitHub Organization",
        "description": "Monitor GitHub org health — Actions, security alerts, PRs.",
        "how_it_works": (
            "Uses a GitHub Personal Access Token locally (no SSH needed) to monitor "
            "CI/CD workflows, track security alerts, review PR activity, and audit "
            "branch protection across your organization."
        ),
        "connection": "local",
        "fields": [],
    },
}
