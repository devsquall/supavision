# End-to-end browser tests (Playwright)

Playwright-based smoke tests for the dashboard happy-path flows. **Not run as
part of the default `pytest tests/`** — they need a running dev server and a
browser. Treat them as a separate suite that gates release candidates.

## Setup

```bash
.venv/bin/pip install pytest-playwright
.venv/bin/playwright install chromium
```

## Running locally

In one terminal, start a dev server pointed at a throwaway database:

```bash
export SUPAVISION_DB=$(mktemp -t supavision-e2e.XXXXXX.db)
.venv/bin/supavision create-admin   # interactive — set up admin
.venv/bin/supavision serve --port 8765
```

In another terminal:

```bash
export SUPAVISION_E2E_BASE_URL=http://localhost:8765
export SUPAVISION_E2E_EMAIL=...
export SUPAVISION_E2E_PASSWORD=...
.venv/bin/pytest tests/e2e/ -v
```

If `SUPAVISION_E2E_BASE_URL` is unset, the suite is skipped — that keeps `pytest tests/` honest.

## What's covered

- `test_happy_path.py::test_login_then_view_dashboard` — sign in and reach the dashboard.
- `test_happy_path.py::test_add_resource_then_view_detail` — wizard happy path with env-var-reference credential fields.

This is a scaffold for the full flagship workflow coverage in plan P2 #17. Add tests as we identify more risky regressions in the dashboard UI.
