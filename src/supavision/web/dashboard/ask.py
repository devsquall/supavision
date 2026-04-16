"""Ask Supavision — conversational access to system state.

Zero LLM cost. Score-based intent classification routes questions
to MCP tool handlers. Composers produce answer/evidence/next_step
responses that feel like an assistant, not a data dump.
"""

from __future__ import annotations

import json
import logging
import re

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse

from . import _check_rate_limit, _render

logger = logging.getLogger(__name__)

router = APIRouter()

_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4, "unknown": 5}

# Tools that return lists (not error dicts) on empty results
_LIST_TOOLS = {
    "supavision_list_resources",
}


# ── Context gathering ─────────────────────────────────────────────


def _gather_context(store) -> dict:
    """Gather current system state for the context panel (batch queries, no N+1)."""
    resources = store.list_resources()
    evals = store.get_latest_evaluations_batch()
    all_metrics = store.get_latest_metrics_batch()

    resource_summaries = []
    for r in resources[:20]:
        ev = evals.get(r.id)
        resource_summaries.append({
            "id": r.id,
            "name": r.name,
            "type": r.resource_type,
            "severity": ev.severity if ev else "unknown",
            "summary": ev.summary if ev else None,
            "metrics": all_metrics.get(r.id, {}),
        })


    return {
        "resources": resource_summaries,
    }


# ── Resource name resolution ──────────────────────────────────────


def _resolve_resource(question_lower: str, resources: list[dict]) -> str | None:
    """Word-boundary matching. Longest name first to avoid partial matches."""
    sorted_res = sorted(resources, key=lambda r: len(r["name"]), reverse=True)
    for r in sorted_res:
        name = r["name"].lower()
        if len(name) < 2:
            continue
        pattern = r"(?:^|(?<=[\s,;:!?./]))" + re.escape(name) + r"(?=$|[\s,;:!?./])"
        if re.search(pattern, question_lower):
            return r["id"]
    return None


def _resource_name(resource_id: str, context: dict) -> str:
    """Look up resource name from context."""
    for r in context["resources"]:
        if r["id"] == resource_id:
            return r["name"]
    return resource_id


# ── Score-based intent classification ─────────────────────────────


_INTENT_PATTERNS: dict[str, dict] = {
    "help": {
        "keywords": ["help", "hello", "what can you do", "how do i use this"],
        "weight": 4,
    },
    "priority": {
        "keywords": [
            "fix first", "should i fix", "priority", "prioritize",
            "triage", "most important", "worst", "most urgent",
        ],
        "weight": 3,
    },
    "problems": {
        "keywords": [
            "what's wrong", "what is wrong", "any problems", "any issues",
            "anything broken", "broken", "failing", "errors", "problems",
        ],
        "weight": 3,
    },
    "overview": {
        "keywords": [
            "status", "overview", "summary", "health",
            "how is", "how are", "show me everything",
        ],
        "weight": 1,
    },
    "metrics": {
        "keywords": [
            "metric", "metrics", "cpu", "disk", "memory", "cost",
            "usage", "performance", "capacity",
        ],
        "weight": 1,
    },
    "history": {
        "keywords": ["run history", "recent runs", "last run", "runs"],
        "weight": 1,
    },
    "report": {
        "keywords": ["report", "baseline", "discovery", "checklist"],
        "weight": 1,
    },
}


def _kw_match(keyword: str, text: str) -> bool:
    """Word-boundary keyword matching."""
    pattern = r"(?:^|(?<=\s))" + re.escape(keyword) + r"(?=\s|$|[.,;:!?])"
    return bool(re.search(pattern, text))


def _classify_intent(question: str) -> str:
    q = question.lower()
    best_intent = "fallback"
    best_score = 0

    for intent, conf in _INTENT_PATTERNS.items():
        score = 0
        for kw in conf["keywords"]:
            if _kw_match(kw, q):
                score += len(kw.split()) * conf["weight"]
        if score == 0:
            continue
        if score > best_score or (score == best_score and conf["weight"] > (
            _INTENT_PATTERNS.get(best_intent, {}).get("weight", 0)
        )):
            best_score = score
            best_intent = intent

    return best_intent


def _detect_modifiers(question: str) -> dict:
    q = question.lower()
    mods: dict = {}
    for sev in ("critical", "high", "medium", "low", "info"):
        if sev in q:
            mods["severity"] = sev
            break
    for stage in ("scanned", "evaluated", "dismissed", "created"):
        if stage in q:
            mods["stage"] = stage
            break
    return mods



