"""Web dashboard routes for Supavision — split into domain modules."""

from __future__ import annotations

import html as html_mod
import logging
import re
import time
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

router = APIRouter()


def _render(request: Request, template: str, context: dict | None = None, **kwargs):
    """Render a template with CSRF token and current user auto-injected."""
    ctx = context or {}
    ctx["csrf_token"] = getattr(request.state, "csrf_token", "")
    ctx["current_user"] = getattr(request.state, "current_user", None)
    ctx["is_admin"] = getattr(request.state, "is_admin", False)
    return templates.TemplateResponse(request, template, ctx, **kwargs)


# ── Authorization helpers ─────────────────────────────────────────


def _require_admin(request: Request) -> None:
    """Raise 403 if the current user is not an admin.

    Call at the top of any route handler that mutates state (create, delete,
    trigger, approve, configure).  Read-only GET routes should remain open
    to viewers.
    """
    if not getattr(request.state, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin access required")


# ── Rate limiting ──────────────────────────────────────────────────

# In-memory rate limiting — resets on server restart.
# For persistent rate limiting, deploy behind a reverse proxy.
_rate_limits: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(ip: str, max_per_minute: int | None = None) -> bool:
    """Simple per-IP rate limiter. Returns True if request is allowed."""
    if max_per_minute is None:
        from ...config import RATE_LIMIT_DEFAULT
        max_per_minute = RATE_LIMIT_DEFAULT
    now = time.monotonic()
    _rate_limits[ip] = [t for t in _rate_limits[ip] if now - t < 60]
    if len(_rate_limits[ip]) >= max_per_minute:
        return False
    _rate_limits[ip].append(now)
    return True


# ── Markdown helpers (shared across modules) ──────────────────────


def _inline(text: str) -> str:
    """Apply inline markdown: **bold**, *italic*, `code`, and [links](url)."""
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)

    def _link(m):
        import html as html_mod
        label, url = m.group(1), m.group(2)
        if url.startswith(("http://", "https://", "http%3A", "https%3A")):
            return f'<a href="{html_mod.escape(url)}" rel="noopener" target="_blank">{html_mod.escape(label)}</a>'
        return m.group(0)

    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _link, text)
    return text


def _md_to_html(text: str) -> str:
    """Minimal markdown to HTML. Handles headers, bold, code blocks, tables, lists."""
    lines = html_mod.escape(text).split("\n")
    out = []
    in_code = False
    in_table = False
    in_ul = False
    in_ol = False

    def _close_list():
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    for line in lines:
        # Code blocks
        if line.strip().startswith("```"):
            _close_list()
            if in_code:
                out.append("</code></pre>")
                in_code = False
            else:
                out.append('<pre class="report-view"><code>')
                in_code = True
            continue
        if in_code:
            out.append(line)
            continue

        # Close table if line doesn't start with |
        if in_table and not line.strip().startswith("|"):
            out.append("</tbody></table></div>")
            in_table = False

        stripped = line.strip()

        # Skip table separator rows
        if re.match(r"^\|[\s\-:|]+\|$", stripped):
            continue

        # Table rows
        if stripped.startswith("|") and stripped.endswith("|"):
            _close_list()
            cells = [_inline(c.strip()) for c in stripped.strip("|").split("|")]
            if not in_table:
                out.append('<div class="table-wrap"><table class="table"><thead><tr>')
                out.append("".join(f"<th>{c}</th>" for c in cells))
                out.append("</tr></thead><tbody>")
                in_table = True
            else:
                out.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
            continue

        # Headers
        if stripped.startswith("### "):
            _close_list()
            out.append(f"<h4>{_inline(stripped[4:])}</h4>")
            continue
        if stripped.startswith("## "):
            _close_list()
            out.append(f"<h3>{_inline(stripped[3:])}</h3>")
            continue
        if stripped.startswith("# "):
            _close_list()
            out.append(f"<h2>{_inline(stripped[2:])}</h2>")
            continue

        # Horizontal rule
        if stripped == "---":
            _close_list()
            out.append("<hr>")
            continue

        # Numbered list items (1. 2. 3.)
        ol_match = re.match(r"^(\d+)\.\s+(.+)$", stripped)
        if ol_match:
            if in_ul:
                out.append("</ul>")
                in_ul = False
            if not in_ol:
                out.append("<ol>")
                in_ol = True
            out.append(f"<li>{_inline(ol_match.group(2))}</li>")
            continue

        # Unordered list items (- )
        if stripped.startswith("- "):
            if in_ol:
                out.append("</ol>")
                in_ol = False
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{_inline(stripped[2:])}</li>")
            continue

        # Non-list line: close any open list
        _close_list()

        # Inline formatting
        line = _inline(line)

        if stripped:
            out.append(f"<p>{line}</p>")
        else:
            out.append("")

    _close_list()
    if in_code:
        out.append("</code></pre>")
    if in_table:
        out.append("</tbody></table></div>")

    return "\n".join(out)


# ── Landing page (public, no auth) ──────────────────────────────────
@router.get("/landing", response_class=HTMLResponse)
async def landing_page(request: Request):
    """Public marketing page. Auth middleware allowlists /landing."""
    return _render(request, "landing.html", {})


# ── Import sub-routers and include them ────────────────────────────

from .activity import router as activity_router  # noqa: E402
from .alerts import router as alerts_router  # noqa: E402
from .ask import router as ask_router  # noqa: E402
from .auth import router as auth_router  # noqa: E402
from .command_center import router as command_center_router  # noqa: E402
from .metrics_page import router as metrics_page_router  # noqa: E402
from .overview import router as overview_router  # noqa: E402
from .reports import router as reports_router  # noqa: E402
from .resources import router as resources_router  # noqa: E402
from .schedules import router as schedules_router  # noqa: E402
from .sessions import router as sessions_router  # noqa: E402
from .settings import router as settings_router  # noqa: E402

router.include_router(auth_router)
router.include_router(overview_router)
router.include_router(resources_router)
router.include_router(activity_router)
router.include_router(settings_router)
router.include_router(ask_router)
router.include_router(reports_router)
router.include_router(alerts_router)
router.include_router(command_center_router)
router.include_router(sessions_router)
router.include_router(metrics_page_router)
router.include_router(schedules_router)
