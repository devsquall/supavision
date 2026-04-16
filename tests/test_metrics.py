"""Tests for the structured metrics system: schema validation, storage, and extraction."""

import pytest

from supavision.db import Store
from supavision.metric_schemas import (
    SCHEMA_VERSION,
    get_allowed_names,
    get_schema,
    validate_metrics,
)

# ── Schema validation tests ──────────────────────────────────


class TestMetricSchemas:
    def test_schema_version_is_positive(self):
        assert SCHEMA_VERSION >= 1

    def test_all_resource_types_have_schemas(self):
        for rtype in ("server", "aws_account", "database", "github_org", "codebase"):
            assert len(get_schema(rtype)) > 0, f"No schema for {rtype}"

    def test_database_subtypes_use_base_schema(self):
        """database_postgresql and database_mysql should use the 'database' schema."""
        pg = get_schema("database_postgresql")
        mysql = get_schema("database_mysql")
        base = get_schema("database")
        assert pg == base
        assert mysql == base

    def test_unknown_type_returns_empty(self):
        assert get_schema("nonexistent_type") == []

    def test_get_allowed_names_returns_set(self):
        names = get_allowed_names("server")
        assert isinstance(names, set)
        assert "cpu_percent" in names
        assert "bogus" not in names

    def test_each_schema_has_at_least_one_required(self):
        for rtype in ("server", "aws_account", "database", "github_org", "codebase"):
            schema = get_schema(rtype)
            required = [m for m in schema if m.get("required")]
            assert len(required) >= 1, f"{rtype} has no required metrics"


class TestValidateMetrics:
    def test_valid_metrics_pass(self):
        valid, warnings = validate_metrics("server", {
            "cpu_percent": 45,
            "memory_percent": 80,
            "disk_percent": 92,
        })
        assert len(valid) == 3
        assert any(m["name"] == "cpu_percent" and m["value"] == 45.0 for m in valid)

    def test_unknown_metric_rejected(self):
        valid, warnings = validate_metrics("server", {
            "cpu_percent": 45,
            "totally_fake_metric": 99,
        })
        assert len(valid) == 1
        assert any("Rejected unknown metric" in w for w in warnings)

    def test_missing_required_warned(self):
        valid, warnings = validate_metrics("server", {"cpu_percent": 45})
        missing = [w for w in warnings if "Missing required" in w]
        assert len(missing) >= 1  # memory_percent and disk_percent are also required

    def test_value_above_max_rejected(self):
        valid, warnings = validate_metrics("server", {"cpu_percent": 150})
        assert len(valid) == 0
        assert any("above maximum" in w for w in warnings)

    def test_value_below_min_rejected(self):
        valid, warnings = validate_metrics("server", {"cpu_percent": -5})
        assert len(valid) == 0
        assert any("below minimum" in w for w in warnings)

    def test_non_numeric_rejected(self):
        valid, warnings = validate_metrics("server", {"cpu_percent": "high"})
        assert len(valid) == 0
        assert any("not numeric" in w for w in warnings)

    def test_no_max_allows_large_values(self):
        """Metrics with max=None should accept any positive value."""
        valid, warnings = validate_metrics("server", {"ssh_failures_24h": 99999})
        assert len(valid) == 1

    def test_unit_from_schema_applied(self):
        valid, _ = validate_metrics("server", {"cpu_percent": 50})
        assert valid[0]["unit"] == "%"

    def test_aws_metrics(self):
        valid, _ = validate_metrics("aws_account", {
            "monthly_cost_usd": 1234.56,
            "ec2_running": 5,
        })
        assert len(valid) == 2
        assert any(m["unit"] == "USD" for m in valid)

    def test_unknown_resource_type_returns_warning(self):
        valid, warnings = validate_metrics("totally_unknown", {"foo": 1})
        assert len(valid) == 0
        assert any("No metric schema" in w for w in warnings)


# ── Storage tests ────────────────────────────────────────────


