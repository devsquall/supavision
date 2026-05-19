"""Centralised policy for secret-shaped fields and credential env-var references.

Every code path that accepts user-supplied resource config or credentials must
import from this module so the rules stay in one place.

Two checks:

- `validate_no_raw_secrets(config)` — config keys must not look like secrets.
  Secrets belong in `Resource.credentials` as env-var references, not in
  `Resource.config` as plaintext values.
- `validate_credentials_env_vars(credentials)` — values supplied for credential
  references must look like environment variable names. Otherwise a caller
  could write a raw secret into the credentials slot and persist it under a
  different name.

`ssh_key_path` is deliberately NOT a secret here: it is a filesystem path
consumed by the SSH executor, not key material.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

KNOWN_SECRET_KEYS: frozenset[str] = frozenset(
    {
        "aws_secret_key",
        "aws_access_key",
        "github_token",
        "db_password",
        "slack_webhook",
        # Notification channel credentials. URLs are credentials (anyone with
        # the URL can post to the channel); the PagerDuty integration key is
        # the routing key for Events API v2.
        "webhook_url",
        "teams_webhook",
        "pagerduty_integration_key",
    }
)

_SECRET_SUFFIXES: tuple[str, ...] = ("_secret", "_token", "_password", "_api_key")

# Identifiers that look secret-shaped by suffix but are framework/UI tokens —
# not credential material. Add carefully; the cost of a false negative is real.
_NON_SECRET_ALLOWLIST: frozenset[str] = frozenset(
    {
        "csrf_token",  # CSRF protection token, posted by every form
    }
)

_ENV_VAR_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def is_secret_key(name: str) -> bool:
    """Return True if `name` looks like it should hold a secret value."""
    if name in _NON_SECRET_ALLOWLIST:
        return False
    if name in KNOWN_SECRET_KEYS:
        return True
    return any(name.endswith(suffix) for suffix in _SECRET_SUFFIXES)


def is_valid_env_var_name(name: str) -> bool:
    """Return True if `name` is a syntactically valid env var reference."""
    return bool(_ENV_VAR_NAME_RE.match(name))


def validate_no_raw_secrets(config: Mapping[str, object] | None) -> list[str]:
    """Return the list of config keys that look like secrets and have a value set.

    Empty/None values are ignored — a blank field in a form is not a leak.
    Callers reject the request if the returned list is non-empty.
    """
    if not config:
        return []
    offenders: list[str] = []
    for key, value in config.items():
        if not is_secret_key(key):
            continue
        if value is None:
            continue
        if isinstance(value, str) and value.strip() == "":
            continue
        offenders.append(key)
    return offenders


def validate_credentials_env_vars(
    credentials: Mapping[str, str] | None,
) -> list[str]:
    """Return the list of credential names whose values are not env-var-shaped.

    Each value in `credentials` should be the NAME of an environment variable
    (e.g. ``AWS_SECRET_ACCESS_KEY``), not the secret value itself. Reject any
    value that doesn't match ``^[A-Z_][A-Z0-9_]*$``.
    """
    if not credentials:
        return []
    bad: list[str] = []
    for name, env_var in credentials.items():
        if env_var is None:
            bad.append(name)
            continue
        if not isinstance(env_var, str):
            bad.append(name)
            continue
        if env_var.strip() == "":
            continue
        if not is_valid_env_var_name(env_var.strip()):
            bad.append(name)
    return bad


def secret_keys_present(config: Mapping[str, object] | None) -> list[str]:
    """Return secret-shaped keys that exist (any value, including empty) in config.

    Used by the edit UI to decide whether to render the legacy-secrets banner.
    """
    if not config:
        return []
    return [k for k in config if is_secret_key(k)]


__all__: Iterable[str] = (
    "KNOWN_SECRET_KEYS",
    "is_secret_key",
    "is_valid_env_var_name",
    "secret_keys_present",
    "validate_credentials_env_vars",
    "validate_no_raw_secrets",
)
