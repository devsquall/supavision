"""Regression test: no prompt template may reference a known-secret placeholder.

Templates are rendered with resolved credential values via templates.py. If a
prompt contains {{aws_secret_key}}, the resolved secret value ends up in the
LLM prompt body and likely in the saved run transcript. Catch any such
placeholders at the policy boundary.
"""

from __future__ import annotations

import re
from pathlib import Path

from supavision.secrets_policy import is_secret_key

PROMPT_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "src" / "supavision" / "prompt_templates"
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def test_no_prompt_template_references_known_secret_names():
    offending: list[tuple[Path, str]] = []
    for template in PROMPT_TEMPLATES_DIR.rglob("*.md"):
        text = template.read_text(encoding="utf-8")
        for match in _PLACEHOLDER_RE.finditer(text):
            placeholder = match.group(1)
            if is_secret_key(placeholder):
                offending.append((template.relative_to(PROMPT_TEMPLATES_DIR), placeholder))
    assert not offending, (
        "Prompt templates must not reference known-secret credential names directly "
        "(the resolved secret would leak into the LLM prompt and run transcript). "
        f"Found: {offending}"
    )


def test_prompt_template_dir_exists():
    # Sentinel — if the dir layout moves, the regression test silently passes.
    assert PROMPT_TEMPLATES_DIR.exists()
    assert list(PROMPT_TEMPLATES_DIR.rglob("*.md")), "expected at least one .md template"
