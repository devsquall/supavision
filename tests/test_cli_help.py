"""Regression tests for the CLI `--help` output.

Before this fixup, `supavision --help` listed 28 subcommands in a single flat
alphabetised block — overwhelming for a new user trying to find e.g. "the
command to start the server". The fix groups commands by domain via a custom
epilog and suppresses argparse's auto-generated subparser table.

These tests don't snapshot the entire help text (too brittle); they pin the
structural invariants that matter:
- the grouped section headers are present
- every registered subcommand is listed in the epilog
- per-command `--help` still works (no regression from suppressing the
  auto-generated table)
"""

from __future__ import annotations

import subprocess
import sys

import pytest

# Categories and their expected commands. Keep in sync with the epilog in
# cli.main(). If you add a command, add it here too — that's the contract.
COMMAND_GROUPS = {
    "resources:": [
        "resource-add",
        "resource-list",
        "resource-show",
        "set-schedule",
        "add-credential",
    ],
    "runs:": [
        "run-discovery",
        "run-health-check",
        "run-status",
        "run-scheduler",
    ],
    "reports & context:": [
        "report-show",
        "report-list",
        "context-show",
        "context-diff",
    ],
    "monitoring config:": [
        "checklist-show",
        "checklist-add",
        "template-list",
    ],
    "notifications:": [
        "notify-test",
        "notify-configure",
    ],
    "server & api keys:": [
        "serve",
        "api-key-create",
        "api-key-list",
        "api-key-revoke",
    ],
    "mcp integration:": [
        "mcp-config",
        "mcp-serve",
    ],
    "admin & setup:": [
        "setup",
        "create-admin",
        "doctor",
        "seed-demo",
        "purge",
    ],
}


@pytest.fixture(scope="module")
def help_output() -> str:
    """Run `supavision --help` once and share output across tests."""
    result = subprocess.run(
        [sys.executable, "-m", "supavision.cli", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"--help failed: {result.stderr}"
    return result.stdout


def test_help_includes_commands_by_area_header(help_output):
    assert "commands by area:" in help_output


@pytest.mark.parametrize("group_header", list(COMMAND_GROUPS.keys()))
def test_each_group_header_present(help_output, group_header):
    assert group_header in help_output, f"group header missing: {group_header}"


@pytest.mark.parametrize(
    "command",
    [cmd for cmds in COMMAND_GROUPS.values() for cmd in cmds],
)
def test_every_command_listed_in_grouped_help(help_output, command):
    assert command in help_output, f"command '{command}' missing from grouped help"


def test_no_orphan_choices_table_at_top(help_output):
    """The old flat `{cmd1,cmd2,...}` block in usage line must be gone."""
    # The usage line should show "COMMAND" metavar, not the explicit list.
    assert "COMMAND" in help_output
    # Whitelist the commas that we *expect* (in --format choices); the flat
    # subcommand list would have many more.
    first_50_lines = "\n".join(help_output.splitlines()[:50])
    # The auto-generated subparser table listed every command on its own line
    # under "positional arguments". After the fix, only "COMMAND" is there.
    assert "resource-list       List all registered resources" not in first_50_lines, (
        "argparse's auto-generated subparser table is back — the suppression in cli.main() got reverted"
    )


def test_footer_tells_user_how_to_get_command_help(help_output):
    assert "supavision <command> --help" in help_output


def test_per_command_help_still_works():
    """Suppressing the subparser auto-list must NOT break per-command help."""
    result = subprocess.run(
        [sys.executable, "-m", "supavision.cli", "resource-add", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert "resource-add" in result.stdout
    assert "--type" in result.stdout
    assert "Resource type:" in result.stdout
