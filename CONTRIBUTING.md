# Contributing to Supavision

## Development Setup

```bash
git clone https://github.com/devsquall/supavision.git
cd supavision
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

The default backend (`claude_cli`) requires [Claude Code](https://claude.ai/code) installed. No API keys needed.

To use OpenRouter instead, copy `.env.example` to `.env` and set `OPENROUTER_API_KEY`.

## Running Tests

```bash
pytest tests/ -v
```

## Code Style

This project uses [ruff](https://docs.astral.sh/ruff/) for linting:

```bash
ruff check src/ tests/
ruff format src/ tests/
```

## Adding a Resource Type

1. Create a directory under `templates/` (e.g., `templates/my_type/`)
2. Add `discovery.md` — instructions for initial exploration
3. Add `health_check.md` — instructions for recurring health checks
4. If your type needs new tools, add them to `src/supavision/tools.py`:
   - Define the tool in `TOOL_DEFINITIONS`
   - Add a `_tool_<name>` method to `ToolDispatcher`
   - Include input validation (never trust LLM-generated arguments)
5. Add tests for any new tools in `tests/`

## Adding Tools

Tools must be **read-only and safe**. Guidelines:

- Validate all inputs (paths, service names, commands)
- Use allowlists, not blocklists
- Never allow arbitrary command execution
- Return errors as strings, never raise exceptions
- Keep tool output under 10KB (truncate if needed)

## Pull Requests

- Keep PRs focused on a single change
- Include tests for new functionality
- Update README if adding user-facing features
- Run `ruff check` and `pytest` before submitting