def _build_calls(
    intent: str, modifiers: dict, resource_id: str | None,
    context: dict, question: str = "",
) -> list[dict]:
    calls: list[dict] = []

    if intent == "help":
        return []

    elif intent == "priority":
        calls.append({"tool": "supavision_list_resources", "args": {}})

    elif intent == "problems":
        calls.append({"tool": "supavision_list_resources", "args": {}})

    elif intent == "overview":
        calls.append({"tool": "supavision_list_resources", "args": {}})
        if resource_id:
            calls.append({"tool": "supavision_get_metrics", "args": {"resource_id": resource_id}})

    elif intent == "metrics":
        if resource_id:
            calls.append({"tool": "supavision_get_metrics", "args": {"resource_id": resource_id}})
        else:
            for r in context["resources"][:5]:
                calls.append({"tool": "supavision_get_metrics", "args": {"resource_id": r["id"]}})

    elif intent == "history":
        if resource_id:
            calls.append({"tool": "supavision_get_run_history", "args": {"resource_id": resource_id}})
        else:
            calls.append({"tool": "supavision_list_resources", "args": {}})

    elif intent == "report":
        if resource_id:
            calls.append({"tool": "supavision_get_latest_report", "args": {"resource_id": resource_id}})
        else:
            calls.append({"tool": "supavision_list_resources", "args": {}})

    else:
        if resource_id:
            calls.append({"tool": "supavision_get_latest_report", "args": {"resource_id": resource_id}})
            calls.append({"tool": "supavision_get_metrics", "args": {"resource_id": resource_id}})
            calls.append({"tool": "supavision_get_run_history", "args": {"resource_id": resource_id}})
        else:
            calls.append({"tool": "supavision_list_resources", "args": {}})

    return calls


# ── MCP tool execution ────────────────────────────────────────────


