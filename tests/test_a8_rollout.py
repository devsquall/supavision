"""Tests for structured-payload rollout to additional resource types.

Verifies each opted-in resource type produces a preamble with the right payload
path, tag vocabulary, and metric names — and that template versions are unique.
"""

from __future__ import annotations

from pathlib import Path

from supavision.metric_schemas import get_allowed_names
from supavision.report_handoff import build_preamble
from supavision.report_vocab import (
    REPORT_VOCAB,
    get_vocabulary,
    supports_structured_payload,
)

A8_TYPES = [
    "server",
    "aws_account",
    "github_org",
    "database_postgresql",
    "database_mysql",
]


class TestRolloutCoverage:
    def test_all_expected_types_opted_in(self) -> None:
        for t in A8_TYPES:
            assert supports_structured_payload(t), f"{t} should be opted in"

    def test_each_type_has_nonempty_tag_vocabulary(self) -> None:
        for t in A8_TYPES:
            vocab = get_vocabulary(t)
            assert vocab is not None
            assert len(vocab.tag_list) >= 3, f"{t} tag vocab too thin: {vocab.tag_list}"
            assert all(isinstance(tag, str) and tag for tag in vocab.tag_list)

    def test_each_type_has_other_escape_hatch(self) -> None:
        # Every vocab includes an "other" bucket so Claude is never wedged.
        for t in A8_TYPES:
            vocab = get_vocabulary(t)
            assert vocab is not None
            assert "other" in vocab.tag_list, f"{t} missing 'other' tag"

    def test_template_versions_distinct(self) -> None:
        versions = {get_vocabulary(t).template_version for t in A8_TYPES}  # type: ignore[union-attr]
        assert len(versions) == len(A8_TYPES), "template versions must be unique"

    def test_template_version_format(self) -> None:
        # Convention: "<resource_type>/v<N>"
        for t in A8_TYPES:
            vocab = get_vocabulary(t)
            assert vocab is not None
            assert vocab.template_version.startswith(f"{t}/v"), vocab.template_version


class TestPreambleGeneration:
    def test_preamble_builds_for_every_type(self) -> None:
        for t in A8_TYPES:
            vocab = get_vocabulary(t)
            assert vocab is not None
            preamble = build_preamble(Path(f"/tmp/r-{t}.json"), vocab, t)
            assert f"/tmp/r-{t}.json" in preamble
            assert "cat >" in preamble  # file-based handoff
            for tag in vocab.tag_list:
                assert f"`{tag}`" in preamble

    def test_preamble_references_metric_schema_names(self) -> None:
        # The metric names in the preamble must come from metric_schemas.py,
        # not from a duplicated list. This catches drift if someone adds a
        # metric and forgets to regenerate.
        for t in A8_TYPES:
            vocab = get_vocabulary(t)
            assert vocab is not None
            preamble = build_preamble(Path("/tmp/x.json"), vocab, t)
            expected_metrics = get_allowed_names(t)
            # At least one metric name from the schema should appear (or the
            # "(none defined)" placeholder for types with no schema).
            if expected_metrics:
                assert any(f"`{m}`" in preamble for m in expected_metrics), (
                    f"{t} preamble missing any of the expected metric names"
                )

    def test_server_preamble_has_server_specifics(self) -> None:
        vocab = get_vocabulary("server")
        assert vocab is not None
        preamble = build_preamble(Path("/tmp/x.json"), vocab, "server")
        assert "`disk`" in preamble
        assert "`cert-expiry`" in preamble
        assert "`brute-force`" in preamble

    def test_aws_preamble_has_aws_specifics(self) -> None:
        vocab = get_vocabulary("aws_account")
        assert vocab is not None
        preamble = build_preamble(Path("/tmp/x.json"), vocab, "aws_account")
        assert "`cost`" in preamble
        assert "`iam`" in preamble
        assert "`encryption`" in preamble

    def test_github_preamble_has_github_specifics(self) -> None:
        vocab = get_vocabulary("github_org")
        assert vocab is not None
        preamble = build_preamble(Path("/tmp/x.json"), vocab, "github_org")
        assert "`secret-scan`" in preamble
        assert "`dependabot`" in preamble
        assert "`branch-protection`" in preamble

    def test_postgres_preamble_has_postgres_specifics(self) -> None:
        vocab = get_vocabulary("database_postgresql")
        assert vocab is not None
        preamble = build_preamble(Path("/tmp/x.json"), vocab, "database_postgresql")
        assert "`maintenance`" in preamble  # XID wraparound / vacuum
        assert "`replication`" in preamble

    def test_mysql_preamble_has_mysql_specifics(self) -> None:
        vocab = get_vocabulary("database_mysql")
        assert vocab is not None
        preamble = build_preamble(Path("/tmp/x.json"), vocab, "database_mysql")
        assert "`innodb`" in preamble
        assert "`binlog`" in preamble


class TestRepoVocabRegistry:
    def test_no_duplicates_across_types(self) -> None:
        # Tags may repeat across types (e.g., "security" is used in both
        # server and aws_account); this is intentional and should be allowed.
        # This test exists to document that — it does NOT assert uniqueness.
        all_tag_types: dict[str, list[str]] = {}
        for rtype, vocab in REPORT_VOCAB.items():
            for tag in vocab.tag_list:
                all_tag_types.setdefault(tag, []).append(rtype)
        # "security" and "other" should be widely shared; this is fine.
        assert "security" in all_tag_types
        assert "other" in all_tag_types

    def test_registry_is_not_empty(self) -> None:
        assert len(REPORT_VOCAB) >= len(A8_TYPES)
