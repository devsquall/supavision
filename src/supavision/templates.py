"""Template loading and placeholder resolution.

Templates are Markdown files with {{placeholder}} tokens. The engine is
intentionally dumb: read the file, str.replace on {{placeholder}}, return.
No Jinja, no logic blocks. If you need conditional logic, write two templates.

Placeholders are resolved from three sources (in order):
  1. resource.config — e.g., {{region}}, {{account_id}}
  2. Resolved credentials — e.g., {{aws_access_key}} (from env var at runtime)
  3. Runtime context — e.g., {{system_context}}, {{checklist}}, {{recent_reports}}
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .models import Credential, Resource, RunType

TEMPLATE_DIR_DEFAULT = str(Path(__file__).parent / "prompt_templates")


def load_template(
    resource_type: str,
    run_type: RunType,
    template_dir: str | Path = TEMPLATE_DIR_DEFAULT,
    config: dict | None = None,
) -> str:
    """Load a template file from disk.

    Returns the raw template with unresolved {{placeholders}}.
    Raises FileNotFoundError if the template doesn't exist.

    For database resources, checks config["db_engine"] and uses engine-specific
    templates (e.g., database_postgresql/) with fallback to generic database/.
    """
    subdir = "discovery.md" if run_type == RunType.DISCOVERY else "health_check.md"

    # Engine-specific template routing for databases
    effective_type = resource_type
    if resource_type == "database" and config and config.get("db_engine"):
        engine_type = f"database_{config['db_engine']}"
        engine_path = Path(template_dir) / engine_type / subdir
        if engine_path.exists():
            effective_type = engine_type

    path = Path(template_dir) / effective_type / subdir

    # Prevent path traversal — ensure resolved path stays within template_dir
    if not path.resolve().is_relative_to(Path(template_dir).resolve()):
        raise ValueError(
            f"Path traversal detected: resource_type {resource_type!r} "
            f"would escape template directory"
        )

    if not path.exists():
        raise FileNotFoundError(
            f"Template not found: {path}. "
            f"Expected at: {path.resolve()}"
        )

    return path.read_text(encoding="utf-8")


def resolve_credentials(
    resource: Resource,
    inherited_credentials: dict[str, Credential] | None = None,
) -> dict[str, str]:
    """Resolve credential env var references to actual values.

    Merges inherited (parent) credentials with resource's own credentials.
    Child credentials override parent on name collision.
    Returns {credential_name: actual_value}.
    Missing env vars are returned as empty string with a warning prefix.
    """
    merged: dict[str, Credential] = {}

    if inherited_credentials:
        merged.update(inherited_credentials)

    # Child overrides parent
    merged.update(resource.credentials)

    resolved: dict[str, str] = {}
    for name, cred in merged.items():
        value = os.environ.get(cred.env_var, "")
        if not value:
            resolved[name] = f"[MISSING: env var {cred.env_var} not set]"
        else:
            resolved[name] = value

    return resolved


def resolve_template(
    template: str,
    resource: Resource,
    credentials: dict[str, str],
    runtime_context: dict[str, str] | None = None,
) -> str:
    """Replace all {{placeholder}} tokens in a template with actual values.

    Resolution order:
      1. resource.config values
      2. Resolved credentials
      3. Runtime context (system_context, checklist, recent_reports, etc.)

    Unresolved placeholders are left as-is (they'll be visible in the output
    as a signal that something was expected but not provided).
    """
    context: dict[str, str] = {}

    # Layer 1: resource config
    context.update(resource.config)

    # Layer 2: credentials
    context.update(credentials)

    # Layer 3: runtime context
    if runtime_context:
        context.update(runtime_context)

    # Also add resource metadata
    context.setdefault("resource_name", resource.name)
    context.setdefault("resource_type", resource.resource_type)
    context.setdefault("resource_id", resource.id)

    # Replace {{key}} with value
    def replacer(match: re.Match) -> str:
        key = match.group(1).strip()
        return context.get(key, match.group(0))  # Leave unresolved as-is

    return re.sub(r"\{\{(\s*\w+\s*)\}\}", replacer, template)


def list_templates(template_dir: str | Path = TEMPLATE_DIR_DEFAULT) -> list[dict[str, str]]:
    """List all available template sets (resource types that have templates)."""
    tdir = Path(template_dir).resolve()
    if not tdir.exists():
        return []

    results: list[dict[str, str]] = []
    for subdir in sorted(tdir.iterdir()):
        if not subdir.is_dir():
            continue
        has_discovery = (subdir / "discovery.md").exists()
        has_health = (subdir / "health_check.md").exists()
        if has_discovery or has_health:
            results.append({
                "resource_type": subdir.name,
                "discovery": "yes" if has_discovery else "no",
                "health_check": "yes" if has_health else "no",
            })

    return results