def _execute_mcp_calls(store, calls: list[dict]) -> list[dict]:
    """Execute MCP tool calls. Normalizes error-dicts from list-type tools to []."""
    import sqlite3 as _sqlite3

    from ...mcp import _HANDLERS

    if not calls:
        return []

    conn = _sqlite3.connect(f"file:{store.db_path}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = _sqlite3.Row
    results: list[dict] = []
    try:
        for call in calls:
            handler = _HANDLERS.get(call["tool"])
            if not handler:
                continue
            try:
                raw = handler(conn, call["args"])
                data = json.loads(raw)
                if call["tool"] in _LIST_TOOLS and isinstance(data, dict) and "error" in data:
                    data = []
                results.append({"tool": call["tool"], "args": call["args"], "data": data})
            except Exception as e:
                logger.warning("MCP tool %s failed: %s", call["tool"], e)
    finally:
        conn.close()
    return results


# ── Result helpers ────────────────────────────────────────────────


def _get_all_data(results: list[dict], tool_name: str) -> list:
    merged: list = []
    for r in results:
        if r["tool"] == tool_name and isinstance(r.get("data"), list):
            merged.extend(r["data"])
    return merged


def _get_single_data(results: list[dict], tool_name: str) -> dict | None:
    for r in results:
        if r["tool"] == tool_name and isinstance(r.get("data"), dict):
            if not r["data"].get("error"):
                return r["data"]
    return None


def _resource_chip(r: dict) -> dict:
    """Build an evidence chip from a resource."""
    return {
        "label": r.get("name", "?"),
        "detail": r.get("severity", "unknown"),
        "link": f"/resources/{r['id']}" if r.get("id") else None,
    }


# ── Intent-specific composers ─────────────────────────────────────


def _compose_help(_results: list, context: dict) -> dict:
    return {
        "answer": "Hey! I can help you explore your Supavision data. Try one of these:",
        "evidence": [],
        "next_step": None,
        "sections": [{"type": "help", "title": "Example Questions", "data": [
            "What's wrong with my system?",
            "What should I fix first?",
            "Show me metrics for my server",
            "What's the status of everything?",
        ]}],
    }


def _compose_priority(results: list, context: dict) -> dict:
    resources = _get_all_data(results, "supavision_list_resources")
    unhealthy = [r for r in resources if r.get("severity") not in ("healthy", "unknown", None)]
    unhealthy.sort(key=lambda r: _SEV_ORDER.get(r.get("severity", "unknown"), 5))

    if not unhealthy:
        return {
            "answer": "Nothing urgent. All resources are healthy.",
            "evidence": [_resource_chip(r) for r in resources[:3]] if resources else [],
            "next_step": None, "sections": [],
        }

    first = unhealthy[0]
    answer = f"Start with {first.get('name', '?')} ({first.get('severity', 'unknown')})."
    if len(unhealthy) > 1:
        answer += f" Then {len(unhealthy) - 1} more resource(s) need attention."
    return {
        "answer": answer,
        "evidence": [_resource_chip(r) for r in unhealthy[:5]],
        "next_step": "Click a resource to investigate.",
        "sections": [{"type": "resources", "title": "By Priority", "data": unhealthy}],
    }


def _compose_problems(results: list, context: dict) -> dict:
    resources = _get_all_data(results, "supavision_list_resources")
    unhealthy = [r for r in resources if r.get("severity") not in ("healthy", "unknown", None)]

    if not unhealthy:
        evidence = [_resource_chip(r) for r in resources[:3]] if resources else []
        return {"answer": "Nothing urgent. All resources are healthy.", "evidence": evidence,
                "next_step": None, "sections": []}

    names = ", ".join(r.get("name", "?") for r in unhealthy)
    answer = (f"{len(unhealthy)} resource{'s need' if len(unhealthy) != 1 else ' needs'} "
              f"attention: {names}.")
    return {
        "answer": answer,
        "evidence": [_resource_chip(r) for r in unhealthy[:5]],
        "next_step": "Click a resource to investigate.",
        "sections": [{"type": "resources", "title": "Unhealthy Resources", "data": unhealthy}],
    }


def _compose_overview(results: list, context: dict) -> dict:
    resources = _get_all_data(results, "supavision_list_resources")
    if not resources:
        return {"answer": "No resources configured yet. Add a server to get started.",
                "evidence": [], "next_step": "Go to Resources and add your first one.", "sections": []}

    n = len(resources)
    by_sev: dict[str, int] = {}
    for r in resources:
        s = r.get("severity") or "unknown"
        by_sev[s] = by_sev.get(s, 0) + 1
    issues = {k: v for k, v in by_sev.items() if k not in ("healthy", "unknown")}

    answer = f"{n} resource{'s' if n != 1 else ''} monitored"
    if issues:
        answer += f" — {', '.join(f'{v} {k}' for k, v in issues.items())}."
    else:
        answer += ", all healthy."

    evidence = [_resource_chip(r) for r in resources[:5]]
    sections: list[dict] = []
    if len(resources) > 5:
        sections.append({"type": "resources", "title": "All Resources", "data": resources})
    return {"answer": answer, "evidence": evidence, "next_step": None, "sections": sections}


def _compose_metrics(results: list, context: dict) -> dict:
    all_metrics: list[dict] = []
    for r in results:
        if r["tool"] == "supavision_get_metrics" and isinstance(r.get("data"), dict):
            if not r["data"].get("error"):
                all_metrics.append(r)

    if not all_metrics:
        return {"answer": "No metrics available. Run a health check to start collecting.",
                "evidence": [], "next_step": "Go to a resource and click Diagnose.", "sections": []}

    evidence: list[dict] = []
    sections: list[dict] = []
    answer_parts: list[str] = []
    for r in all_metrics[:3]:
        res_id = r["args"].get("resource_id", "")
        d = r["data"]
        for k, v in list(d.items())[:3]:
            val = v.get("value", v) if isinstance(v, dict) else v
            unit = v.get("unit", "") if isinstance(v, dict) else ""
            evidence.append({"label": k, "detail": f"{val}{' ' + unit if unit else ''}"})
            answer_parts.append(f"{k}: {val}{' ' + unit if unit else ''}")
        sections.append({"type": "metrics", "title": f"Metrics ({res_id})", "data": d})

    return {"answer": ", ".join(answer_parts[:5]) + "." if answer_parts else "Metrics collected.",
            "evidence": evidence[:5], "next_step": None, "sections": sections}


def _compose_resource_detail(results: list, context: dict) -> dict:
    report = _get_single_data(results, "supavision_get_latest_report")
    metrics = _get_single_data(results, "supavision_get_metrics")
    runs = _get_all_data(results, "supavision_get_run_history")

    parts: list[str] = []
    evidence: list[dict] = []
    sections: list[dict] = []
    if report:
        sev = report.get("severity", "unknown")
        summary = report.get("summary", "")
        parts.append(f"Status: {sev}." + (f" {summary}" if summary else ""))
        sections.append({"type": "report", "title": "Latest Report", "data": report})
    if metrics and isinstance(metrics, dict):
        for k, v in list(metrics.items())[:3]:
            val = v.get("value", v) if isinstance(v, dict) else v
            evidence.append({"label": k, "detail": str(val)})
        sections.append({"type": "metrics", "title": "Metrics", "data": metrics})
    if runs:
        last = runs[0]
        parts.append(f"Last run: {last.get('run_type', '?')} — {last.get('status', '?')}.")
        sections.append({"type": "run_history", "title": "Run History", "data": runs})
    if not parts:
        parts.append("No data available for this resource yet.")
    return {"answer": " ".join(parts), "evidence": evidence[:5], "next_step": None, "sections": sections}


def _compose_generic(results: list, context: dict) -> dict:
    sections: list[dict] = []
    evidence: list[dict] = []
    parts: list[str] = []

    for r in results:
        tool = r["tool"]
        data = r.get("data")
        if not data:
            continue
        if tool == "supavision_get_run_history" and isinstance(data, list) and data:
            parts.append(f"{len(data)} recent runs.")
            sections.append({"type": "run_history", "title": "Run History", "data": data})
        elif tool == "supavision_get_latest_report" and isinstance(data, dict):
            sev = data.get("severity", "")
            parts.append(f"Report severity: {sev}." if sev else "Report available.")
            sections.append({"type": "report", "title": "Report", "data": data})
        elif tool == "supavision_list_resources" and isinstance(data, list) and data:
            parts.append(f"{len(data)} resources.")
            evidence.extend(_resource_chip(r) for r in data[:3])
            sections.append({"type": "resources", "title": "Resources", "data": data})

    if not parts:
        return {
            "answer": "I'm not sure how to answer that. Try one of these:",
            "evidence": [], "next_step": None,
            "sections": [{"type": "help", "title": "Example Questions", "data": [
                "What's wrong with my system?",
                "What should I fix first?",
                "Show me the latest report",
            ]}],
        }

    return {"answer": " ".join(parts), "evidence": evidence[:5], "next_step": None, "sections": sections}


_COMPOSERS: dict[str, object] = {
    "help": _compose_help,
    "priority": _compose_priority,
    "problems": _compose_problems,
    "overview": _compose_overview,
    "metrics": _compose_metrics,
    "resource_detail": _compose_resource_detail,
}


# ── Routes ────────────────────────────────────────────────────────


@router.get("/ask", response_class=HTMLResponse)
async def ask_page(request: Request):
    store = request.app.state.store
    context = _gather_context(store)
    return _render(request, "ask.html", {"context": context})


@router.post("/api/mcp/query")
async def mcp_query(request: Request):
    """Query Supavision data. No LLM, zero cost."""
    if not getattr(request.state, "current_user", None):
        return Response(content=json.dumps({"error": "Authentication required"}),
                        status_code=401, media_type="application/json")

    ip = request.client.host if request.client else "unknown"
    from ...config import RATE_LIMIT_ASK
    if not _check_rate_limit(ip, max_per_minute=RATE_LIMIT_ASK):
        return Response(content=json.dumps({"error": "Rate limit exceeded. Try again in a minute."}),
                        status_code=429, media_type="application/json")

    try:
        body = await request.json()
    except Exception:
        return Response(content=json.dumps({"error": "Invalid JSON body"}),
                        status_code=400, media_type="application/json")

    question = (body.get("question") or "").strip()
    if not question:
        return Response(content=json.dumps({"error": "question is required"}),
                        status_code=400, media_type="application/json")
    if len(question) > 2000:
        return Response(content=json.dumps({"error": "Question too long (max 2000 chars)"}),
                        status_code=400, media_type="application/json")

    store = request.app.state.store
    context = _gather_context(store)

    q_lower = question.lower()
    intent = _classify_intent(question)
    resource_id = _resolve_resource(q_lower, context["resources"])
    modifiers = _detect_modifiers(question)

    if intent == "fallback" and resource_id:
        intent = "resource_detail"

    calls = _build_calls(intent, modifiers, resource_id, context, question)
    results = _execute_mcp_calls(store, calls)

    composer = _COMPOSERS.get(intent, _compose_generic)
    response = composer(results, context)

    return Response(
        content=json.dumps({
            "question": question,
            "answer": response.get("answer", ""),
            "evidence": response.get("evidence", []),
            "next_step": response.get("next_step"),
            "sections": response.get("sections", []),
            "context": context,
        }),
        status_code=200,
        media_type="application/json",
    )