class TestMetricsStorage:
    @pytest.fixture
    def store(self, tmp_path):
        return Store(str(tmp_path / "test.db"))

    def test_save_and_get_latest(self, store):
        store.save_metrics("res-1", "report-1", [
            {"name": "cpu_percent", "value": 45.0, "unit": "%"},
            {"name": "disk_percent", "value": 82.0, "unit": "%"},
        ])
        latest = store.get_latest_metrics("res-1")
        assert latest["cpu_percent"] == 45.0
        assert latest["disk_percent"] == 82.0

    def test_latest_returns_most_recent(self, store):
        store.save_metrics("res-1", "report-1", [
            {"name": "cpu_percent", "value": 30.0, "unit": "%"},
        ])
        store.save_metrics("res-1", "report-2", [
            {"name": "cpu_percent", "value": 60.0, "unit": "%"},
        ])
        latest = store.get_latest_metrics("res-1")
        assert latest["cpu_percent"] == 60.0

    def test_history_returns_chronological(self, store):
        store.save_metrics("res-1", "r1", [{"name": "disk_percent", "value": 80.0, "unit": "%"}])
        store.save_metrics("res-1", "r2", [{"name": "disk_percent", "value": 85.0, "unit": "%"}])
        store.save_metrics("res-1", "r3", [{"name": "disk_percent", "value": 90.0, "unit": "%"}])

        history = store.get_metrics_history("res-1", "disk_percent", days=30)
        assert len(history) == 3
        values = [h["value"] for h in history]
        assert values == [80.0, 85.0, 90.0]

    def test_empty_metrics_returns_empty(self, store):
        assert store.get_latest_metrics("nonexistent") == {}
        assert store.get_metrics_history("nonexistent", "cpu_percent") == []

    def test_different_resources_isolated(self, store):
        store.save_metrics("res-1", "r1", [{"name": "cpu_percent", "value": 10.0, "unit": "%"}])
        store.save_metrics("res-2", "r2", [{"name": "cpu_percent", "value": 90.0, "unit": "%"}])

        assert store.get_latest_metrics("res-1")["cpu_percent"] == 10.0
        assert store.get_latest_metrics("res-2")["cpu_percent"] == 90.0


# ── Parser tests ─────────────────────────────────────────────


class TestMetricsParser:
    def _parse(self, text):
        from supavision.engine import Engine
        # Use __new__ to avoid __init__ validation
        engine = Engine.__new__(Engine)
        return engine._parse_metrics_section(text)

    def test_basic_parsing(self):
        text = """
Some report text here.

=== METRICS ===
cpu_percent: 45
memory_percent: 80
disk_percent: 92
"""
        result = self._parse(text)
        assert result == {"cpu_percent": 45.0, "memory_percent": 80.0, "disk_percent": 92.0}

    def test_no_metrics_section_returns_empty(self):
        assert self._parse("Just a report with no metrics.") == {}

    def test_float_values(self):
        text = "=== METRICS ===\nload_average_1m: 1.73\ndb_size_gb: 45.2"
        result = self._parse(text)
        assert result["load_average_1m"] == 1.73
        assert result["db_size_gb"] == 45.2

    def test_ignores_non_numeric_values(self):
        text = "=== METRICS ===\ncpu_percent: high\nmemory_percent: 80"
        result = self._parse(text)
        assert "cpu_percent" not in result
        assert result["memory_percent"] == 80.0

    def test_strips_trailing_units(self):
        text = "=== METRICS ===\ncpu_percent: 45 %\ndisk_percent: 92 percent"
        result = self._parse(text)
        assert result["cpu_percent"] == 45.0
        assert result["disk_percent"] == 92.0

    def test_ignores_comment_lines(self):
        text = "=== METRICS ===\n# This is a comment\ncpu_percent: 45"
        result = self._parse(text)
        assert result == {"cpu_percent": 45.0}

    def test_stops_at_next_section(self):
        text = """
=== METRICS ===
cpu_percent: 45

=== CHECKLIST ===
- Check disk space
"""
        result = self._parse(text)
        assert result == {"cpu_percent": 45.0}
