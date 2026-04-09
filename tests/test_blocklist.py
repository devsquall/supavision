"""Tests for blocklist management."""

import pytest

from supavision.blocklist import Blocklist, create_entry_from_finding, make_signature
from supavision.models import BlocklistEntry, Finding, FindingSeverity


@pytest.fixture
def sample_finding():
    return Finding(
        id="find1",
        resource_id="res1",
        category="sql-injection",
        severity=FindingSeverity.HIGH,
        language="python",
        file_path="src/db.py",
        line_number=42,
        snippet='.execute(f"SELECT * FROM users WHERE id={user_id}")',
    )


def test_make_signature():
    sig = make_signature("sql-injection", "python", 'execute(f"SELECT...")')
    assert sig.startswith("sql-injection:python:")
    assert len(sig.split(":")) == 3


def test_signature_consistency():
    sig1 = make_signature("xss", "js", "innerHTML = userInput")
    sig2 = make_signature("xss", "js", "innerHTML = userInput")
    assert sig1 == sig2


def test_signature_differs_for_different_snippets():
    sig1 = make_signature("xss", "js", "innerHTML = a")
    sig2 = make_signature("xss", "js", "innerHTML = b")
    assert sig1 != sig2


def test_blocklist_matches():
    entry = BlocklistEntry(
        pattern_signature=make_signature("xss", "js", "innerHTML = x"),
        category="xss", language="js", description="Test",
    )
    bl = Blocklist([entry])
    assert bl.matches("xss", "js", "innerHTML = x") is not None
    assert bl.matches("xss", "js", "something else") is None


def test_blocklist_add_remove():
    bl = Blocklist()
    entry = BlocklistEntry(
        id="e1",
        pattern_signature="test:sig",
        category="test", language="any", description="Test",
    )
    bl.add(entry)
    assert len(bl.entries) == 1
    bl.remove("e1")
    assert len(bl.entries) == 0


def test_create_entry_from_finding(sample_finding):
    entry = create_entry_from_finding(sample_finding, "Not exploitable")
    assert entry.category == "sql-injection"
    assert entry.language == "python"
    assert entry.source_finding_id == sample_finding.id
