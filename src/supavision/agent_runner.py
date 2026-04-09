"""Agent Runner — background thread that executes jobs via Claude Code.

Uses `claude -p --output-format stream-json --verbose` as a subprocess.
Captures structured JSON output line by line. No tmux needed.

This is a Lane 2 (Work) module — it operates on WorkItem/AgentJob models
and must never import from models.health or write to the evaluations table.

ToolPolicy: tool access is scoped by job_type:
  - evaluate: Read,Glob,Grep (read-only)
  - implement: full access (Edit, Write, Bash, git)
  - scout: Read,Glob,Grep (read-only)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .code_evaluator import generate_work_item_eval_prompt, parse_eval_result
from .db import Store
from .models import (
    AgentJob,
    FindingSeverity,
    FindingStage,
    JobStatus,
    ManualTask,
    Priority,
    TaskCategory,
    WorkItem,
)
from .prompt_builder import generate_work_item_prompt


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _log(msg: str) -> None:
    ts = _now().strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[agent-runner] [{ts}] {msg}", flush=True)


# ── Job Configuration (ToolPolicy) ──────────────────────────────────

JOB_CONFIG = {
    "evaluate": {
        "max_turns": 25,
        "timeout": 180,  # 3 minutes
        "allowed_tools": "Read,Glob,Grep",
    },
    "implement": {
        "max_turns": 100,
        "timeout": 1200,  # 20 minutes
        "allowed_tools": "Edit,Write,Read,Glob,Grep,Bash(*)",
    },
    "scout": {
        "max_turns": 50,
        "timeout": 300,  # 5 minutes
        "allowed_tools": "Read,Glob,Grep",
    },
}

# ── Scout Prompts ────────────────────────────────────────────────────

SCOUT_PROMPTS = {
    "general": (
        "Analyze this project and identify the top 5-10 most impactful improvements.\n"
        "Look for: bugs, security issues, performance problems, missing error handling, "
        "code quality issues, missing tests, and feature gaps.\n\n"
        "For each finding, output a JSON array:\n```json\n[\n  {\n"
        '    "title": "Short descriptive title",\n'
        '    "description": "Detailed explanation of the issue and why it matters",\n'
        '    "category": "bug|security|performance|improvement|feature",\n'
        '    "priority": "critical|high|medium|low",\n'
        '    "file_path": "path/to/file.py",\n'
        '    "line_number": 42,\n'
        '    "effort": "trivial|small|medium|large"\n  }\n]\n```\n\n'
        "Focus on actionable, specific findings. Read the actual code."
    ),
    "security": (
        "Perform a security audit of this project. Look for:\n"
        "- Input validation gaps\n- Authentication/authorization issues\n"
        "- SQL injection, XSS, command injection\n- Hardcoded secrets\n"
        "- Insecure dependencies\n- Missing rate limiting\n"
        "- Error handling that leaks information\n\n"
        "Output findings as a JSON array (same format as above)."
    ),
    "performance": (
        "Analyze this project for performance issues:\n"
        "- N+1 query patterns\n- Unbounded data loading\n"
        "- Missing caching opportunities\n- Synchronous I/O in async contexts\n"
        "- Expensive operations in hot paths\n- Memory leaks\n\n"
        "Output findings as a JSON array (same format as above)."
    ),
    "quality": (
        "Review this project's code quality:\n"
        "- Missing error handling on I/O\n- Functions too long or complex\n"
        "- Missing or inadequate tests\n- Dead code\n- Inconsistent patterns\n"
        "- Poor separation of concerns\n\n"
        "Output findings as a JSON array (same format as above)."
    ),
}


# ── Live Output Buffers ─────────────────────────────────────────────

MAX_BUFFER_LINES = 5000
MAX_OUTPUT_BYTES = 100_000

_job_buffers: dict[str, list[tuple[float, str]]] = {}  # (timestamp, text)
_job_complete: dict[str, bool] = {}


def get_job_buffer(job_id: str) -> tuple[list[tuple[float, str]], bool]:
    """Get buffered output and completion status for a job.

    Returns (events, is_done) where events are (timestamp, text) tuples.
    """
    return _job_buffers.get(job_id, []), _job_complete.get(job_id, False)


# ── Agent Runner ─────────────────────────────────────────────────────

class AgentRunner:
    """Background thread that picks up pending AgentJobs and runs Claude Code."""

    def __init__(self, store: Store):
        self.store = store
        self._running = False
        self._thread: threading.Thread | None = None
        self._current_proc: subprocess.Popen | None = None
        self._current_job_id: str | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="agent-runner"
        )
        self._thread.start()
        _log("Agent runner started")

    def stop(self) -> None:
        self._running = False
        if self._current_proc:
            self._graceful_kill(self._current_proc)
        _log("Agent runner stopped")

    def _graceful_kill(self, proc: subprocess.Popen) -> None:
        """SIGTERM first, wait 5s, then SIGKILL."""
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        except ProcessLookupError:
            pass

    def get_status(self) -> dict:
        """Return current runner status."""
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            "pending_jobs": len(self.store.get_pending_agent_jobs()),
        }

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a running or pending job."""
        if self._current_job_id == job_id and self._current_proc:
            self._graceful_kill(self._current_proc)
            _log(f"Cancelled process for job {job_id}")
            return True
        job = self.store.get_agent_job(job_id)
        if job and job.status == JobStatus.PENDING:
            job.status = JobStatus.CANCELLED
            job.completed_at = _now()
            self.store.save_agent_job(job)
            return True
        return False

    def _run_loop(self) -> None:
        while self._running:
            try:
                pending = self.store.get_pending_agent_jobs()
                if pending:
                    self._execute_job(pending[0])
                else:
                    time.sleep(3)
            except Exception as e:
                _log(f"Runner error: {e}")
                time.sleep(5)

    def _execute_job(self, job: AgentJob) -> None:
        """Execute a single job via Claude Code subprocess."""
        self._current_job_id = job.id
        is_scout = job.job_type.startswith("scout")

        # Resolve resource to get working directory
        resource = self.store.get_resource(job.resource_id)
        if not resource:
            self._fail_job(job, f"Resource {job.resource_id} not found")
            return

        work_dir = resource.config.get("path", "")
        if not work_dir or not Path(work_dir).is_dir():
            self._fail_job(job, f"Resource path not found: {work_dir}")
            return

        # Build prompt
        if is_scout:
            focus = job.job_type.replace("scout-", "") or "general"
            config = JOB_CONFIG.get("scout", JOB_CONFIG["evaluate"])
            prompt = SCOUT_PROMPTS.get(focus, SCOUT_PROMPTS["general"])
            item = None
        else:
            item = self.store.get_work_item(job.work_item_id)
            if not item:
                self._fail_job(job, f"Work item {job.work_item_id} not found")
                return

            config = JOB_CONFIG.get(job.job_type, JOB_CONFIG["evaluate"])

            if job.job_type == "evaluate":
                bl_entries = self.store.list_blocklist()
                prompt = generate_work_item_eval_prompt(item, bl_entries)
            else:
                prompt = generate_work_item_prompt(item, resource.name)

        # Capture git state before implement jobs
        git_before = ""
        is_git_repo = False
        if job.job_type == "implement":
            try:
                r = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=work_dir,
                    capture_output=True, text=True, timeout=5,
                )
                if r.returncode == 0:
                    git_before = r.stdout.strip()
                    is_git_repo = True
            except Exception:
                pass

        # Generate MCP config so agent can query Supervisor data
        mcp_config = {
            "mcpServers": {
                "supavision": {
                    "command": sys.executable,
                    "args": ["-m", "supavision.mcp"],
                    "env": {
                        "SUPAVISION_DB_PATH": str(self.store.db_path),
                    },
                }
            }
        }
        mcp_fd = tempfile.NamedTemporaryFile(
            suffix=".json", prefix="supavision-mcp-", delete=False, mode="w",
        )
        mcp_fd.write(json.dumps(mcp_config))
        mcp_fd.close()
        mcp_config_file = Path(mcp_fd.name)

        # Build claude command
        cmd = [
            "claude", "-p", prompt,
            "--output-format", "stream-json",
            "--verbose",
            "--max-turns", str(config["max_turns"]),
            "--permission-mode", "auto",
            "--mcp-config", str(mcp_config_file),
        ]
        if config["allowed_tools"]:
            cmd.extend(["--allowedTools", config["allowed_tools"]])

        title = item.display_title if item else f"scout-{job.job_type}"
        _log(f"Starting job {job.id} ({job.job_type}) for {title}")

        # Update job status
        job.status = JobStatus.RUNNING
        job.started_at = _now()
        self.store.save_agent_job(job)

        # Initialize output buffer for SSE streaming (timestamped)
        _job_buffers[job.id] = []
        _job_complete[job.id] = False
        _job_start_time = time.time()

        try:
            proc = subprocess.Popen(
                cmd, shell=False,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, cwd=work_dir,
            )
            self._current_proc = proc
            job.pid = proc.pid

            last_result = None
            last_assistant_text = ""
            deadline = time.time() + config["timeout"]

            for line in proc.stdout:
                if not self._running:
                    self._graceful_kill(proc)
                    break

                if time.time() > deadline:
                    self._graceful_kill(proc)
                    job.error = f"Timeout after {config['timeout']}s"
                    _log(f"Job {job.id} timed out")
                    break

                line = line.strip()
                if not line:
                    continue

                # Buffer for SSE with timestamp (capped to prevent memory bloat)
                if job.id in _job_buffers:
                    elapsed = time.time() - _job_start_time
                    _job_buffers[job.id].append((elapsed, line))
                    if len(_job_buffers[job.id]) > MAX_BUFFER_LINES:
                        _job_buffers[job.id] = _job_buffers[job.id][-MAX_BUFFER_LINES:]

                try:
                    msg = json.loads(line)
                    msg_type = msg.get("type", "")

                    if msg_type == "result":
                        last_result = msg
                        result_text = msg.get("result", "")
                        if result_text:
                            job.output += f"\n--- Result ---\n{result_text}\n"

                    elif msg_type == "assistant":
                        message = msg.get("message", {})
                        content = message.get("content", [])
                        if isinstance(content, list):
                            for block in content:
                                if not isinstance(block, dict):
                                    continue
                                if block.get("type") == "text":
                                    text = block.get("text", "")
                                    if text:
                                        job.output += text + "\n"
                                        last_assistant_text = text
                                elif block.get("type") == "tool_use":
                                    tool_name = block.get("name", "")
                                    tool_input = block.get("input", {})
                                    file_arg = tool_input.get(
                                        "file_path",
                                        tool_input.get(
                                            "path",
                                            tool_input.get("pattern", ""),
                                        ),
                                    )
                                    job.output += (
                                        f"[TOOL] {tool_name}: "
                                        f"{str(file_arg)[:100]}\n"
                                    )

                except json.JSONDecodeError:
                    job.output += line + "\n"

            proc.wait(timeout=10)
            if proc.stderr:
                try:
                    proc.stderr.read()
                except Exception:
                    pass

            # Determine final status
            if last_result:
                job.result = json.dumps(last_result)
                job.status = JobStatus.COMPLETED
                self._apply_results(item, job, last_result)
                _log(f"Job {job.id} completed successfully")
            elif job.error:
                job.status = JobStatus.FAILED
            else:
                rc = proc.returncode
                if rc == 0 and last_assistant_text:
                    job.result = json.dumps({"result": last_assistant_text})
                    job.status = JobStatus.COMPLETED
                    self._apply_results(
                        item, job, {"result": last_assistant_text}
                    )
                    _log(f"Job {job.id} completed (used last text)")
                else:
                    job.status = JobStatus.FAILED
                    stderr = proc.stderr.read() if proc.stderr else ""
                    job.error = f"Exit code {rc}. {stderr[:500]}"
                    _log(f"Job {job.id} failed: {job.error[:100]}")

            # Capture git diff for implement jobs
            if (
                job.job_type == "implement"
                and is_git_repo
                and job.status == JobStatus.COMPLETED
            ):
                self._capture_git_diff(job, work_dir, git_before)

        except Exception as e:
            job.status = JobStatus.FAILED
            job.error = str(e)[:500]
            _log(f"Job {job.id} exception: {e}")

        finally:
            job.completed_at = _now()
            # Cap output before persisting to avoid DB bloat
            if len(job.output) > MAX_OUTPUT_BYTES:
                job.output = job.output[-MAX_OUTPUT_BYTES:]
            # Persist recording from buffer
            if job.id in _job_buffers and _job_buffers[job.id]:
                events = _job_buffers[job.id]
                recording = [[round(ts * 1000), text] for ts, text in events]
                job.recording = json.dumps(recording)[-500_000:]
            self.store.save_agent_job(job)
            self._current_proc = None
            self._current_job_id = None
            _job_complete[job.id] = True
            # Thread-safe buffer cleanup after 60s grace period
            import threading
            _cleanup_id = job.id
            threading.Timer(60.0, lambda jid=_cleanup_id: (
                _job_buffers.pop(jid, None), _job_complete.pop(jid, None)
            )).start()
            try:
                mcp_config_file.unlink()
            except OSError:
                pass

    def _fail_job(self, job: AgentJob, error: str) -> None:
        job.status = JobStatus.FAILED
        job.error = error
        job.completed_at = _now()
        self.store.save_agent_job(job)
        self._current_job_id = None
        _log(f"Job {job.id} failed: {error}")

    def _apply_results(
        self, item: WorkItem | None, job: AgentJob, result_msg: dict
    ) -> None:
        """Apply job results back to the work item."""
        if job.job_type.startswith("scout"):
            self._apply_scout_results(job, result_msg)
            return

        if not item:
            return

        if job.job_type == "evaluate":
            result_text = result_msg.get("result", "") or job.output
            eval_data = parse_eval_result(result_text)

            if eval_data.get("reasoning"):
                item.evaluation_verdict = eval_data.get("verdict", "")
                item.evaluation_reasoning = eval_data.get("reasoning", "")
                item.evaluation_fix_approach = eval_data.get("fix_approach", "")
                item.evaluation_effort = eval_data.get("effort", "")
                if hasattr(item, "confidence"):
                    item.confidence = float(eval_data.get("confidence", 0.0))
            else:
                reasoning = job.output.strip() or result_text
                item.evaluation_reasoning = reasoning[:3000]
                text_lower = reasoning.lower()
                if "false positive" in text_lower or "not a real" in text_lower:
                    item.evaluation_verdict = "false_positive"
                elif "vulnerability" in text_lower or "exploitable" in text_lower:
                    item.evaluation_verdict = "true_positive"
                else:
                    item.evaluation_verdict = "needs_investigation"

            try:
                if item.stage in (FindingStage.SCANNED, FindingStage.CREATED):
                    item.transition_to(FindingStage.EVALUATED)
            except ValueError:
                pass

            self.store.save_work_item(item)

        elif job.job_type == "implement":
            result_text = result_msg.get("result", "")
            if hasattr(item, "evaluation_fix_approach"):
                # Store completion note in the reasoning field
                item.evaluation_reasoning = (
                    (item.evaluation_reasoning or "")
                    + f"\n\n[Agent completed]\n{result_text[:2000]}"
                )

            try:
                if item.stage == FindingStage.APPROVED:
                    item.transition_to(FindingStage.IMPLEMENTING)
            except ValueError:
                pass

            self.store.save_work_item(item)

    def _apply_scout_results(
        self, job: AgentJob, result_msg: dict
    ) -> None:
        """Parse scout output and create ManualTask entries."""
        result_text = result_msg.get("result", "") or job.output

        # Try JSON array in output
        findings: list[dict] = []
        json_match = re.search(r"\[[\s\S]*?\]", result_text)
        if json_match:
            try:
                findings = json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        if not findings:
            code_match = re.search(
                r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", result_text, re.DOTALL
            )
            if code_match:
                try:
                    findings = json.loads(code_match.group(1))
                except json.JSONDecodeError:
                    pass

        if not findings:
            _log(f"Scout job {job.id}: no structured findings in output")
            return

        cat_map = {
            "bug": TaskCategory.BUG,
            "security": TaskCategory.SECURITY,
            "performance": TaskCategory.PERFORMANCE,
            "improvement": TaskCategory.IMPROVEMENT,
            "feature": TaskCategory.FEATURE,
        }
        pri_map = {
            "critical": Priority.CRITICAL,
            "high": Priority.HIGH,
            "medium": Priority.MEDIUM,
            "low": Priority.LOW,
        }

        created = 0
        for f in findings:
            if not isinstance(f, dict) or not f.get("title"):
                continue

            task = ManualTask(
                resource_id=job.resource_id,
                title=f.get("title", "")[:200],
                description=f.get("description", ""),
                task_category=cat_map.get(
                    f.get("category", ""), TaskCategory.IMPROVEMENT
                ),
                priority=pri_map.get(f.get("priority", ""), Priority.MEDIUM),
                severity=FindingSeverity.MEDIUM,
                file_path=f.get("file_path", ""),
                line_number=f.get("line_number", 0) or 0,
                evaluation_effort=f.get("effort", ""),
            )
            self.store.save_work_item(task)
            created += 1

        _log(
            f"Scout job {job.id}: created {created} tasks "
            f"from {len(findings)} findings"
        )

    def _capture_git_diff(
        self, job: AgentJob, work_dir: str, git_before: str
    ) -> None:
        """Capture git diff for completed implement jobs."""
        try:
            git_after = ""
            r = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=work_dir,
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                git_after = r.stdout.strip()

            commit_diff = ""
            if git_before and git_after and git_before != git_after:
                r = subprocess.run(
                    ["git", "diff", git_before, git_after],
                    cwd=work_dir, capture_output=True, text=True, timeout=10,
                )
                commit_diff = r.stdout[:50000]

            result = json.loads(job.result) if job.result else {}
            result["git"] = {
                "commit_diff": commit_diff,
                "before": git_before,
                "after": git_after,
            }
            job.result = json.dumps(result)
            _log(f"Job {job.id} git diff captured")
        except Exception as ge:
            _log(f"Job {job.id} git diff capture failed: {ge}")


# ── Global runner instance ───────────────────────────────────────────

_runner: AgentRunner | None = None


def get_runner() -> AgentRunner | None:
    return _runner


def start_runner(store: Store) -> AgentRunner:
    global _runner
    # Recover any agent jobs stuck in RUNNING from a previous crash
    recovered = store.recover_stale_agent_jobs(hours=1)
    if recovered:
        _log(f"Recovered {recovered} stale agent job(s)")
    _runner = AgentRunner(store)
    _runner.start()
    return _runner


def stop_runner() -> None:
    global _runner
    if _runner:
        _runner.stop()
        _runner = None
