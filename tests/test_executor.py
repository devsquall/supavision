"""Tests for executor.py — command execution and result building."""

from __future__ import annotations

import pytest

from supervisor.executor import (
    CommandResult,
    ConnectionConfig,
    Executor,
    MAX_OUTPUT_BYTES,
    MULTIPLEX_DIR,
)


# ── CommandResult ────────────────────────────────────────────────


class TestCommandResult:
    def test_basic_fields(self):
        r = CommandResult(stdout="hello", stderr="", exit_code=0)
        assert r.stdout == "hello"
        assert r.stderr == ""
        assert r.exit_code == 0
        assert r.timed_out is False
        assert r.truncated is False

    def test_timed_out_flag(self):
        r = CommandResult(stdout="", stderr="", exit_code=-1, timed_out=True)
        assert r.timed_out is True

    def test_truncated_flag(self):
        r = CommandResult(stdout="", stderr="", exit_code=0, truncated=True)
        assert r.truncated is True


# ── ConnectionConfig ─────────────────────────────────────────────


class TestConnectionConfig:
    def test_default_port(self):
        conn = ConnectionConfig(host="example.com", user="root", key_path="/key")
        assert conn.port == 22

    def test_custom_port(self):
        conn = ConnectionConfig(host="example.com", user="root", key_path="/key", port=2222)
        assert conn.port == 2222

    def test_control_path_contains_host_info(self):
        conn = ConnectionConfig(host="example.com", user="deploy", key_path="/key", port=2222)
        cp = conn.control_path
        assert "deploy@example.com:2222" in cp
        assert str(MULTIPLEX_DIR) in cp


# ── Executor local execution ────────────────────────────────────


class TestExecutorLocal:
    @pytest.mark.asyncio
    async def test_run_local_echo(self):
        executor = Executor()
        result = await executor.run("echo hello_world")
        assert result.exit_code == 0
        assert "hello_world" in result.stdout

    @pytest.mark.asyncio
    async def test_run_local_failing_command(self):
        executor = Executor()
        result = await executor.run("false")
        assert result.exit_code != 0

    @pytest.mark.asyncio
    async def test_run_local_stderr(self):
        executor = Executor()
        result = await executor.run("echo error_msg >&2")
        assert "error_msg" in result.stderr

    @pytest.mark.asyncio
    async def test_run_local_timeout(self):
        executor = Executor()
        result = await executor.run("sleep 10", timeout=1)
        assert result.timed_out is True
        assert result.exit_code == -1

    @pytest.mark.asyncio
    async def test_test_connection_local(self):
        executor = Executor()
        ok, msg = await executor.test_connection()
        assert ok is True
        assert "ok" in msg

    @pytest.mark.asyncio
    async def test_run_local_captures_both_streams(self):
        executor = Executor()
        result = await executor.run("echo out && echo err >&2")
        assert "out" in result.stdout
        assert "err" in result.stderr
        assert result.exit_code == 0


# ── _build_result truncation ────────────────────────────────────


class TestBuildResult:
    def test_no_truncation_for_small_output(self):
        executor = Executor()
        result = executor._build_result(b"hello", b"", 0)
        assert result.truncated is False
        assert result.stdout == "hello"

    def test_truncation_for_large_stdout(self):
        executor = Executor()
        big_stdout = b"x" * (MAX_OUTPUT_BYTES + 1000)
        result = executor._build_result(big_stdout, b"", 0)
        assert result.truncated is True
        assert "TRUNCATED" in result.stdout
        assert len(result.stdout) < len(big_stdout)

    def test_truncation_for_large_stderr(self):
        executor = Executor()
        big_stderr = b"e" * (MAX_OUTPUT_BYTES + 1000)
        result = executor._build_result(b"ok", big_stderr, 0)
        assert result.truncated is True
        assert "TRUNCATED" in result.stderr

    def test_unicode_handling(self):
        executor = Executor()
        result = executor._build_result("hello unicode: \u2603".encode("utf-8"), b"", 0)
        assert "\u2603" in result.stdout

    def test_invalid_utf8_replaced(self):
        executor = Executor()
        result = executor._build_result(b"\xff\xfe", b"", 0)
        # Should not raise, uses errors="replace"
        assert isinstance(result.stdout, str)


