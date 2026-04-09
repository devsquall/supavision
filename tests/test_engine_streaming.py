"""Tests for engine.py — streaming buffers, output validation, and retry logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from supavision.engine import Engine, _run_buffers, _run_complete, get_run_buffer


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_buffers():
    """Ensure module-level buffer dicts are clean before and after each test."""
    _run_buffers.clear()
    _run_complete.clear()
    yield
    _run_buffers.clear()
    _run_complete.clear()


# ── get_run_buffer tests ────────────────────────────────────────────


class TestGetRunBuffer:
    def test_unknown_run_id_returns_empty_done(self):
        """Buffer not present at all means the run finished and was cleaned up."""
        lines, done = get_run_buffer("nonexistent-run-id")
        assert lines == []
        assert done is True

    def test_buffer_exists_not_complete(self):
        """Active run: buffer exists, completion flag not set yet."""
        _run_buffers["run-1"] = ["line one", "line two"]
        _run_complete["run-1"] = False

        lines, done = get_run_buffer("run-1")
        assert lines == ["line one", "line two"]
        assert done is False

    def test_buffer_exists_and_complete(self):
        """Run finished: buffer exists and completion flag is True."""
        _run_buffers["run-2"] = ["output line"]
        _run_complete["run-2"] = True

        lines, done = get_run_buffer("run-2")
        assert lines == ["output line"]
        assert done is True

    def test_direct_manipulation_reflects_in_get(self):
        """Verify that direct dict manipulation is visible through get_run_buffer."""
        _run_buffers["run-3"] = []
        _run_complete["run-3"] = False

        # Simulate streaming: append lines
        _run_buffers["run-3"].append("first")
        lines, done = get_run_buffer("run-3")
        assert lines == ["first"]
        assert done is False

        _run_buffers["run-3"].append("second")
        _run_complete["run-3"] = True
        lines, done = get_run_buffer("run-3")
        assert lines == ["first", "second"]
        assert done is True

    def test_after_cleanup_returns_done(self):
        """After popping from both dicts (simulating the 60s cleanup), returns done."""
        _run_buffers["run-4"] = ["data"]
        _run_complete["run-4"] = True

        # Simulate the scheduled cleanup
        _run_buffers.pop("run-4", None)
        _run_complete.pop("run-4", None)

        lines, done = get_run_buffer("run-4")
        assert lines == []
        assert done is True

    def test_buffer_exists_complete_key_missing(self):
        """Edge case: buffer present but _run_complete key not set.
        get_run_buffer uses .get(run_id, False) so should return False."""
        _run_buffers["run-5"] = ["some output"]
        # Deliberately do NOT set _run_complete["run-5"]

        lines, done = get_run_buffer("run-5")
        assert lines == ["some output"]
        assert done is False


# ── Engine CLI output validation and retry tests ────────────────────


def _make_engine():
    """Create an Engine with mocked dependencies so we skip __init__ validation."""
    with patch("supavision.engine.shutil.which", return_value="/usr/bin/claude"):
        engine = Engine.__new__(Engine)
        engine.store = None
        engine.template_dir = ""
        engine.model = "sonnet"
        engine.max_turns = 50
        engine.backend = "claude_cli"
        engine._api_key = None
        engine._evaluator = None
        engine._CLI_MAX_RETRIES = 2
        engine._CLI_RETRY_DELAY = 0  # No sleep in tests
    return engine


class TestOutputValidation:
    @pytest.mark.asyncio
    async def test_short_output_raises_runtime_error(self):
        """Output under 50 chars should raise RuntimeError."""
        engine = _make_engine()

        mock_once = AsyncMock(return_value=("short", {"turns": 0}))

        with patch.object(engine, "_run_claude_cli_once", mock_once):
            # _run_claude_cli catches RuntimeError and retries up to _CLI_MAX_RETRIES.
            # Since mock always returns short output, all attempts fail.
            with pytest.raises(RuntimeError, match="insufficient output"):
                await engine._run_claude_cli("test prompt", run_id=None)

        # Should have been called _CLI_MAX_RETRIES times (2)
        assert mock_once.call_count == engine._CLI_MAX_RETRIES

    @pytest.mark.asyncio
    async def test_output_over_5mb_gets_truncated(self):
        """Output exceeding 5MB cap should be truncated."""
        engine = _make_engine()

        large_output = "x" * 6_000_000  # 6MB
        mock_once = AsyncMock(return_value=(large_output, {
            "turns": 0, "tool_calls": 0,
            "input_tokens": 0, "output_tokens": 0,
        }))

        with patch.object(engine, "_run_claude_cli_once", mock_once):
            output, stats = await engine._run_claude_cli("test prompt", run_id=None)

        # Output should be capped at 5MB + truncation notice
        assert len(output) < 6_000_000
        assert output.endswith("[Output truncated]")
        assert stats["attempt"] == 1


class TestRetryLogic:
    @pytest.mark.asyncio
    async def test_retry_on_runtime_error_then_succeed(self):
        """First call fails with RuntimeError, second succeeds."""
        engine = _make_engine()

        good_output = "A" * 100  # Over 50 char minimum
        good_stats = {
            "turns": 0, "tool_calls": 0,
            "input_tokens": 0, "output_tokens": 0,
        }

        call_count = 0

        async def _mock_once(prompt, timeout, run_id=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Claude CLI exited with code 1: some error")
            return good_output, good_stats

        with patch.object(engine, "_run_claude_cli_once", side_effect=_mock_once):
            output, stats = await engine._run_claude_cli("test prompt", run_id=None)

        assert call_count == 2
        assert output == good_output
        assert stats["attempt"] == 2

    @pytest.mark.asyncio
    async def test_retry_on_os_error(self):
        """OSError (e.g. subprocess issue) triggers retry."""
        engine = _make_engine()

        good_output = "B" * 100
        good_stats = {
            "turns": 0, "tool_calls": 0,
            "input_tokens": 0, "output_tokens": 0,
        }

        call_count = 0

        async def _mock_once(prompt, timeout, run_id=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("No such file or directory")
            return good_output, good_stats

        with patch.object(engine, "_run_claude_cli_once", side_effect=_mock_once):
            output, stats = await engine._run_claude_cli("test prompt", run_id=None)

        assert call_count == 2
        assert output == good_output

    @pytest.mark.asyncio
    async def test_max_retries_exceeded_raises_last_error(self):
        """When all attempts fail, the last error is raised."""
        engine = _make_engine()

        mock_once = AsyncMock(side_effect=RuntimeError("persistent failure"))

        with patch.object(engine, "_run_claude_cli_once", mock_once):
            with pytest.raises(RuntimeError, match="persistent failure"):
                await engine._run_claude_cli("test prompt", run_id=None)

        assert mock_once.call_count == engine._CLI_MAX_RETRIES
