"""Skip the entire e2e suite unless a base URL is provided.

The default `pytest tests/` invocation must remain network-free and
browser-free. Set ``SUPAVISION_E2E_BASE_URL`` to point at a running dev
server to opt in.
"""

from __future__ import annotations

import os

import pytest

E2E_BASE_URL = os.environ.get("SUPAVISION_E2E_BASE_URL", "").strip()


def pytest_collection_modifyitems(config, items):
    if E2E_BASE_URL:
        return
    skip = pytest.mark.skip(
        reason="SUPAVISION_E2E_BASE_URL not set — see tests/e2e/README.md",
    )
    for item in items:
        # Only skip items that live under tests/e2e/. Without this guard pytest
        # applies the hook to the whole session (this conftest is at e2e/ but
        # pytest_collection_modifyitems receives every collected item).
        if "tests/e2e/" in str(item.fspath) or "tests\\e2e\\" in str(item.fspath):
            item.add_marker(skip)
