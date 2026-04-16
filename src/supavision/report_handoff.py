"""Structured report handoff mechanism (Workstream A2).

Two backends submit structured ReportPayloads into the engine:

1. **Claude CLI backend** (`_run_claude_cli`) — Claude CLI has a fixed toolset
   and does not support custom tool registration. We use a file-based handoff:
   the engine pre-allocates a temp path, the prompt preamble instructs Claude
   to `cat > {path} << 'EOF' { ... } EOF` at the very end of its run, and the
   engine reads+parses the file after the subprocess exits.

2. **OpenRouter backend** (`_run_agentic_loop`) — uses real function-calling,
   so the `submit_report` tool in `tools.py` validates and stashes the payload
   on the dispatcher directly. The engine reads it via
   `ToolDispatcher.submitted_payload` after the loop ends.

Both paths produce the same `ReportPayload | None` that downstream slices
(A3 persistence, A4 evaluator, A5 UI, A6 diff, A7 alerts) consume.

Missing/invalid payloads are **not** errors — they produce `None` and the
caller falls back to the regex evaluator path (see A4).
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

from pydantic import ValidationError

from .models.health import ReportPayload
from .report_vocab import ReportVocabulary, get_metric_names

logger = logging.getLogger(__name__)


# ── Path allocation ──────────────────────────────────────────────────


def allocate_payload_path(run_id: str) -> Path:
    """Return a per-run temp file path for the structured report payload.

    The engine creates the parent directory, instructs Claude to write to it,
    then reads it after the subprocess exits. The path is deterministic on
    run_id so retries within a single run target the same file (last write wins,
    matching R2 in the plan).
    """
    base = Path(tempfile.gettempdir()) / "supavision-reports"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"report-{run_id}.json"


def cleanup_payload_path(path: Path) -> None:
    """Remove the payload file after the engine has read it."""
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("Failed to clean up payload file %s: %s", path, e)


# ── Reading ──────────────────────────────────────────────────────────


def read_payload(path: Path) -> ReportPayload | None:
    """Read and validate a structured report payload written by Claude.

    Returns None on any failure (missing file, invalid JSON, schema mismatch).
    The caller is responsible for falling back to legacy behavior — this is
    Risk R1 and R4 in the plan: the tool may not have been called at all, or
    may have been called with garbage. Either way: return None, log, move on.
    """
    if not path.exists():
        logger.info("No structured payload at %s — falling back to legacy", path)
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Could not read payload file %s: %s", path, e)
        return None
    if not raw.strip():
        logger.info("Payload file %s is empty — falling back to legacy", path)
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("Payload JSON at %s is malformed: %s", path, e)
        return None
    try:
        return ReportPayload.model_validate(data)
    except ValidationError as e:
        logger.warning("Payload at %s failed schema validation: %s", path, e)
        return None


def read_payload_from_dict(data: dict) -> ReportPayload | None:
    """Parse a payload from an already-loaded dict (OpenRouter tool path)."""
    try:
        return ReportPayload.model_validate(data)
    except ValidationError as e:
        logger.warning("Inline payload failed schema validation: %s", e)
        return None


# ── Preamble generation ─────────────────────────────────────────────


_PREAMBLE_TEMPLATE = """\
## STRUCTURED REPORT SUBMISSION (REQUIRED)

At the very end of your investigation — after you have completed all checks \
and written your narrative analysis above — you MUST submit a structured \
report by writing a single JSON document to the following file path, using \
the Bash tool:

```
cat > {payload_path} << 'SUPAVISION_EOF'
{{
  "status": "healthy" | "warning" | "critical",
  "summary": "1-3 sentence TL;DR of the resource's current state",
  "metrics": {{
    // Numeric gauges. Use the canonical metric names listed below.
    // Omit metrics you could not measure; do not invent values.
  }},
  "issues": [
    {{
      "title": "Short human-readable title",
      "severity": "critical" | "warning" | "info",
      "evidence": "Short excerpt or command output supporting the finding",
      "recommendation": "One specific, actionable next step",
      "tags": ["primary_tag", "..."],
      "scope": "optional: filesystem path, service name, hostname, etc."
    }}
  ]
}}
SUPAVISION_EOF
```

### Rules

1. **Submit exactly once.** If you submit more than once, only the last \
submission is kept.
2. **Use canonical metric names.** For this resource type, valid metric \
names are: {metric_names}. Unknown names will be dropped. Omit metrics you \
could not actually measure — do not guess.
3. **Pick tags from this fixed vocabulary.** For this resource type, valid \
tags are: {tag_list}. The FIRST tag is the primary category and determines \
the issue's stable ID (so "disk" + scope "/var" is the same issue across \
runs even if you reword the title). Prefer specific tags; use "other" only \
as a last resort.
4. **`scope` should name the specific entity.** For disk issues, a mount \
path like `/var`. For service issues, the systemd unit name like `nginx`. \
For cert issues, the hostname. This keeps run-vs-run diffs stable.
5. **Every issue needs all six fields** (title, severity, evidence, \
recommendation, tags, scope). Evidence should be a short factual excerpt, \
not speculation. Recommendation should be one concrete action, not a list.
6. **Status rollup rule.** If any issue is `critical` → status is \
`critical`. Else if any issue is `warning` → `warning`. Else `healthy`.
7. **The JSON must be valid.** Pydantic will validate it. If validation \
fails, the payload is discarded and the report falls back to prose-only \
evaluation, which is strictly worse for the user.

You still write your full prose narrative above (commands run, findings, \
recommendations) — the structured payload complements it, it does not \
replace it.
"""


def build_preamble(payload_path: Path, vocabulary: ReportVocabulary, resource_type: str) -> str:
    """Generate the structured-report submission instructions appended to prompts.

    Injected by the engine at prompt-build time (A2). Per-template markdown
    files stay focused on *what to investigate*; the submission contract lives
    here so it can be updated in one place without editing every template.
    """
    metric_names = get_metric_names(resource_type)
    metric_display = ", ".join(f"`{n}`" for n in metric_names) if metric_names else "(none defined)"
    tag_display = ", ".join(f"`{t}`" for t in vocabulary.tag_list)
    return _PREAMBLE_TEMPLATE.format(
        payload_path=str(payload_path),
        metric_names=metric_display,
        tag_list=tag_display,
    )
