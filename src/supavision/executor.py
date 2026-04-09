"""Command executor — runs commands on local or remote hosts via subprocess.

Uses the system `ssh` binary for remote execution (zero Python SSH dependencies).
For localhost, runs commands directly via asyncio subprocess.

Security:
  - All commands go through scoped tools (tools.py), never arbitrary user input.
  - Output truncated to MAX_OUTPUT_BYTES to prevent memory exhaustion.
  - Command timeout enforced via asyncio.wait_for.
  - SSH multiplexing for connection reuse within a run.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_OUTPUT_BYTES = 10_240  # 10KB per command
DEFAULT_TIMEOUT = 30  # seconds
MULTIPLEX_DIR = Path("/tmp/supavision-ssh-mux")


@dataclass
class CommandResult:
    """Result of a command execution."""

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    truncated: bool = False


@dataclass
class ConnectionConfig:
    """SSH connection configuration for a resource."""

    host: str
    user: str
    key_path: str
    port: int = 22

    @property
    def control_path(self) -> str:
        """SSH ControlPath for multiplexing."""
        MULTIPLEX_DIR.mkdir(parents=True, exist_ok=True)
        return str(MULTIPLEX_DIR / f"{self.user}@{self.host}:{self.port}")


@dataclass
class Executor:
    """Executes commands on local or remote hosts."""

    connection: ConnectionConfig | None = None
    timeout: int = DEFAULT_TIMEOUT
    _mux_established: bool = field(default=False, init=False)

    async def run(self, command: str, timeout: int | None = None) -> CommandResult:
        """Run a command, dispatching to local or remote based on connection config."""
        effective_timeout = timeout or self.timeout
        if self.connection:
            return await self._run_remote(command, effective_timeout)
        return await self._run_local(command, effective_timeout)

    async def _run_local(self, command: str, timeout: int) -> CommandResult:
        """Execute a command locally via subprocess."""
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            return self._build_result(
                stdout_bytes, stderr_bytes, proc.returncode or 0
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return CommandResult(
                stdout=f"[TIMED OUT after {timeout}s]",
                stderr="",
                exit_code=-1,
                timed_out=True,
            )
        except Exception as e:
            return CommandResult(
                stdout="", stderr=f"[EXECUTION ERROR: {e}]", exit_code=-1
            )

    async def _run_remote(self, command: str, timeout: int) -> CommandResult:
        """Execute a command on a remote host via system ssh."""
        conn = self.connection
        if not conn:
            return CommandResult(
                stdout="", stderr="[ERROR: No connection configured]", exit_code=-1
            )

        # Validate key exists
        if not os.path.isfile(conn.key_path):
            return CommandResult(
                stdout="",
                stderr=f"[ERROR: SSH key not found at {conn.key_path}]",
                exit_code=-1,
            )

        ssh_args = self._build_ssh_args(conn, command)

        try:
            proc = await asyncio.create_subprocess_exec(
                *ssh_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            return self._build_result(
                stdout_bytes, stderr_bytes, proc.returncode or 0
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return CommandResult(
                stdout=f"[TIMED OUT after {timeout}s]",
                stderr="",
                exit_code=-1,
                timed_out=True,
            )
        except Exception as e:
            return CommandResult(
                stdout="", stderr=f"[SSH ERROR: {e}]", exit_code=-1
            )

    def _build_ssh_args(self, conn: ConnectionConfig, command: str) -> list[str]:
        """Build ssh command arguments with multiplexing support."""
        ssh_bin = shutil.which("ssh") or "ssh"
        args = [
            ssh_bin,
            "-o", "StrictHostKeyChecking=yes",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            "-o", f"ControlPath={conn.control_path}",
            "-i", conn.key_path,
            "-p", str(conn.port),
        ]

        # Enable multiplexing on first connection
        if not self._mux_established:
            args.extend(["-o", "ControlMaster=auto", "-o", "ControlPersist=300"])

        args.append(f"{conn.user}@{conn.host}")
        args.append(command)
        return args

    async def setup_multiplexing(self) -> bool:
        """Establish SSH multiplexed connection for reuse within a run."""
        if not self.connection or self._mux_established:
            return True

        result = await self._run_remote("echo connected", timeout=15)
        if result.exit_code == 0:
            self._mux_established = True
            logger.info(
                "SSH multiplexing established: %s@%s",
                self.connection.user, self.connection.host,
            )
            return True

        logger.error(
            "SSH multiplexing failed: %s@%s — %s",
            self.connection.user, self.connection.host, result.stderr,
        )
        return False

    async def teardown_multiplexing(self) -> None:
        """Close the SSH multiplexed connection."""
        if not self.connection or not self._mux_established:
            return

        conn = self.connection
        ssh_bin = shutil.which("ssh") or "ssh"
        try:
            proc = await asyncio.create_subprocess_exec(
                ssh_bin,
                "-O", "exit",
                "-o", f"ControlPath={conn.control_path}",
                f"{conn.user}@{conn.host}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=5)
        except Exception as e:
            logger.warning("SSH multiplex teardown error: %s", e)
        finally:
            self._mux_established = False

    async def test_connection(self) -> tuple[bool, str]:
        """Test connectivity. Returns (success, message)."""
        if not self.connection:
            # Local — always works
            result = await self._run_local("echo ok", timeout=5)
            return result.exit_code == 0, result.stdout.strip() or result.stderr.strip()

        result = await self.run("echo ok", timeout=15)
        if result.exit_code == 0:
            return True, f"Connected to {self.connection.user}@{self.connection.host}"
        return False, result.stderr.strip() or "Connection failed"

    def _build_result(
        self, stdout_bytes: bytes, stderr_bytes: bytes, exit_code: int
    ) -> CommandResult:
        """Build CommandResult with truncation handling."""
        truncated = False
        total_stdout = len(stdout_bytes)
        total_stderr = len(stderr_bytes)

        stdout = stdout_bytes[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        stderr = stderr_bytes[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")

        if total_stdout > MAX_OUTPUT_BYTES:
            truncated = True
            stdout += f"\n[OUTPUT TRUNCATED — {total_stdout} bytes total, showing first {MAX_OUTPUT_BYTES}]"

        if total_stderr > MAX_OUTPUT_BYTES:
            truncated = True
            stderr += f"\n[STDERR TRUNCATED — {total_stderr} bytes total, showing first {MAX_OUTPUT_BYTES}]"

        return CommandResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            truncated=truncated,
        )
