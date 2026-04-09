"""Tests for the regex scanner."""

from datetime import datetime, timedelta, timezone

from supavision.scanner import (
    _should_skip,
    extract_context,
    load_patterns,
    scan_directory,
)


class TestPatterns:
    def test_load_patterns(self):
        patterns = load_patterns()
        assert len(patterns) > 60
        categories = {p["category"] for p in patterns}
        assert "code-injection" in categories
        assert "sql-injection" in categories
        assert "shell-injection" in categories

    def test_all_patterns_have_required_fields(self):
        patterns = load_patterns()
        for p in patterns:
            assert "category" in p
            assert "language" in p
            assert "pattern" in p
            assert "confidence" in p


class TestSkipLogic:
    def test_skip_test_files(self):
        assert _should_skip("test_foo.py")
        assert _should_skip("foo_test.py")
        assert _should_skip("foo.test.js")
        assert _should_skip("foo.spec.ts")

    def test_skip_dirs(self):
        assert _should_skip("node_modules/foo.js")
        assert _should_skip("vendor/bar.php")
        assert _should_skip(".git/config")

    def test_skip_suffixes(self):
        assert _should_skip("bundle.min.js")
        assert _should_skip("style.min.css")

    def test_normal_files_not_skipped(self):
        assert not _should_skip("src/main.py")
        assert not _should_skip("lib/utils.js")


class TestScanDirectory:
    def test_scan_finds_eval(self, tmp_path):
        (tmp_path / "danger.py").write_text("result = eval(user_input)\n")
        result, findings = scan_directory(resource_id="r1", directory=str(tmp_path))
        assert not result.error
        assert len(findings) >= 1
        assert any(f.category == "code-injection" for f in findings)

    def test_scan_finds_sql_injection(self, tmp_path):
        (tmp_path / "db.py").write_text(
            'cursor.execute(f"SELECT * FROM users WHERE id={uid}")\n'
        )
        result, findings = scan_directory(resource_id="r1", directory=str(tmp_path))
        assert any(f.category == "sql-injection" for f in findings)

    def test_scan_skips_test_files(self, tmp_path):
        (tmp_path / "test_danger.py").write_text("eval(x)\n")
        result, findings = scan_directory(resource_id="r1", directory=str(tmp_path))
        assert len(findings) == 0

    def test_scan_dedup(self, tmp_path):
        (tmp_path / "a.py").write_text("eval(x)\neval(y)\n")
        result, findings = scan_directory(resource_id="r1", directory=str(tmp_path))
        code_injection = [f for f in findings if f.category == "code-injection"]
        assert len(code_injection) == 1

    def test_scan_empty_dir(self, tmp_path):
        result, findings = scan_directory(resource_id="r1", directory=str(tmp_path))
        assert not result.error
        assert len(findings) == 0

    def test_findings_have_resource_id(self, tmp_path):
        (tmp_path / "danger.py").write_text("result = eval(user_input)\n")
        result, findings = scan_directory(resource_id="res-123", directory=str(tmp_path), run_id="run-456")
        assert len(findings) >= 1
        assert all(f.resource_id == "res-123" for f in findings)
        assert all(f.run_id == "run-456" for f in findings)

    def test_scan_result_summary(self, tmp_path):
        (tmp_path / "danger.py").write_text("result = eval(user_input)\n")
        result, findings = scan_directory(resource_id="r1", directory=str(tmp_path))
        assert "finding" in result.summary


class TestExtractContext:
    def test_context_extraction(self, tmp_path):
        content = "\n".join(f"line {i}" for i in range(20))
        f = tmp_path / "test.py"
        f.write_text(content)
        before, after = extract_context(str(f), 10, context_lines=3)
        assert len(before) == 3
        assert len(after) == 3


class TestInlineSuppression:
    def test_supavision_ignore_inline(self, tmp_path):
        (tmp_path / "vuln.py").write_text("eval(input())  # supavision:ignore\n")
        result, findings = scan_directory(resource_id="r1", directory=str(tmp_path))
        assert len(findings) == 0

    def test_supervisor_ignore_backward_compat(self, tmp_path):
        """Backward compat: supervisor:ignore still works."""
        (tmp_path / "vuln.py").write_text("eval(input())  # supervisor:ignore\n")
        result, findings = scan_directory(resource_id="r1", directory=str(tmp_path))
        assert len(findings) == 0

    def test_devos_ignore_backward_compat(self, tmp_path):
        """Backward compat: devos:ignore still works."""
        (tmp_path / "vuln.py").write_text("eval(input())  # devos:ignore\n")
        result, findings = scan_directory(resource_id="r1", directory=str(tmp_path))
        assert len(findings) == 0

    def test_ignore_line_above(self, tmp_path):
        (tmp_path / "vuln.py").write_text("# supavision:ignore\neval(input())\n")
        result, findings = scan_directory(resource_id="r1", directory=str(tmp_path))
        assert len(findings) == 0

    def test_no_ignore_still_detects(self, tmp_path):
        (tmp_path / "vuln.py").write_text("eval(input())\n")
        result, findings = scan_directory(resource_id="r1", directory=str(tmp_path))
        assert len(findings) > 0


class TestIncrementalScan:
    def test_skips_old_files(self, tmp_path):
        (tmp_path / "old.py").write_text("eval(input())\n")
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        result, findings = scan_directory(resource_id="r1", directory=str(tmp_path), last_scan_at=future)
        assert len(findings) == 0

    def test_scans_new_files(self, tmp_path):
        (tmp_path / "new.py").write_text("eval(input())\n")
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        result, findings = scan_directory(resource_id="r1", directory=str(tmp_path), last_scan_at=past)
        assert len(findings) > 0


class TestLanguagePatterns:
    def test_rust_unsafe_block(self, tmp_path):
        (tmp_path / "lib.rs").write_text("fn main() { unsafe { *ptr } }\n")
        result, findings = scan_directory(resource_id="r1", directory=str(tmp_path))
        assert any(f.language == "rust" for f in findings)

    def test_go_unsafe_pointer(self, tmp_path):
        (tmp_path / "main.go").write_text("p := unsafe.Pointer(&x)\n")
        result, findings = scan_directory(resource_id="r1", directory=str(tmp_path))
        assert any(f.category == "unsafe-pointer" for f in findings)

    def test_ts_as_any(self, tmp_path):
        (tmp_path / "app.ts").write_text("const x = data as any;\n")
        result, findings = scan_directory(resource_id="r1", directory=str(tmp_path))
        assert any(f.category == "type-safety-bypass" for f in findings)
