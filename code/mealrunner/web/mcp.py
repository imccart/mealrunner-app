"""MCP (Model Context Protocol) endpoint for MealRunner.

Exposes MealRunner as a remote MCP server so Claude apps (Desktop, mobile,
web) can operate the app conversationally. Auth is via a personal access
token from Settings, sent as `Authorization: Bearer <token>` — the auth
middleware in app.py resolves the token to a user before we're called.

We speak the MCP "Streamable HTTP" transport (spec 2025-06-18) at its
minimum useful surface: a single POST /mcp endpoint that consumes JSON-RPC
2.0 messages and returns plain JSON responses. No SSE, no server-initiated
streams, no session ids — stateless. That's enough for tool-call
interactions, which is the whole point.

Walking-skeleton tool set: one tool (view_plan). The other 11 planned tools
(add_meal, add_grocery_item, add_staples_to_list, etc.) slot in as
additional entries in _TOOLS and dispatch branches in _call_tool once the
protocol path is proven end-to-end.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text

from mealrunner.database import get_connection


router = APIRouter()

# MCP protocol version we implement. Advertised in `initialize` response;
# clients negotiate against this.
_PROTOCOL_VERSION = "2025-06-18"

_SERVER_INFO = {
    "name": "mealrunner",
    "title": "MealRunner",
    "version": "0.1.0",
}

# Tool catalog. Each entry is emitted verbatim in tools/list responses. The
# `inputSchema` MUST be a JSON Schema object (not our internal shape).
_TOOLS: list[dict] = [
    {
        "name": "view_plan",
        "description": (
            "Show the meals planned for the next N days on the user's rolling meal "
            "plan. Returns each date with the meal name (or 'no meal planned' when "
            "the slot is empty). Use this when the user asks 'what am I eating this "
            "week', 'what's for dinner Thursday', or similar."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "days_ahead": {
                    "type": "integer",
                    "description": "How many days from today to show (1-10). Defaults to 10 (the full rolling window).",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 10,
                },
            },
            "additionalProperties": False,
        },
    },
]


# ── JSON-RPC helpers ─────────────────────────────────────

def _rpc_result(request_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _rpc_error(request_id: Any, code: int, message: str, data: Any = None) -> dict:
    err: dict = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": err}


# JSON-RPC error codes (subset — we only need the standard ones)
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603


# ── Tool implementations ────────────────────────────────

def _tool_view_plan(user_id: str, arguments: dict) -> dict:
    """Return the rolling 10-day meal plan as a text content block."""
    from mealrunner.planner import load_rolling_week

    try:
        days_ahead = int(arguments.get("days_ahead", 10))
    except (TypeError, ValueError):
        days_ahead = 10
    days_ahead = max(1, min(10, days_ahead))

    with get_connection() as conn:
        mw = load_rolling_week(conn, user_id)

    lines = [f"Meal plan {mw.start_date} through {mw.end_date} (showing next {days_ahead} days):"]
    for day in mw.all_days[:days_ahead]:
        meal = day.get("meal")
        if meal is None:
            meal_line = "no meal planned"
        else:
            # The Meal dataclass exposes `recipe_name` for library recipes and
            # `notes` for freeform "chef's night out" entries. Prefer the
            # recipe name, fall back to the notes string, then to a placeholder
            # so we always print something recognizable.
            name = (getattr(meal, "recipe_name", "") or "").strip()
            if not name:
                name = (getattr(meal, "notes", "") or "").strip() or "unnamed meal"
            sides = ", ".join(
                s.side_name for s in (getattr(meal, "sides", []) or []) if getattr(s, "side_name", "")
            )
            meal_line = f"{name} (with {sides})" if sides else name
        lines.append(f"  {day['day_short']} {day['date']} — {meal_line}")

    return {
        "content": [{"type": "text", "text": "\n".join(lines)}],
        "isError": False,
    }


def _call_tool(user_id: str, name: str, arguments: dict) -> dict:
    """Dispatch a tool call. Returns an MCP tool result dict."""
    if name == "view_plan":
        return _tool_view_plan(user_id, arguments)
    return {
        "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
        "isError": True,
    }


# ── Method dispatch ──────────────────────────────────────

def _handle_rpc(user_id: str, message: dict) -> dict | None:
    """Handle a single JSON-RPC message. Returns the response dict, or None
    for notifications (no response owed to the client)."""
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}
    is_notification = "id" not in message

    if method == "initialize":
        return _rpc_result(request_id, {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": _SERVER_INFO,
        })

    if method == "notifications/initialized" or method == "initialized":
        # Client-side "handshake complete" notification. No response.
        return None

    if method == "ping":
        return _rpc_result(request_id, {})

    if method == "tools/list":
        return _rpc_result(request_id, {"tools": _TOOLS})

    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments") or {}
        if not tool_name:
            return _rpc_error(request_id, _INVALID_PARAMS, "tools/call requires a 'name' parameter")
        try:
            result = _call_tool(user_id, tool_name, arguments)
        except Exception as e:
            # Surface tool-execution failures as tool errors (isError=True),
            # not as JSON-RPC errors. Per spec: JSON-RPC errors are for
            # protocol-level failures; tool-level failures ride inside a
            # successful RPC response with isError set.
            print(f"[mcp] Tool {tool_name} raised: {type(e).__name__}: {e}", flush=True)
            result = {
                "content": [{"type": "text", "text": f"Tool error: {type(e).__name__}: {e}"}],
                "isError": True,
            }
        return _rpc_result(request_id, result)

    if is_notification:
        # Unknown notification — spec says silently ignore.
        return None

    return _rpc_error(request_id, _METHOD_NOT_FOUND, f"Method not found: {method}")


# ── HTTP surface ────────────────────────────────────────

@router.post("/mcp")
async def mcp_post(request: Request):
    """MCP Streamable HTTP endpoint. Consumes one JSON-RPC message per POST
    (or a batch) and returns the response(s) as plain JSON.

    Content negotiation: the spec requires clients to advertise both
    application/json and text/event-stream in Accept. We always return
    application/json — SSE isn't needed for tool-call round trips."""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        # Shouldn't happen — middleware enforces auth — but guard anyway.
        return JSONResponse({"jsonrpc": "2.0", "id": None,
                             "error": {"code": _INVALID_REQUEST, "message": "Not authenticated"}},
                            status_code=401)

    try:
        raw = await request.body()
        payload = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None,
             "error": {"code": _PARSE_ERROR, "message": "Parse error"}},
            status_code=400,
        )

    if payload is None:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None,
             "error": {"code": _INVALID_REQUEST, "message": "Empty request body"}},
            status_code=400,
        )

    # Batch support: the spec allows a JSON array of messages.
    if isinstance(payload, list):
        responses = []
        for msg in payload:
            if not isinstance(msg, dict):
                responses.append({"jsonrpc": "2.0", "id": None,
                                  "error": {"code": _INVALID_REQUEST, "message": "Batch item must be an object"}})
                continue
            r = _handle_rpc(user_id, msg)
            if r is not None:
                responses.append(r)
        # If every message was a notification, return 202 with no body.
        if not responses:
            return Response(status_code=202)
        return JSONResponse(responses)

    if not isinstance(payload, dict):
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None,
             "error": {"code": _INVALID_REQUEST, "message": "Request must be a JSON object or array"}},
            status_code=400,
        )

    response = _handle_rpc(user_id, payload)
    if response is None:
        # Notification — spec says 202 Accepted with no body.
        return Response(status_code=202)
    return JSONResponse(response)


@router.get("/mcp")
async def mcp_get():
    """Server-initiated SSE stream. Not offered — we're a stateless
    request/response server, so tell the client so per spec (405)."""
    return Response(status_code=405)


@router.delete("/mcp")
async def mcp_delete():
    """Explicit session termination. We don't run sessions, so 405 signals
    that clients shouldn't expect DELETE to do anything."""
    return Response(status_code=405)
