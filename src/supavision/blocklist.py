"""Anti-pattern blocklist management.

The blocklist grows from user rejection feedback. When a user rejects a finding
as a false positive and checks "add to blocklist", the pattern signature is
captured. Future scans auto-dismiss matching patterns.

This is a Lane 2 (Work) module — it operates on Finding/BlocklistEntry models
and must never import from models.health or write to the evaluations table.
"""

from __future__ import annotations

import hashlib

from .models import BlocklistEntry, FeedbackType, Finding


def make_signature(category: str, language: str, snippet: str) -> str:
    """Generate a blocklist pattern signature.

    Format: "{category}:{language}:{snippet_hash}"
    The snippet hash uses the first 50 chars (normalized) to catch the same
    code pattern recurring across files without being too broad.
    """
    normalized = snippet.strip().lower()[:50]
    snippet_hash = hashlib.sha256(normalized.encode()).hexdigest()[:16]
    return f"{category}:{language}:{snippet_hash}"


def create_entry_from_finding(
    finding: Finding,
    description: str,
    feedback_type: FeedbackType = FeedbackType.FALSE_POSITIVE,
) -> BlocklistEntry:
    """Create a blocklist entry from a rejected finding."""
    return BlocklistEntry(
        pattern_signature=make_signature(finding.category, finding.language, finding.snippet),
        category=finding.category,
        language=finding.language,
        description=description,
        source_finding_id=finding.id,
    )


class Blocklist:
    """In-memory blocklist for fast matching during scans."""

    def __init__(self, entries: list[BlocklistEntry] | None = None):
        self._entries: dict[str, BlocklistEntry] = {}
        if entries:
            for e in entries:
                self._entries[e.pattern_signature] = e

    def matches(self, category: str, language: str, snippet: str) -> str | None:
        """Check if a scan hit matches a blocklist entry.

        Returns the entry ID if matched, None otherwise.
        """
        sig = make_signature(category, language, snippet)
        entry = self._entries.get(sig)
        if entry:
            return entry.id
        return None

    def add(self, entry: BlocklistEntry) -> None:
        self._entries[entry.pattern_signature] = entry

    def remove(self, entry_id: str) -> None:
        to_remove = [sig for sig, e in self._entries.items() if e.id == entry_id]
        for sig in to_remove:
            del self._entries[sig]

    @property
    def entries(self) -> list[BlocklistEntry]:
        return list(self._entries.values())
