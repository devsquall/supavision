"""Discovery drift detection.

Compares two SystemContext content strings (markdown) at the section level
and produces a structured diff summary.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SectionDiff:
    """A single section that changed between context versions."""

    heading: str
    change_type: str  # "added", "removed", "changed"
    old_content: str = ""
    new_content: str = ""


@dataclass
class ContextDiff:
    """Result of comparing two context versions."""

    sections: list[SectionDiff] = field(default_factory=list)
    total_added: int = 0
    total_removed: int = 0
    total_changed: int = 0

    @property
    def has_changes(self) -> bool:
        return len(self.sections) > 0

    @property
    def is_significant(self) -> bool:
        """Drift is significant if sections were added/removed,
        or more than 1 section changed content."""
        return self.total_added > 0 or self.total_removed > 0 or self.total_changed > 1

    def summary(self) -> str:
        """Human-readable summary of changes."""
        if not self.has_changes:
            return "No changes detected."

        parts = []
        added = [s for s in self.sections if s.change_type == "added"]
        removed = [s for s in self.sections if s.change_type == "removed"]
        changed = [s for s in self.sections if s.change_type == "changed"]

        if added:
            names = ", ".join(s.heading for s in added)
            parts.append(f"Added: {names}")
        if removed:
            names = ", ".join(s.heading for s in removed)
            parts.append(f"Removed: {names}")
        if changed:
            names = ", ".join(s.heading for s in changed)
            parts.append(f"Changed: {names}")

        counts = []
        if self.total_added:
            counts.append(f"{self.total_added} added")
        if self.total_removed:
            counts.append(f"{self.total_removed} removed")
        if self.total_changed:
            counts.append(f"{self.total_changed} changed")

        return "\n".join(parts) + f"\n({', '.join(counts)})"


def _normalize_key(heading: str) -> str:
    """Normalize heading for comparison: strip, lowercase, collapse whitespace."""
    return re.sub(r"\s+", " ", heading.strip().lower())


def _normalize_content(content: str) -> str:
    """Normalize content for comparison: strip trailing whitespace per line."""
    lines = [line.rstrip() for line in content.strip().splitlines()]
    return "\n".join(lines)


def _parse_sections(content: str) -> dict[str, tuple[str, str]]:
    """Parse markdown into sections.

    Returns {normalized_key: (original_heading, section_content)}.
    Content before the first heading goes under key "__preamble__".
    """
    sections: dict[str, tuple[str, str]] = {}
    current_heading = "__preamble__"
    current_lines: list[str] = []

    for line in content.splitlines():
        match = re.match(r"^(#{2,4})\s+(.+)$", line)
        if match:
            # Save previous section
            key = _normalize_key(current_heading)
            body = "\n".join(current_lines)
            if body.strip() or key != "__preamble__":
                sections[key] = (current_heading, body)

            current_heading = match.group(2).strip()
            current_lines = []
        else:
            current_lines.append(line)

    # Save last section
    key = _normalize_key(current_heading)
    body = "\n".join(current_lines)
    if body.strip() or key != "__preamble__":
        sections[key] = (current_heading, body)

    return sections


def compute_diff(current_content: str, previous_content: str) -> ContextDiff:
    """Compare two SystemContext content strings at section level."""
    current = _parse_sections(current_content)
    previous = _parse_sections(previous_content)

    diff = ContextDiff()
    all_keys = set(current.keys()) | set(previous.keys())

    for key in sorted(all_keys):
        in_current = key in current
        in_previous = key in previous

        if in_current and not in_previous:
            heading, content = current[key]
            diff.sections.append(
                SectionDiff(heading=heading, change_type="added", new_content=content)
            )
            diff.total_added += 1

        elif in_previous and not in_current:
            heading, content = previous[key]
            diff.sections.append(
                SectionDiff(heading=heading, change_type="removed", old_content=content)
            )
            diff.total_removed += 1

        else:
            # Both exist — compare content
            curr_heading, curr_content = current[key]
            _prev_heading, prev_content = previous[key]
            if _normalize_content(curr_content) != _normalize_content(prev_content):
                diff.sections.append(
                    SectionDiff(
                        heading=curr_heading,
                        change_type="changed",
                        old_content=prev_content,
                        new_content=curr_content,
                    )
                )
                diff.total_changed += 1

    return diff


def should_alert_on_drift(diff: ContextDiff) -> bool:
    """Determine if drift warrants an alert."""
    return diff.is_significant


def format_drift_summary(diff: ContextDiff, resource_name: str) -> str:
    """Format a human-readable drift report for notifications."""
    if not diff.has_changes:
        return f"No infrastructure drift detected for {resource_name}."

    header = f"Infrastructure drift detected for {resource_name}:"
    return f"{header}\n{diff.summary()}"
