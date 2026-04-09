"""Regex-based security pattern scanner.

Walks a project directory, applies regex patterns, extracts code context,
and produces Finding objects. Zero cost — pure Python regex, no API calls.

This is a Lane 2 (Work) module — it produces Finding/WorkItem records
and must never import from models.health or write to the evaluations table.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from .blocklist import Blocklist
from .models import Finding, FindingSeverity

# Directories to skip
SKIP_DIRS = {
    "test", "tests", "node_modules", "vendor", ".git", "docs", "examples",
    "fixtures", "__pycache__", "dist", "build", "migrations", ".tox", ".eggs",
    "site-packages", "venv", ".venv", "env", ".env", ".mypy_cache", ".ruff_cache",
    ".pytest_cache", "egg-info", ".sweep", ".supavision",
}

SKIP_SUFFIXES = {".min.js", ".min.css", ".map", ".pyc", ".whl", ".egg", ".lock"}


def _should_skip(path: str) -> bool:
    parts = Path(path).parts
    for p in parts:
        if p in SKIP_DIRS or p.endswith(".egg-info"):
            return True
    if any(path.endswith(s) for s in SKIP_SUFFIXES):
        return True
    basename = os.path.basename(path)
    if (basename.startswith("test_") or basename.endswith("_test.py")
            or basename.endswith(".test.js") or basename.endswith(".test.ts")
            or basename.endswith(".spec.js") or basename.endswith(".spec.ts")):
        return True
    return False


def load_patterns(patterns_path: str | Path | None = None) -> list[dict]:
    """Load scanner patterns from JSON file."""
    if patterns_path is None:
        patterns_path = Path(__file__).parent / "scanner_patterns" / "patterns.json"
    with open(patterns_path) as f:
        patterns = json.load(f)
    return [p for p in patterns if p.get("enabled", True)]


def extract_context(file_path: str, line_number: int, context_lines: int = 5) -> tuple[list[str], list[str]]:
    """Read lines surrounding the match for code context."""
    try:
        with open(file_path, errors="ignore") as f:
            lines = f.readlines()
    except (OSError, UnicodeDecodeError):
        return [], []

    start = max(0, line_number - 1 - context_lines)
    end = min(len(lines), line_number + context_lines)

    before = [ln.rstrip("\n") for ln in lines[start:line_number - 1]]
    after = [ln.rstrip("\n") for ln in lines[line_number:end]]
    return before, after


def _confidence_to_severity(confidence: str) -> FindingSeverity:
    return {
        "high": FindingSeverity.HIGH,
        "medium": FindingSeverity.MEDIUM,
        "low": FindingSeverity.LOW,
    }.get(confidence, FindingSeverity.MEDIUM)


class ScanResult:
    """Aggregate scan statistics."""

    def __init__(self, resource_id: str, run_id: str = ""):
        self.resource_id = resource_id
        self.run_id = run_id
        self.total_hits: int = 0
        self.high_hits: int = 0
        self.medium_hits: int = 0
        self.low_hits: int = 0
        self.findings_created: int = 0
        self.findings_dismissed: int = 0
        self.error: str = ""

    @property
    def summary(self) -> str:
        parts = []
        if self.findings_created:
            parts.append(f"{self.findings_created} findings")
        if self.high_hits:
            parts.append(f"{self.high_hits} high")
        if self.medium_hits:
            parts.append(f"{self.medium_hits} medium")
        if self.low_hits:
            parts.append(f"{self.low_hits} low")
        if self.findings_dismissed:
            parts.append(f"{self.findings_dismissed} dismissed")
        return ", ".join(parts) if parts else "no findings"


def scan_directory(
    resource_id: str,
    directory: str,
    run_id: str = "",
    patterns: list[dict] | None = None,
    blocklist: Blocklist | None = None,
    existing_signatures: set[tuple[str, str]] | None = None,
    last_scan_at: datetime | None = None,
) -> tuple[ScanResult, list[Finding]]:
    """Scan a directory for security patterns.

    If last_scan_at is provided, only files modified after that time are scanned
    (incremental scan). Returns (ScanResult summary, list of new Findings).
    """
    if patterns is None:
        patterns = load_patterns()
    if existing_signatures is None:
        existing_signatures = set()

    result = ScanResult(resource_id=resource_id, run_id=run_id)
    findings: list[Finding] = []

    compiled_patterns = []
    for p in patterns:
        try:
            compiled_patterns.append((p, re.compile(p["pattern"])))
        except re.error:
            continue

    try:
        for root, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info")]

            for fname in files:
                filepath = os.path.join(root, fname)
                relpath = os.path.relpath(filepath, directory)

                if _should_skip(relpath):
                    continue

                # Incremental scan: skip files not modified since last scan
                if last_scan_at:
                    try:
                        mtime = os.path.getmtime(filepath)
                        if mtime < last_scan_at.timestamp():
                            continue
                    except OSError:
                        continue

                ext = os.path.splitext(fname)[1].lower()

                # Skip large files (e.g., bundled JS, data files) to limit memory use
                try:
                    if os.path.getsize(filepath) > 2 * 1024 * 1024:
                        continue
                except OSError:
                    continue

                # Pre-filter patterns by extension for this file
                file_patterns = [
                    (pd, rx) for pd, rx in compiled_patterns
                    if not pd.get("extensions") or ext in pd["extensions"]
                ]
                if not file_patterns:
                    continue

                try:
                    with open(filepath, errors="ignore") as f:
                        prev_line = ""
                        for i, line in enumerate(f, 1):
                            # Inline suppression: supavision:ignore (canonical), plus legacy aliases
                            _IGNORE_TAGS = ("supavision:ignore", "supervisor:ignore", "devos:ignore")
                            _ignore_current = any(t in line for t in _IGNORE_TAGS)
                            _ignore_prev = i > 1 and any(t in prev_line for t in _IGNORE_TAGS)

                            if not _ignore_current:
                                for pattern_def, regex in file_patterns:
                                    if _ignore_prev:
                                        continue

                                    if not regex.search(line):
                                        continue

                                    snippet = line.strip()[:200]
                                    confidence = pattern_def.get("confidence", "medium")
                                    severity = _confidence_to_severity(confidence)

                                    # Track hit counts
                                    result.total_hits += 1
                                    if confidence == "high":
                                        result.high_hits += 1
                                    elif confidence == "medium":
                                        result.medium_hits += 1
                                    else:
                                        result.low_hits += 1

                                    # Dedup check
                                    sig = (relpath, pattern_def["category"])
                                    if sig in existing_signatures:
                                        continue
                                    existing_signatures.add(sig)

                                    # Blocklist check
                                    if blocklist and blocklist.matches(
                                        pattern_def["category"],
                                        pattern_def["language"],
                                        snippet,
                                    ):
                                        result.findings_dismissed += 1
                                        continue

                                    # Extract context
                                    before, after = extract_context(filepath, i)

                                    finding = Finding(
                                        resource_id=resource_id,
                                        category=pattern_def["category"],
                                        severity=severity,
                                        language=pattern_def["language"],
                                        file_path=relpath,
                                        line_number=i,
                                        snippet=snippet,
                                        context_before=before,
                                        context_after=after,
                                        pattern_name=pattern_def.get("description", ""),
                                        run_id=run_id,
                                    )
                                    findings.append(finding)
                                    result.findings_created += 1

                            prev_line = line
                except (OSError, UnicodeDecodeError):
                    continue

    except Exception as e:
        result.error = str(e)
        return result, findings

    return result, findings
