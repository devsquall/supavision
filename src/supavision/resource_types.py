"""Resource type definitions — metadata for the Add Resource wizard.

Each type defines its wizard flow, UI labels, and connection method.
The wizard uses WIZARD_FLOWS to determine step sequence per type.
"""

from __future__ import annotations

RESOURCE_TYPES: dict[str, dict] = {
    "server": {
        "label": "Server",
        "icon": "server",
        "description": "Monitor server infrastructure health via SSH.",
        "how_it_works": (
            "Supavision will generate an SSH key for this server, connect via SSH, "
            "and explore the target directory to learn your application stack. "
            "It then runs scheduled health checks examining logs, processes, databases, "
            "disk, memory, and CPU. Alerts you when issues are found."
        ),
        "connection": "ssh",
    },
    "aws_account": {
        "label": "AWS Account",
        "icon": "cloud",
        "description": "Monitor AWS services — CloudWatch, Lambda, EC2, costs.",
        "how_it_works": (
            "Uses your AWS credentials locally (no SSH needed) to inventory and monitor "
            "EC2, Lambda, RDS, S3, IAM, and cost trends. Alerts on alarm states, "
            "error spikes, and cost anomalies."
        ),
        "connection": "credentials",
    },
    "github_org": {
        "label": "GitHub Organization",
        "icon": "github",
        "description": "Monitor GitHub org health — Actions, security alerts, PRs.",
        "how_it_works": (
            "Uses a GitHub Personal Access Token locally (no SSH needed) to monitor "
            "CI/CD workflows, track security alerts, review PR activity, and audit "
            "branch protection across your organization."
        ),
        "connection": "credentials",
    },
    "database": {
        "label": "Database",
        "icon": "database",
        "description": "Monitor database health, data integrity, and freshness.",
        "how_it_works": (
            "Connects to your database (via SSH tunnel or direct endpoint) to map schemas, "
            "track data freshness, and monitor ETL pipelines. Runs read-only SQL queries "
            "to check connection health, data freshness, and performance."
        ),
        "connection": "ssh",
    },
}


# Wizard step flows per resource type.
# Each tuple: (display_label, template_suffix) — renders _wizard_{suffix}.html

WIZARD_FLOWS: dict[str, list[tuple[str, str]]] = {
    "server": [
        ("Resource Info", "resource_info"),
        ("SSH Key", "ssh_key"),
        ("Test Connection", "test_connection"),
        ("Schedule", "schedule"),
        ("Confirm", "confirm"),
    ],
    "aws_account": [
        ("Resource Info", "resource_info"),
        ("Credentials", "credentials"),
        ("Schedule", "schedule"),
        ("Confirm", "confirm"),
    ],
    "github_org": [
        ("Resource Info", "resource_info"),
        ("Credentials", "credentials"),
        ("Schedule", "schedule"),
        ("Confirm", "confirm"),
    ],
    "database": [
        ("Resource Info", "resource_info"),
        ("Connection", "db_connection"),
        ("Test Connection", "test_connection"),
        ("Schedule", "schedule"),
        ("Confirm", "confirm"),
    ],
}