# ── _build_ssh_args ──────────────────────────────────────────────


class TestBuildSSHArgs:
    def test_basic_ssh_args(self):
        conn = ConnectionConfig(host="example.com", user="deploy", key_path="/path/key", port=22)
        executor = Executor(connection=conn)
        args = executor._build_ssh_args(conn, "uptime")

        assert "ssh" in args[0] or args[0].endswith("ssh")
        assert "-i" in args
        idx = args.index("-i")
        assert args[idx + 1] == "/path/key"
        assert "deploy@example.com" in args
        assert "uptime" in args

    def test_custom_port(self):
        conn = ConnectionConfig(host="example.com", user="deploy", key_path="/key", port=2222)
        executor = Executor(connection=conn)
        args = executor._build_ssh_args(conn, "ls")
        assert "-p" in args
        idx = args.index("-p")
        assert args[idx + 1] == "2222"

    def test_first_call_includes_control_master(self):
        conn = ConnectionConfig(host="example.com", user="deploy", key_path="/key")
        executor = Executor(connection=conn)
        assert executor._mux_established is False
        args = executor._build_ssh_args(conn, "echo test")
        # Should include ControlMaster=auto for first connection
        assert "ControlMaster=auto" in " ".join(args)

    def test_subsequent_call_no_control_master(self):
        conn = ConnectionConfig(host="example.com", user="deploy", key_path="/key")
        executor = Executor(connection=conn)
        executor._mux_established = True
        args = executor._build_ssh_args(conn, "echo test")
        # Should NOT include ControlMaster for subsequent calls
        assert "ControlMaster=auto" not in " ".join(args)

    def test_strict_host_key_checking(self):
        conn = ConnectionConfig(host="example.com", user="deploy", key_path="/key")
        executor = Executor(connection=conn)
        args = executor._build_ssh_args(conn, "ls")
        assert "StrictHostKeyChecking=yes" in " ".join(args)

    def test_batch_mode(self):
        conn = ConnectionConfig(host="example.com", user="deploy", key_path="/key")
        executor = Executor(connection=conn)
        args = executor._build_ssh_args(conn, "ls")
        assert "BatchMode=yes" in " ".join(args)


# ── Remote execution edge cases ──────────────────────────────────


class TestExecutorRemote:
    @pytest.mark.asyncio
    async def test_remote_no_connection_configured(self):
        executor = Executor()
        result = await executor._run_remote("echo test", timeout=5)
        assert result.exit_code == -1
        assert "No connection" in result.stderr

    @pytest.mark.asyncio
    async def test_remote_missing_key_file(self):
        conn = ConnectionConfig(
            host="example.com",
            user="deploy",
            key_path="/nonexistent/key/path_12345",
        )
        executor = Executor(connection=conn)
        result = await executor._run_remote("echo test", timeout=5)
        assert result.exit_code == -1
        assert "not found" in result.stderr.lower()

    @pytest.mark.asyncio
    async def test_setup_multiplexing_no_connection(self):
        executor = Executor()
        result = await executor.setup_multiplexing()
        assert result is True  # No-op when no connection

    @pytest.mark.asyncio
    async def test_teardown_multiplexing_no_connection(self):
        executor = Executor()
        # Should not raise
        await executor.teardown_multiplexing()

    @pytest.mark.asyncio
    async def test_teardown_multiplexing_not_established(self):
        conn = ConnectionConfig(host="example.com", user="deploy", key_path="/key")
        executor = Executor(connection=conn)
        # _mux_established is False, so teardown is a no-op
        await executor.teardown_multiplexing()

    @pytest.mark.asyncio
    async def test_test_connection_remote_missing_key(self):
        conn = ConnectionConfig(
            host="example.com",
            user="deploy",
            key_path="/nonexistent/key/path_12345",
        )
        executor = Executor(connection=conn)
        ok, msg = await executor.test_connection()
        assert ok is False
