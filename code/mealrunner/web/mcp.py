"""MCP (Model Context Protocol) endpoint for MealRunner.

Exposes MealRunner as a remote MCP server so Claude apps (Desktop, mobile,
web) can operate the app conversationally. Auth is via OAuth 2.1 access
tokens issued through the /oauth/* flow, or personal access tokens for
CLI use. The auth middleware in app.py resolves the token to a user
before we're called.

We speak the MCP "Streamable HTTP" transport (spec 2025-06-18) at its
minimum useful surface: a single POST /mcp endpoint that consumes JSON-RPC
2.0 messages and returns plain JSON responses. No SSE, no server-initiated
streams, no session ids — stateless.

Tool design principles (see the design conversation for the full context):
  - Voice-first: tool names and descriptions read like verbs a human would say
  - No config mutation: tools operate on the current plan / current grocery
    list / current order. They do NOT add/remove recipes from the library,
    staples from the staples config, etc.
  - Freeform additions welcome: add_meal accepts names not in the library
    and creates a freeform meal (returns a note that it's not a stored recipe).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
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
    "version": "0.2.0",
}


# ── Tool catalog ────────────────────────────────────────
#
# Each entry is emitted verbatim in tools/list responses. Descriptions
# are written for Claude to route natural language to the right tool —
# they should say WHEN to call the tool, not just what it does.

_TOOLS: list[dict] = [
    # Plan — read
    {
        "name": "view_plan",
        "description": (
            "Show the meals planned for the next N days on the user's rolling meal "
            "plan. Returns each date with the meal name and any sides. Use this when "
            "the user asks 'what am I eating this week', 'what's for dinner Thursday', "
            "'show me the meal plan', or similar."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "days_ahead": {
                    "type": "integer",
                    "description": "How many days from today to show (1-10). Defaults to 10.",
                    "minimum": 1, "maximum": 10, "default": 10,
                },
            },
            "additionalProperties": False,
        },
    },
    # Plan — write
    {
        "name": "add_meal",
        "description": (
            "Add a meal to a specific date on the rolling plan. If the name matches "
            "a recipe in the user's library, that recipe is linked; otherwise the "
            "meal is added as a freeform entry (the tool returns a note when this "
            "happens). Use for 'add tacos Thursday', 'plan spaghetti for tomorrow', etc."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "ISO date (YYYY-MM-DD). Must be within the rolling 10-day window (today or up to 9 days out).",
                },
                "name": {
                    "type": "string",
                    "description": "The meal name — 'tacos', 'spaghetti', etc. Matched case-insensitively against the recipe library.",
                },
            },
            "required": ["date", "name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "suggest_meal_plan",
        "description": (
            "Fill empty slots in the rolling plan with random meals drawn from the "
            "user's recipe library, avoiding recently-cooked meals and weighted "
            "toward familiar favorites. Use when the user says 'suggest a meal plan', "
            "'fill in the week', 'pick meals for the next N days'. Only fills days "
            "that don't already have a meal."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "How many days from start_date to fill (1-10). Defaults to 5.",
                    "minimum": 1, "maximum": 10, "default": 5,
                },
                "start_date": {
                    "type": "string",
                    "description": "ISO date (YYYY-MM-DD) to start filling from. Defaults to today.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "swap_meal",
        "description": (
            "Replace the meal on a specific date with a fresh random suggestion from "
            "the recipe library (avoiding recently-cooked meals). Use when the user "
            "says 'suggest a different meal for Wednesday', 'swap Thursday', 'give "
            "me something else for tomorrow'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "ISO date (YYYY-MM-DD) of the meal to replace.",
                },
            },
            "required": ["date"],
            "additionalProperties": False,
        },
    },
    {
        "name": "remove_meal",
        "description": (
            "Remove the meal from a specific date, leaving that slot empty. Use for "
            "'cancel dinner Wednesday', 'we're going out Thursday, clear it', "
            "'remove the meal from Friday'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "ISO date (YYYY-MM-DD)."},
            },
            "required": ["date"],
            "additionalProperties": False,
        },
    },
    # Grocery — read
    {
        "name": "view_grocery_list",
        "description": (
            "Show the user's current grocery list, grouped by shopping section "
            "(Produce, Dairy, etc.). Use when the user asks 'what's on my list', "
            "'show my grocery list', 'what am I buying'."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    # Grocery — write
    {
        "name": "add_grocery_item",
        "description": (
            "Add a freeform item to the user's grocery list. Deduplicates against "
            "existing active items. Use for 'add potatoes to my list', 'put milk "
            "on the grocery list', 'I need to buy paper towels'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The item name — 'potatoes', 'milk', etc."},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "add_staples_to_list",
        "description": (
            "Add all the user's configured 'every trip' staples to the current "
            "grocery list. Skips items already on the list. Use for 'add my staples', "
            "'add my usuals', 'load up my regular items'."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    # Config — read only
    {
        "name": "list_staples",
        "description": (
            "List the user's configured staples — items they buy every trip and "
            "items they keep on hand. This is the configuration, not what's on "
            "this week's list. Use when the user asks 'what are my staples', "
            "'what do I usually buy', 'show my regulars'."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    # Order — write
    {
        "name": "select_defaults",
        "description": (
            "For every unpicked item on the order page, auto-select the user's most-"
            "picked past product for that item (requires 3+ prior selections and "
            "current Kroger availability). Use for 'pick my usuals', 'select "
            "defaults', 'auto-fill my order'."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "submit_to_kroger",
        "description": (
            "Push the user's currently-selected products to the Kroger cart. The "
            "user still needs to complete checkout on Kroger's site. Use for 'send "
            "to Kroger', 'submit my order', 'push it to Kroger cart'. Only works "
            "when the user has linked their Kroger account."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
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


_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603


# ── Tool result helpers ─────────────────────────────────

def _text_result(text_body: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text_body}], "isError": is_error}


def _in_rolling_window(iso_date: str) -> bool:
    try:
        d = date.fromisoformat(iso_date)
    except ValueError:
        return False
    today = date.today()
    return today <= d <= today + timedelta(days=9)


# ── Tool implementations ────────────────────────────────

def _tool_view_plan(user_id: str, arguments: dict) -> dict:
    from mealrunner.planner import load_rolling_week
    try:
        days_ahead = int(arguments.get("days_ahead", 10))
    except (TypeError, ValueError):
        days_ahead = 10
    days_ahead = max(1, min(10, days_ahead))

    with get_connection() as conn:
        mw = load_rolling_week(conn, user_id)

    lines = [f"Meal plan {mw.start_date} through {mw.end_date} (next {days_ahead} days):"]
    for day in mw.all_days[:days_ahead]:
        meal = day.get("meal")
        if meal is None:
            meal_line = "no meal planned"
        else:
            name = (getattr(meal, "recipe_name", "") or "").strip()
            if not name:
                name = (getattr(meal, "notes", "") or "").strip() or "unnamed meal"
            sides = ", ".join(
                s.side_name for s in (getattr(meal, "sides", []) or []) if getattr(s, "side_name", "")
            )
            meal_line = f"{name} (with {sides})" if sides else name
        lines.append(f"  {day['day_short']} {day['date']} — {meal_line}")

    return _text_result("\n".join(lines))


def _tool_add_meal(user_id: str, arguments: dict) -> dict:
    from mealrunner.planner import set_meal, set_freeform_meal
    from mealrunner.recipes import filter_recipes

    iso = (arguments.get("date") or "").strip()
    name = (arguments.get("name") or "").strip()
    if not iso or not name:
        return _text_result("date and name are required.", is_error=True)
    if not _in_rolling_window(iso):
        return _text_result(
            f"Date {iso} is outside the rolling 10-day window. Only today plus the next 9 days can be planned.",
            is_error=True,
        )

    with get_connection() as conn:
        # Case-insensitive match against the user's own recipe library.
        recipes = filter_recipes(conn, user_id=user_id)
        match = next((r for r in recipes if r.name.strip().lower() == name.lower()), None)
        if match:
            set_meal(conn, user_id, iso, match.name)
            return _text_result(f"Added {match.name} for {iso}.")
        # Freeform entry — writes to the meals table but no recipe_id link.
        set_freeform_meal(conn, user_id, iso, name)
        return _text_result(
            f"Added '{name}' for {iso} as a freeform meal — no recipe on file for that name."
        )


def _tool_suggest_meal_plan(user_id: str, arguments: dict) -> dict:
    from mealrunner.planner import load_rolling_week, surprise_pick, set_meal

    try:
        days = int(arguments.get("days", 5))
    except (TypeError, ValueError):
        days = 5
    days = max(1, min(10, days))

    start_str = (arguments.get("start_date") or "").strip()
    if start_str:
        try:
            start_d = date.fromisoformat(start_str)
        except ValueError:
            return _text_result(f"start_date '{start_str}' is not a valid ISO date.", is_error=True)
    else:
        start_d = date.today()

    if not _in_rolling_window(start_d.isoformat()):
        return _text_result(
            "start_date must be within the rolling 10-day window (today plus 9 days).",
            is_error=True,
        )

    added: list[str] = []
    skipped: list[str] = []
    with get_connection() as conn:
        mw = load_rolling_week(conn, user_id)
        planned_dates = {m.slot_date for m in mw.meals}
        # Avoid re-picking the same recipe twice in one suggestion pass.
        chosen_recipe_ids: set[int] = set(
            m.recipe_id for m in mw.meals if m.recipe_id is not None
        )
        for i in range(days):
            d = start_d + timedelta(days=i)
            iso = d.isoformat()
            if not _in_rolling_window(iso):
                break
            if iso in planned_dates:
                skipped.append(iso)
                continue
            pick = surprise_pick(conn, user_id, iso, exclude_ids=chosen_recipe_ids)
            if not pick:
                skipped.append(f"{iso} (no eligible recipe)")
                continue
            recipe_name = pick["meal"]["name"]
            side = pick.get("side")
            sides = [{"side_name": side, "side_recipe_id": None}] if side else None
            set_meal(conn, user_id, iso, recipe_name, sides=sides)
            chosen_recipe_ids.add(pick["meal"]["id"])
            added.append(f"{iso}: {recipe_name}" + (f" (with {side})" if side else ""))

    if not added:
        return _text_result(
            "No meals added. " + ("All requested days were already planned." if skipped else "No eligible recipes in your library."),
        )
    lines = [f"Added {len(added)} meal{'s' if len(added) != 1 else ''}:"]
    lines.extend(f"  {a}" for a in added)
    if skipped:
        lines.append(f"Skipped {len(skipped)} (already planned or no candidate).")
    return _text_result("\n".join(lines))


def _tool_swap_meal(user_id: str, arguments: dict) -> dict:
    from mealrunner.planner import surprise_pick, set_meal, remove_meal as do_remove_meal
    iso = (arguments.get("date") or "").strip()
    if not iso or not _in_rolling_window(iso):
        return _text_result(
            "A date within the rolling 10-day window is required.", is_error=True,
        )
    with get_connection() as conn:
        existing = conn.execute(
            text("SELECT recipe_name FROM meals WHERE user_id = :u AND slot_date = :d"),
            {"u": user_id, "d": iso},
        ).fetchone()
        old_name = existing["recipe_name"] if existing else None
        do_remove_meal(conn, user_id, iso)
        pick = surprise_pick(conn, user_id, iso)
        if not pick:
            return _text_result(
                f"Removed the meal on {iso}, but no eligible recipe was available to swap in. The slot is empty.",
            )
        recipe_name = pick["meal"]["name"]
        side = pick.get("side")
        sides = [{"side_name": side, "side_recipe_id": None}] if side else None
        set_meal(conn, user_id, iso, recipe_name, sides=sides)

    if old_name:
        line = f"Swapped {old_name} for {recipe_name} on {iso}"
    else:
        line = f"Added {recipe_name} for {iso} (that slot was empty)"
    if side:
        line += f" (with {side})"
    return _text_result(line + ".")


def _tool_remove_meal(user_id: str, arguments: dict) -> dict:
    from mealrunner.planner import remove_meal as do_remove_meal
    iso = (arguments.get("date") or "").strip()
    if not iso:
        return _text_result("date is required.", is_error=True)
    with get_connection() as conn:
        existing = conn.execute(
            text("SELECT recipe_name FROM meals WHERE user_id = :u AND slot_date = :d"),
            {"u": user_id, "d": iso},
        ).fetchone()
        if not existing:
            return _text_result(f"No meal was planned for {iso}.")
        do_remove_meal(conn, user_id, iso)
    return _text_result(f"Removed {existing['recipe_name'] or 'the meal'} from {iso}.")


def _tool_view_grocery_list(user_id: str, arguments: dict) -> dict:
    with get_connection() as conn:
        rows = conn.execute(
            text("""SELECT name, shopping_group, quantity
                    FROM grocery_items
                    WHERE user_id = :uid AND status = 'active' AND buy_elsewhere = 0
                    ORDER BY shopping_group, name"""),
            {"uid": user_id},
        ).fetchall()
    if not rows:
        return _text_result("Your grocery list is empty.")

    by_group: dict[str, list[str]] = {}
    for r in rows:
        group = r["shopping_group"] or "Other"
        qty = r["quantity"] if r["quantity"] and r["quantity"] > 1 else None
        display = f"{r['name']} x{qty}" if qty else r["name"]
        by_group.setdefault(group, []).append(display)

    lines = [f"Grocery list ({len(rows)} item{'s' if len(rows) != 1 else ''}):"]
    for group in sorted(by_group.keys()):
        lines.append(f"\n{group}:")
        for item in by_group[group]:
            lines.append(f"  • {item}")
    return _text_result("\n".join(lines))


def _tool_add_grocery_item(user_id: str, arguments: dict) -> dict:
    from mealrunner.normalize import compare_key, resolve_user_canonical
    from mealrunner.web.api import _normalize_name, _infer_item_group

    raw = (arguments.get("name") or "").strip()
    if not raw:
        return _text_result("An item name is required.", is_error=True)

    with get_connection() as conn:
        name, ingredient_id = _normalize_name(conn, raw)
        if ingredient_id is None:
            name = resolve_user_canonical(conn, user_id, name)
        key = compare_key(name)

        active = conn.execute(
            text("SELECT name FROM grocery_items WHERE user_id = :uid AND status = 'active'"),
            {"uid": user_id},
        ).fetchall()
        if any(compare_key(r["name"]) == key for r in active):
            return _text_result(f"{name} is already on your grocery list.")

        group = _infer_item_group(conn, name, user_id)
        conn.execute(
            text("""INSERT INTO grocery_items
                    (user_id, name, shopping_group, source, for_meals, meal_count)
                    VALUES (:uid, :name, :grp, 'extra', '', 0)"""),
            {"uid": user_id, "name": name, "grp": group},
        )
        conn.commit()
    return _text_result(f"Added {name} to your grocery list.")


def _tool_add_staples_to_list(user_id: str, arguments: dict) -> dict:
    from mealrunner.staples import list_staples, EVERY_TRIP
    from mealrunner.normalize import compare_key
    from mealrunner.web.api import _infer_item_group

    with get_connection() as conn:
        staples = list_staples(conn, user_id, mode=EVERY_TRIP)
        if not staples:
            return _text_result("You don't have any 'every trip' staples configured.")

        active = conn.execute(
            text("SELECT name FROM grocery_items WHERE user_id = :uid AND status = 'active'"),
            {"uid": user_id},
        ).fetchall()
        on_list = {compare_key(r["name"]) for r in active}

        added: list[str] = []
        skipped: list[str] = []
        for s in staples:
            n = s.name.lower()
            if compare_key(n) in on_list:
                skipped.append(s.name)
                continue
            group = _infer_item_group(conn, n, user_id)
            conn.execute(
                text("""INSERT INTO grocery_items
                        (user_id, name, shopping_group, source, for_meals, meal_count)
                        VALUES (:uid, :name, :grp, 'regular', '', 0)"""),
                {"uid": user_id, "name": n, "grp": group},
            )
            added.append(s.name)
        conn.commit()

    parts = []
    if added:
        parts.append(f"Added {len(added)} staple{'s' if len(added) != 1 else ''}: {', '.join(added)}.")
    if skipped:
        parts.append(f"Skipped {len(skipped)} already on the list.")
    return _text_result(" ".join(parts) or "Nothing to add.")


def _tool_list_staples(user_id: str, arguments: dict) -> dict:
    from mealrunner.staples import list_staples, EVERY_TRIP, KEEP_ON_HAND
    with get_connection() as conn:
        every_trip = list_staples(conn, user_id, mode=EVERY_TRIP)
        keep_on_hand = list_staples(conn, user_id, mode=KEEP_ON_HAND)
    if not every_trip and not keep_on_hand:
        return _text_result("You don't have any staples configured yet.")
    lines: list[str] = []
    if every_trip:
        lines.append(f"Every-trip staples ({len(every_trip)}):")
        lines.extend(f"  • {s.name}" for s in every_trip)
    if keep_on_hand:
        if lines:
            lines.append("")
        lines.append(f"Keep-on-hand staples ({len(keep_on_hand)}):")
        lines.extend(f"  • {s.name}" for s in keep_on_hand)
    return _text_result("\n".join(lines))


def _tool_select_defaults(user_id: str, arguments: dict) -> dict:
    """Mirror the /order/select-defaults endpoint logic, minus the async
    Kroger fanout — for the MCP path we keep it synchronous."""
    from mealrunner.kroger import search_products_fast, save_preference
    from mealrunner.stores import get_kroger_location_id
    from mealrunner.planner import load_rolling_week
    from mealrunner.web.api import _ensure_active_trip

    with get_connection() as conn:
        location_id = get_kroger_location_id(conn, user_id)
        if not location_id:
            return _text_result(
                "You need to link a Kroger account (and select a store) in Preferences before this can auto-fill.",
                is_error=True,
            )
        mw = load_rolling_week(conn, user_id)
        _ensure_active_trip(conn, mw, user_id)

        pending = conn.execute(
            text("""SELECT name FROM grocery_items
                    WHERE user_id = :uid AND status = 'active'
                      AND product_upc = '' AND buy_elsewhere = 0
                    ORDER BY name"""),
            {"uid": user_id},
        ).fetchall()
        if not pending:
            return _text_result("Nothing to fill — every item on your list already has a product picked.")

        selected = 0
        no_history = 0
        unavailable = 0
        for r in pending:
            item_name = r["name"]
            # Recency-first: 3+ picks in the last 60 days beats stale but frequent.
            top = conn.execute(
                text("""SELECT search_term, upc FROM product_preferences
                        WHERE user_id = :uid AND LOWER(search_term) = LOWER(:name)
                          AND times_picked >= 3 AND upc != ''
                          AND last_picked > NOW() - INTERVAL '60 days'
                        ORDER BY times_picked DESC, last_picked DESC LIMIT 1"""),
                {"uid": user_id, "name": item_name},
            ).fetchone()
            if not top:
                top = conn.execute(
                    text("""SELECT search_term, upc FROM product_preferences
                            WHERE user_id = :uid AND LOWER(search_term) = LOWER(:name)
                              AND times_picked >= 3 AND upc != ''
                            ORDER BY times_picked DESC, last_picked DESC LIMIT 1"""),
                    {"uid": user_id, "name": item_name},
                ).fetchone()
            if not top:
                no_history += 1
                continue
            try:
                products = search_products_fast(
                    term=top["search_term"], limit=50,
                    fulfillment="curbside", location_id=location_id,
                )
            except Exception:
                unavailable += 1
                continue
            match = next((p for p in products if p.upc == top["upc"]), None)
            if not match:
                unavailable += 1
                continue

            qty_row = conn.execute(
                text("""SELECT MODE() WITHIN GROUP (ORDER BY quantity) AS qty
                        FROM grocery_items
                        WHERE user_id = :uid AND LOWER(name) = LOWER(:name)
                          AND product_upc = :upc AND submitted_at IS NOT NULL
                          AND quantity > 0"""),
                {"uid": user_id, "name": item_name, "upc": match.upc},
            ).fetchone()
            qty = int(qty_row["qty"]) if qty_row and qty_row["qty"] else 1

            conn.execute(
                text("""UPDATE grocery_items SET
                           product_upc = :upc, product_name = :name,
                           product_brand = :brand, product_size = :size,
                           product_price = :price, product_image = :image,
                           quantity = :quantity,
                           ordered = 1, ordered_at = CURRENT_TIMESTAMP,
                           selected_at = CURRENT_TIMESTAMP,
                           receipt_status = '', receipt_acknowledged = 0,
                           receipt_item = '', receipt_upc = '', receipt_price = NULL,
                           status = 'active'
                       WHERE user_id = :uid AND LOWER(name) = :item_name
                         AND status = 'active' AND product_upc = ''"""),
                {"upc": match.upc, "name": match.description, "brand": match.brand,
                 "size": match.size, "price": match.price, "image": match.image_url,
                 "quantity": qty,
                 "uid": user_id, "item_name": item_name.lower()},
            )
            save_preference(conn, user_id, item_name, match, source="auto-default")
            selected += 1
        conn.commit()

    total = len(pending)
    return _text_result(
        f"Filled {selected} of {total} from your history. "
        f"{no_history} had no qualifying history; {unavailable} weren't currently available."
    )


def _tool_submit_to_kroger(user_id: str, arguments: dict) -> dict:
    """Push currently-selected products to the Kroger cart. Mirrors the
    core logic of /order/submit (fetches the user's DB-stored token, adds
    to cart, marks rows as ordered), skipping the household-account-picker
    branch — the MCP client is always operating as its own user."""
    from mealrunner.kroger import add_to_cart, get_user_token_from_db
    from mealrunner.stores import get_kroger_location_id

    with get_connection() as conn:
        location_id = get_kroger_location_id(conn, user_id)
        if not location_id:
            return _text_result(
                "You need to link a Kroger account and select a store before submitting.",
                is_error=True,
            )
        token = get_user_token_from_db(conn, user_id)
        if not token:
            return _text_result(
                "Your Kroger link needs to be re-authorized. Open Preferences → Online Store Integrations to reconnect.",
                is_error=True,
            )
        # Same chokepoint filters /order/submit uses — avoids double-sending
        # rows already submitted, or in a closed state.
        rows = conn.execute(
            text("""SELECT product_upc, quantity FROM grocery_items
                    WHERE user_id = :uid AND status = 'active'
                      AND product_upc != '' AND ordered = 1 AND submitted_at IS NULL
                      AND checked = 0 AND have_it = 0 AND removed = 0
                      AND COALESCE(receipt_status, '') = ''"""),
            {"uid": user_id},
        ).fetchall()
        if not rows:
            return _text_result(
                "Nothing to submit — either your order is empty or everything has already been sent."
            )
        # add_to_cart wants {'upc': str, 'qty': int}, not 'quantity'
        items = [{"upc": r["product_upc"], "qty": r["quantity"] or 1} for r in rows]
        try:
            add_to_cart(items, token=token)
        except Exception as e:
            return _text_result(f"Kroger cart submission failed: {e}", is_error=True)
        conn.execute(
            text("""UPDATE grocery_items SET status = 'ordered',
                       submitted_at = CURRENT_TIMESTAMP
                    WHERE user_id = :uid AND status = 'active'
                      AND product_upc != '' AND submitted_at IS NULL"""),
            {"uid": user_id},
        )
        conn.commit()
    return _text_result(
        f"Sent {len(items)} item{'s' if len(items) != 1 else ''} to your Kroger cart. Finish checkout at kroger.com."
    )


def _call_tool(user_id: str, name: str, arguments: dict) -> dict:
    dispatch = {
        "view_plan": _tool_view_plan,
        "add_meal": _tool_add_meal,
        "suggest_meal_plan": _tool_suggest_meal_plan,
        "swap_meal": _tool_swap_meal,
        "remove_meal": _tool_remove_meal,
        "view_grocery_list": _tool_view_grocery_list,
        "add_grocery_item": _tool_add_grocery_item,
        "add_staples_to_list": _tool_add_staples_to_list,
        "list_staples": _tool_list_staples,
        "select_defaults": _tool_select_defaults,
        "submit_to_kroger": _tool_submit_to_kroger,
    }
    handler = dispatch.get(name)
    if handler is None:
        return _text_result(f"Unknown tool: {name}", is_error=True)
    return handler(user_id, arguments)


# ── Method dispatch ──────────────────────────────────────

def _handle_rpc(user_id: str, message: dict) -> dict | None:
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
            print(f"[mcp] Tool {tool_name} raised: {type(e).__name__}: {e}", flush=True)
            import traceback; traceback.print_exc()
            result = _text_result(f"Tool error: {type(e).__name__}: {e}", is_error=True)
        return _rpc_result(request_id, result)

    if is_notification:
        return None

    return _rpc_error(request_id, _METHOD_NOT_FOUND, f"Method not found: {method}")


# ── HTTP surface ────────────────────────────────────────

@router.post("/mcp")
async def mcp_post(request: Request):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
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
        return Response(status_code=202)
    return JSONResponse(response)


@router.get("/mcp")
async def mcp_get():
    return Response(status_code=405)


@router.delete("/mcp")
async def mcp_delete():
    return Response(status_code=405)
