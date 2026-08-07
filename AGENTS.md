# Repository Guidelines

## Project Structure & Module Organization
Supavision is a Python 3.12+ package using a `src/` layout. Core code lives in `src/supavision/`: runtime orchestration in `engine.py`, persistence in `db.py`, CLI in `cli.py`, scheduler in `scheduler.py`, and web entrypoints under `web/`. Dashboard routes are in `web/dashboard/`, Jinja templates in `web/templates/`, static assets in `web/static/`, and resource prompt templates in `prompt_templates/{resource_type}/`. Tests live in `tests/`.

## Build, Test, and Development Commands
Create a local environment with `python -m venv .venv && source .venv/bin/activate`. Install dev dependencies with `pip install -e ".[dev]"`. Run the full test suite with `pytest tests/ -v --tb=short`. Run lint checks with `ruff check src/ tests/`, and verify formatting with `ruff format --check src/ tests/`. For local web development, run `SUPAVISION_COOKIE_SECURE=false supavision serve --port 8080`.

## Coding Style & Naming Conventions
Use Ruff with Python 3.12 settings, 120-character lines, and `E,F,I,W` lint rules. Keep imports sorted by Ruff. Use Pydantic models for structured data. Follow existing snake_case naming for functions, variables, modules, and test files. Keep CLI JSON output on stdout and human-readable status on stderr.

## Testing Guidelines
Use pytest. Tests should be named `test_*.py` and colocated in `tests/`. Prefer real SQLite stores backed by `tmp_path`; engine and CLI tests may mock Claude CLI subprocesses. Run `pytest tests/test_lane_boundary.py -v` after architecture or import changes because it enforces model import boundaries.

## Commit & Pull Request Guidelines
Commit history uses Conventional Commit-style prefixes such as `feat:`, `fix:`, `chore:`, `polish:`, and `security:`. Keep PRs focused, describe the change, link relevant issues, include screenshots for UI changes, and update README/docs for user-facing behavior. PRs should pass `pytest tests/` and `ruff check src/ tests/`.

## Security & Agent-Specific Notes
Tools must remain read-only and allowlisted; never add arbitrary shell execution. Validate all LLM-generated tool arguments. Store credentials as environment variable references, not raw secrets. For local HTTP development, set `SUPAVISION_COOKIE_SECURE=false`.
