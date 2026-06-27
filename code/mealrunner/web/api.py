"""JSON API endpoints for the React frontend."""

from __future__ import annotations

import logging
from typing import NamedTuple

from fastapi import APIRouter, Request, UploadFile, File
from fastapi.responses import JSONResponse
from sqlalchemy import text

logger = logging.getLogger(__name__)

from mealrunner.database import get_request_connection, get_connection, release_db_during_io

router = APIRouter(prefix="/api")


def _conn():
    conn = get_request_connection()
    if conn is not None:
        return conn
    return get_connection()


def _extras_dedup_key(name: str) -> str:
    """Normalization key for receipt_extra_items dedup.

    Stronger than LOWER(item_name) because receipts arrive from multiple
    parsers with different quirks: Kroger PDFs use unicode ligatures
    (Cauliﬂower, ﬁ), image OCR may strip the ® / ™ glyphs that PDF
    preserves, fancy quotes vs straight, slashes vs commas. All collapse
    to the same canonical form here so the same physical product on two
    receipts dedups instead of accumulating a sibling row per parser.
    """
    import re as _re
    import unicodedata
    s = unicodedata.normalize("NFKC", name or "").lower()
    return " ".join(_re.sub(r"[^a-z0-9]+", " ", s).split())


# ── Price logging ──────────────────────────────────────────


def _log_prices(conn, products: list[dict], location_id: str, source: str, user_id: str | None = None, fulfillment: str | None = None):
    """Log product prices to product_prices table for price tracking.

    fulfillment scopes the observation to 'curbside' or 'delivery' so the
    baseline median read can return mode-correct usual prices. Optional
    so legacy callers that don't have the mode handy still work.
    """
    for p in products:
        upc = p.get("upc", "")
        price = p.get("price")
        if not upc or price is None:
            continue
        try:
            conn.execute(
                text("""INSERT INTO product_prices (upc, location_id, store_chain, price, promo_price, in_stock, source, user_id, fulfillment)
                   VALUES (:upc, :loc, 'kroger', :price, :promo, :stock, :source, :uid, :ff)"""),
                {"upc": upc, "loc": location_id, "price": price,
                 "promo": p.get("promo_price"), "stock": p.get("in_stock"),
                 "source": source, "uid": user_id, "ff": fulfillment},
            )
        except Exception:
            pass
    try:
        conn.commit()
    except Exception:
        pass


# ── Per-user rate limiting (DB-backed, persists across deploys) ────────


def _check_throttle(user_id: str, endpoint: str, max_requests: int, window_seconds: int):
    """Return a 429 JSONResponse if the user exceeds the rate limit, else None.
    Uses DB-backed counters that persist across deploys."""
    from datetime import datetime, timezone, timedelta

    conn = _conn()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=window_seconds)

    row = conn.execute(
        text("SELECT id, count, window_start FROM rate_limits WHERE endpoint = :ep AND user_id = :uid"),
        {"ep": endpoint, "uid": user_id},
    ).fetchone()

    if row:
        # window_start is timestamptz post-session-53 migration — psycopg2
        # returns it as a tz-aware datetime. Before the migration it was a
        # TEXT ISO string. Handle both so old + new rows work.
        ws = row["window_start"]
        if isinstance(ws, str):
            try:
                ws = datetime.fromisoformat(ws.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                ws = cutoff  # treat unparseable as expired
        if ws is not None and ws.tzinfo is None:
            ws = ws.replace(tzinfo=timezone.utc)
        if ws is None or ws < cutoff:
            # Window expired — reset
            conn.execute(
                text("UPDATE rate_limits SET count = 1, window_start = :ws WHERE id = :id"),
                {"ws": now.isoformat(), "id": row["id"]},
            )
            conn.commit()
            return None
        if row["count"] >= max_requests:
            return JSONResponse(
                status_code=429,
                content={"ok": False, "error": "Too many requests, please try again later"},
            )
        conn.execute(
            text("UPDATE rate_limits SET count = count + 1 WHERE id = :id"),
            {"id": row["id"]},
        )
        conn.commit()
        return None

    # First request — insert
    conn.execute(
        text("INSERT INTO rate_limits (endpoint, user_id, count, window_start) VALUES (:ep, :uid, 1, :ws) ON CONFLICT DO NOTHING"),
        {"ep": endpoint, "uid": user_id, "ws": now.isoformat()},
    )
    conn.commit()
    return None


# ── Meals ────────────────────────────────────────────────


@router.get("/meals")
async def get_meals(request: Request):
    """Get rolling 7-day meals."""
    from mealrunner.planner import load_rolling_week

    user_id = request.state.user_id
    conn = _conn()
    mw = load_rolling_week(conn, user_id)
    return {
        "start_date": mw.start_date,
        "end_date": mw.end_date,
        "days": [
            {
                "date": d["date"],
                "day_short": d["day_short"],
                "meal": _meal_dict(d["meal"]) if d["meal"] else None,
            }
            for d in mw.all_days
        ],
    }


@router.get("/meals/past")
async def get_past_meals(request: Request):
    """Get the 7 days before today."""
    from datetime import date, timedelta
    from mealrunner.planner import load_meals

    user_id = request.state.user_id
    conn = _conn()
    today = date.today()
    end = today - timedelta(days=1)
    start = today - timedelta(days=7)
    meals = load_meals(conn, user_id, start.isoformat(), end.isoformat())
    meal_map = {m.slot_date: m for m in meals}

    days = []
    for i in range(7):
        d = start + timedelta(days=i)
        ds = d.isoformat()
        day_short = d.strftime("%a").upper()[:3]
        m = meal_map.get(ds)
        days.append({
            "date": ds,
            "day_short": day_short,
            "meal": _meal_dict(m) if m else None,
        })
    return {"days": days}


@router.get("/meals/{date}/sides")
async def get_sides(date: str, request: Request):
    """Return available side options for a date's meal."""
    from mealrunner.planner import load_meals, rolling_range

    user_id = request.state.user_id
    conn = _conn()
    meal_row = conn.execute(
        text("SELECT id FROM meals WHERE slot_date = :date AND user_id = :user_id"),
        {"date": date, "user_id": user_id},
    ).fetchone()
    # The side library (the user's side recipes) doesn't depend on a meal
    # existing on this date — the new-meal picker loads sides *before* the meal
    # is saved (the meal lands on Done). Only the "current" sides are
    # meal-specific, so guard just that lookup on meal_row.
    current_ids = []
    if meal_row:
        current_sides = conn.execute(
            text("SELECT side_recipe_id FROM meal_sides WHERE meal_id = :mid ORDER BY position"),
            {"mid": meal_row["id"]},
        ).fetchall()
        current_ids = [cs["side_recipe_id"] for cs in current_sides if cs["side_recipe_id"]]

    # Get user's side recipes
    side_recipes = conn.execute(
        text("SELECT id, name FROM recipes WHERE user_id = :uid AND recipe_type = 'side' ORDER BY name"),
        {"uid": user_id},
    ).fetchall()

    s, e = rolling_range()
    week_meals = load_meals(conn, user_id, s, e)
    used_side_names = set()
    for m in week_meals:
        if m.slot_date != date:
            for sd in m.sides:
                if sd.side_name:
                    used_side_names.add(sd.side_name)

    sides = []
    for sr in side_recipes:
        sides.append({
            "id": sr["id"],
            "name": sr["name"],
            "in_use": sr["name"] in used_side_names,
            "current": sr["id"] in current_ids,
        })
    return {"sides": sides, "current_ids": current_ids, "fixed": False, "max_sides": 3}


@router.post("/meals/{date}/set-side")
async def set_side(date: str, body: dict, request: Request):
    """Set sides for a date's meal. Accepts {sides: [{side_recipe_id, side_name}, ...]}."""
    from mealrunner.planner import save_meal, _row_to_meal, _resolve_side
    from mealrunner.models import MealSide

    user_id = request.state.user_id
    conn = _conn()
    row = conn.execute(
        text("SELECT * FROM meals WHERE slot_date = :date AND user_id = :user_id"),
        {"date": date, "user_id": user_id},
    ).fetchone()
    if not row:
        return await get_meals(request)

    meal = _row_to_meal(row)
    sides_data = body.get("sides", [])[:3]
    resolved = []
    for i, s in enumerate(sides_data):
        sid = s.get("side_recipe_id")
        sname = s.get("side_name", "")
        if not sid and sname:
            sid = _resolve_side(conn, user_id, sname)
        resolved.append(MealSide(id=None, side_recipe_id=sid, side_name=sname, position=i))
    meal.sides = resolved
    save_meal(conn, user_id, meal)
    return await get_meals(request)


@router.post("/meals/{date}/toggle-grocery")
async def toggle_grocery(date: str, request: Request):
    from mealrunner.planner import toggle_grocery as do_toggle

    user_id = request.state.user_id
    conn = _conn()
    do_toggle(conn, user_id, date)
    return await get_meals(request)


@router.post("/meals/{date}/notes")
async def update_meal_notes(date: str, body: dict, request: Request):
    user_id = request.state.user_id
    conn = _conn()
    notes = body.get("notes", "")
    conn.execute(
        text("UPDATE meals SET notes = :notes WHERE user_id = :uid AND slot_date = :date"),
        {"notes": notes, "uid": user_id, "date": date},
    )
    conn.commit()
    return await get_meals(request)


@router.post("/meals/{date}/set")
async def set_meal(date: str, body: dict, request: Request):
    from mealrunner.planner import set_meal as do_set
    from mealrunner.recipes import get_recipe

    user_id = request.state.user_id
    conn = _conn()
    if "recipe_id" not in body:
        return {"ok": False, "error": "recipe_id required"}
    recipe = get_recipe(conn, body["recipe_id"])
    if recipe:
        sides = body.get("sides")  # list of {side_recipe_id, side_name} or None
        do_set(conn, user_id, date, recipe.name, sides=sides)
    return await get_meals(request)


@router.get("/plan/optimize")
async def optimize_plan(request: Request):
    """Suggest 1-2 recipe swaps that reduce single-use ingredients and
    increase ingredient overlap across the rolling plan.

    v1 efficiency-only scoring (no diet, no parent-follow-up bulk-cooking
    pairs yet). Returns whole-meal swap proposals only — per-meal
    ingredient overrides are a v2 concern.
    """
    from mealrunner.planner import load_rolling_week, _get_recent_recipe_ids
    from mealrunner.recipes import get_recipe_ingredients, filter_recipes, get_recipe
    from collections import Counter
    from datetime import date as _date

    user_id = request.state.user_id
    conn = _conn()
    mw = load_rolling_week(conn, user_id)

    today_iso = _date.today().isoformat()
    planned = [m for m in mw.meals
               if m.recipe_id and m.slot_date >= today_iso]
    if len(planned) < 2:
        return {"suggestions": []}

    plan_ings: dict[str, set[int]] = {}
    plan_recipe_name: dict[str, str] = {}
    for m in planned:
        ings = get_recipe_ingredients(conn, m.recipe_id)
        plan_ings[m.slot_date] = {i.ingredient_id for i in ings}
        plan_recipe_name[m.slot_date] = m.recipe_name

    ing_count: Counter = Counter()
    for s in plan_ings.values():
        ing_count.update(s)

    used_ids = {m.recipe_id for m in planned}
    used_ids |= _get_recent_recipe_ids(conn, user_id, today_iso)
    candidates = filter_recipes(conn, exclude_ids=used_ids, user_id=user_id)

    cand_ings: dict[int, set[int]] = {}
    for c in candidates:
        cand_ings[c.id] = {
            i.ingredient_id for i in get_recipe_ingredients(conn, c.id)
        }

    scored: list[dict] = []
    for m in planned:
        orig_ings = plan_ings[m.slot_date]
        orig_single = {i for i in orig_ings if ing_count[i] == 1}
        other_ings: set[int] = set()
        for d, s in plan_ings.items():
            if d != m.slot_date:
                other_ings |= s

        for c in candidates:
            c_ings = cand_ings[c.id]
            if not c_ings:
                continue
            dropped_single = len(orig_single - c_ings)
            added_single = len(c_ings - other_ings - orig_ings)
            shared = len(c_ings & other_ings)
            similarity = (len(c_ings & orig_ings)
                          / max(len(c_ings | orig_ings), 1))
            score = dropped_single * 1.5 + shared * 1.0 - added_single * 0.8 + similarity * 0.5
            if score <= 0:
                continue
            scored.append({
                "slot_date": m.slot_date,
                "current_recipe_id": m.recipe_id,
                "current_recipe_name": m.recipe_name,
                "candidate_recipe_id": c.id,
                "candidate_recipe_name": c.name,
                "score": round(score, 2),
                "dropped_single_use": dropped_single,
                "newly_shared": shared,
                "added_single_use": added_single,
                "similarity": round(similarity, 2),
            })

    scored.sort(key=lambda s: -s["score"])

    seen_slots: set[str] = set()
    top: list[dict] = []
    for s in scored:
        if s["slot_date"] in seen_slots:
            continue
        seen_slots.add(s["slot_date"])
        bits = []
        if s["dropped_single_use"]:
            bits.append(f"drops {s['dropped_single_use']} single-use ingredient" + ("s" if s["dropped_single_use"] != 1 else ""))
        if s["newly_shared"]:
            bits.append(f"reuses {s['newly_shared']} ingredient" + ("s" if s["newly_shared"] != 1 else "") + " from other meals")
        if s["added_single_use"]:
            bits.append(f"adds {s['added_single_use']} new single-use")
        s["explanation"] = "; ".join(bits) or "improves overall ingredient overlap"
        top.append(s)
        if len(top) >= 2:
            break

    return {"suggestions": top}


@router.post("/meals/fresh-start")
async def fresh_start(request: Request):
    """Clear all meals in the rolling window. Grocery list updates on next view."""
    from mealrunner.planner import load_rolling_week

    user_id = request.state.user_id
    conn = _conn()
    mw = load_rolling_week(conn, user_id)

    # Delete all meals in the rolling window
    conn.execute(
        text("DELETE FROM meals WHERE slot_date >= :start AND slot_date <= :end AND user_id = :user_id"),
        {"start": mw.start_date, "end": mw.end_date, "user_id": user_id},
    )

    # Refresh grocery list — meal-derived items with no meals will be removed
    trip = _get_active_trip(conn, user_id)
    if trip:
        mw = load_rolling_week(conn, user_id)
        _refresh_trip_meal_items(conn, user_id, mw)

    conn.commit()
    return await get_meals(request)


@router.post("/meals/all-to-grocery")
async def all_to_grocery(request: Request):
    from mealrunner.planner import set_all_grocery

    user_id = request.state.user_id
    conn = _conn()
    from mealrunner.planner import load_rolling_week

    mw = load_rolling_week(conn, user_id)
    if mw.meals:
        set_all_grocery(conn, user_id, mw.start_date, mw.end_date, on=True)
    return await get_meals(request)


@router.delete("/meals/{date}")
async def remove_meal(date: str, request: Request):
    from mealrunner.planner import remove_meal as do_remove

    user_id = request.state.user_id
    conn = _conn()
    do_remove(conn, user_id, date)
    return await get_meals(request)


@router.post("/meals/{date}/set-freeform")
async def set_freeform(date: str, body: dict, request: Request):
    from mealrunner.planner import set_freeform_meal

    user_id = request.state.user_id
    conn = _conn()
    if not body.get("name"):
        return {"ok": False, "error": "name required"}
    set_freeform_meal(conn, user_id, date, body["name"])
    return await get_meals(request)


@router.post("/meals/swap-days")
async def swap_days(body: dict, request: Request):
    from mealrunner.planner import swap_dates

    user_id = request.state.user_id
    conn = _conn()
    if "date_a" not in body or "date_b" not in body:
        return {"ok": False, "error": "date_a and date_b required"}
    swap_dates(conn, user_id, body["date_a"], body["date_b"])
    return await get_meals(request)


@router.get("/meals/{date}/candidates")
async def get_candidates(date: str, request: Request):
    from mealrunner.planner import get_candidates as do_get
    from mealrunner.recipes import list_recipes

    user_id = request.state.user_id
    conn = _conn()
    candidates = do_get(conn, user_id, date)
    all_recipes = list_recipes(conn, user_id=user_id)
    return {
        "candidates": [_recipe_dict(r) for r in candidates],
        "all_recipes": [_recipe_dict(r) for r in all_recipes],
    }


@router.get("/meals/{date}/surprise")
async def surprise_meal(date: str, request: Request):
    """One smart meal suggestion (+ most-paired side) for the surprise-me banner.
    Query params: cuisine (italian/mexican/asian/american/quick), exclude
    (comma-separated recipe ids already shown this banner session)."""
    from mealrunner.planner import surprise_pick

    user_id = request.state.user_id
    conn = _conn()
    cuisine = request.query_params.get("cuisine") or None
    if cuisine == "all":
        cuisine = None
    exclude = request.query_params.get("exclude", "")
    exclude_ids = {int(x) for x in exclude.split(",") if x.strip().isdigit()}
    return surprise_pick(conn, user_id, date, cuisine=cuisine, exclude_ids=exclude_ids) or {"meal": None}


# ── Grocery (trip-based) ──────────────────────────────────


def _parse_ts(ts):
    """Coerce a value from a timestamptz column (or legacy string) into a tz-aware datetime."""
    from datetime import datetime, timezone
    if not ts:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t
    except (ValueError, TypeError, AttributeError):
        return None


def _regulars_prompt_state(conn, trip) -> str:
    """Return 'prompt', 'done', or 'hidden' for the regulars prompt.

    Shows the regulars prompt only when BOTH:
    - 3+ days since regulars were last offered (or never offered), AND
    - New meal-sourced items exist that were added after regulars_added_at
    """
    from datetime import datetime, timedelta, timezone

    try:
        uid = trip["user_id"]
        if not trip["regulars_added"]:
            # Never acted on — show prompt only if there are meal-sourced items
            meal_items = conn.execute(
                text("SELECT COUNT(*) as cnt FROM grocery_items WHERE user_id = :uid AND source = 'meal'"),
                {"uid": uid},
            ).fetchone()
            return "prompt" if meal_items["cnt"] > 0 else "hidden"

        ts_str = trip["regulars_added_at"] if "regulars_added_at" in trip.keys() else None
        if not ts_str:
            return "done"

        acted_at = _parse_ts(ts_str)
        if not acted_at:
            return "done"

        age = datetime.now(timezone.utc) - acted_at
        if age <= timedelta(days=3):
            return "done"

        # 3+ days old — check if new meal-sourced items were added since
        new_meal_items = conn.execute(
            text("SELECT COUNT(*) as cnt FROM grocery_items WHERE user_id = :uid AND source = 'meal' AND added_at > :since"),
            {"uid": uid, "since": ts_str},
        ).fetchone()
        if new_meal_items["cnt"] > 0:
            return "prompt"
        return "done"
    except (KeyError, Exception):
        return "hidden"
    except (KeyError, Exception):
        return "prompt"


def _get_active_trip(conn, user_id: str):
    """Return the user's grocery_state row, or None.

    Kept named `_get_active_trip` for callsite compatibility — in the
    perpetual-list model, every user has one grocery_state row that plays the
    role the old grocery_trips row used to play.
    """
    return conn.execute(
        text("SELECT * FROM grocery_state WHERE user_id = :user_id"),
        {"user_id": user_id},
    ).fetchone()


def _normalize_name(conn, raw_name: str) -> tuple[str, int | None]:
    """Normalize an item name to its canonical form. Returns (name, ingredient_id)."""
    from mealrunner.normalize import normalize_item_name
    return normalize_item_name(conn, raw_name)


def _infer_item_group(conn, name: str, user_id: str) -> str:
    """Resolve shopping group: user override > ingredient aisle > staples > keyword inference."""
    from mealrunner.staples import _infer_group

    # 1. User override (highest priority)
    row = conn.execute(
        text("SELECT shopping_group FROM user_item_groups WHERE LOWER(item_name) = LOWER(:name) AND user_id = :user_id"),
        {"name": name, "user_id": user_id},
    ).fetchone()
    if row:
        return row["shopping_group"]

    # 2. Ingredient table (normalize first to catch typos/variants)
    canonical, ing_id = _normalize_name(conn, name)
    if ing_id:
        row = conn.execute(
            text("SELECT aisle FROM ingredients WHERE id = :id"),
            {"id": ing_id},
        ).fetchone()
        if row and row["aisle"]:
            return row["aisle"]

    # 3. Staples
    row = conn.execute(
        text("SELECT shopping_group FROM staples WHERE LOWER(name) = LOWER(:name) AND user_id = :user_id"),
        {"name": name, "user_id": user_id},
    ).fetchone()
    if row and row["shopping_group"]:
        return row["shopping_group"]

    # 4. Keyword inference
    return _infer_group(name)


def _build_group_resolver(conn, user_id: str):
    """Build a fast group resolver by pre-loading all lookup tables.

    Returns a function: resolve(name) -> str that uses the same priority
    as _infer_item_group but without per-item DB queries.
    """
    from mealrunner.staples import _infer_group

    # Load all user overrides
    rows = conn.execute(
        text("SELECT LOWER(item_name) AS item_name, shopping_group FROM user_item_groups WHERE user_id = :user_id"),
        {"user_id": user_id},
    ).fetchall()
    overrides = {r["item_name"]: r["shopping_group"] for r in rows}

    # Load all ingredient aisles
    rows = conn.execute(text("SELECT LOWER(name) AS name, aisle FROM ingredients WHERE aisle != ''")).fetchall()
    aisles = {r["name"]: r["aisle"] for r in rows}

    # Load all staples groups
    rows = conn.execute(
        text("SELECT LOWER(name) AS name, shopping_group FROM staples WHERE user_id = :user_id AND shopping_group != ''"),
        {"user_id": user_id},
    ).fetchall()
    staple_groups = {r["name"]: r["shopping_group"] for r in rows}

    def resolve(name: str) -> str:
        nl = name.strip().lower()
        if nl in overrides:
            return overrides[nl]
        if nl in aisles:
            return aisles[nl]
        if nl in staple_groups:
            return staple_groups[nl]
        return _infer_group(nl)

    return resolve


def _build_trip_from_meals(conn, mw, user_id: str) -> None:
    """Populate grocery_items from current meal grocery build + saved extras."""
    from mealrunner.feedback import get_skips_for_meal, get_adds_for_meal
    from mealrunner.grocery import build_grocery_list, split_by_store

    grocery_meals = [m for m in mw.meals if m.on_grocery]
    resolve = _build_group_resolver(conn, user_id)
    inserted_names: set[str] = set()

    if grocery_meals:
        # Collect skip overrides for all meals on the plan
        skip_pairs: set[tuple[str, str]] = set()
        for meal in grocery_meals:
            for item_name in get_skips_for_meal(conn, user_id, meal.recipe_name):
                skip_pairs.add((item_name, meal.recipe_name))

        gl = build_grocery_list(conn, grocery_meals, mw.start_date, mw.end_date, user_id=user_id)
        by_store = split_by_store(gl)
        for items in by_store.values():
            for item in items:
                # Check if ALL meals for this item have a skip override
                item_meals = item.meals or []
                if item_meals and all(
                    (item.ingredient_name.lower(), m) in skip_pairs for m in item_meals
                ):
                    continue

                name_lower = item.ingredient_name.lower()
                if name_lower in inserted_names:
                    continue
                group = resolve(item.ingredient_name)
                for_meals = ",".join(item_meals) if item_meals else ""
                conn.execute(
                    text("""INSERT INTO grocery_items
                       (user_id, name, shopping_group, source, for_meals, meal_count)
                       VALUES (:user_id, :name, :group, 'meal', :for_meals, :meal_count)"""),
                    {"user_id": user_id, "name": name_lower,
                     "group": group, "for_meals": for_meals,
                     "meal_count": len(item_meals)},
                )
                inserted_names.add(name_lower)

        # Add auto-include overrides
        for meal in grocery_meals:
            for add in get_adds_for_meal(conn, user_id, meal.recipe_name):
                name = add["item_name"]
                if name in inserted_names:
                    continue
                group = _infer_item_group(conn, name, user_id)
                conn.execute(
                    text("""INSERT INTO grocery_items
                       (user_id, name, shopping_group, source, for_meals, meal_count)
                       VALUES (:user_id, :name, :group, 'meal', :for_meals, 1)"""),
                    {"user_id": user_id, "name": name, "group": group,
                     "for_meals": meal.recipe_name},
                )
                inserted_names.add(name)

    conn.commit()


def _ensure_active_trip(conn, mw, user_id: str):
    """Find or create the user's grocery_state row, refresh meal items, prune stale extras.

    In the perpetual-list model there is no real "trip" — every user has one
    grocery_state row that holds household-shared grocery state. This helper
    keeps the legacy name for callsite compatibility.
    """
    trip = _get_active_trip(conn, user_id)

    if trip is None:
        conn.execute(
            text("""INSERT INTO grocery_state (user_id) VALUES (:user_id)
               ON CONFLICT (user_id) DO NOTHING"""),
            {"user_id": user_id},
        )
        conn.commit()
        _build_trip_from_meals(conn, mw, user_id)
        trip = _get_active_trip(conn, user_id)
    else:
        # Refresh meal-sourced items (meals may have changed) but preserve extras and checked state
        _refresh_trip_meal_items(conn, user_id, mw)

    # Fix inconsistent state: items with submitted_at but ordered=0 are stuck
    # (can't be re-ordered, don't show as ordered). Clear stale submitted_at.
    # Exclude receipt-reconciled rows — those legitimately have ordered=0 with
    # submitted_at preserved from the original submission, and clearing it
    # makes them re-pickable by submit if select_product re-stamps ordered=1
    # via its LOWER(name) match, causing Kroger to receive the same UPC twice
    # and double the quantity.
    conn.execute(
        text("""UPDATE grocery_items SET submitted_at = NULL
           WHERE user_id = :user_id AND ordered = 0 AND submitted_at IS NOT NULL
             AND COALESCE(receipt_status, '') = ''"""),
        {"user_id": user_id},
    )

    # After 3 days an ordered+unreconciled row is either fulfilled (user
    # didn't upload the receipt) or cancelled. Meal rows soft-delete via
    # checked=1 so refresh's existing_map keeps the row and Branch 3
    # preserves state — without this, the missing row looks like a fresh
    # need and gets re-INSERTed on the next load (the "already-bought
    # meal ingredients reappeared on my list" bug). Old checked_at keeps
    # it out of the 24hr Recently-checked panel. New meal occurrences
    # still reset state via Branch 2; late receipts still reconcile
    # (receipt_status='' is matchable regardless of checked). Non-meal
    # rows hard-delete — refresh doesn't touch them, so no resurrection
    # risk. receipt_status='' on both paths protects matched/substituted/
    # not_fulfilled rows.
    conn.execute(
        text("""UPDATE grocery_items SET
                  checked = 1, checked_at = submitted_at,
                  ordered = 0, submitted_at = NULL,
                  selected_at = NULL, ordered_at = NULL,
                  product_upc = '', product_name = '',
                  product_brand = '', product_size = '',
                  product_price = NULL, product_image = '',
                  status = 'bought'
           WHERE user_id = :user_id
             AND source = 'meal'
             AND ordered = 1
             AND submitted_at IS NOT NULL
             AND submitted_at < NOW() - INTERVAL '3 days'
             AND COALESCE(receipt_status, '') = ''"""),
        {"user_id": user_id},
    )
    conn.execute(
        text("""DELETE FROM grocery_items
           WHERE user_id = :user_id
             AND source != 'meal'
             AND ordered = 1
             AND submitted_at IS NOT NULL
             AND submitted_at < NOW() - INTERVAL '3 days'
             AND COALESCE(receipt_status, '') = ''"""),
        {"user_id": user_id},
    )

    # Auto-settle bought rows older than 3 days. Bought is the transient
    # post-buy state; after the window passes the row is closed out
    # permanently and drops off both the matcher's candidate scope and the
    # receipt-page confirm/rate queue. Covers manual check-off, receipt
    # match, and the 3-day ordered timeout — all three land on 'bought'
    # with checked_at or submitted_at set.
    conn.execute(
        text("""UPDATE grocery_items SET status = 'settled', receipt_acknowledged = 1
           WHERE user_id = :user_id
             AND status = 'bought'
             AND COALESCE(checked_at, submitted_at)::timestamptz < NOW() - INTERVAL '3 days'"""),
        {"user_id": user_id},
    )

    # Auto-settle not_fulfilled rows the user never acted on. 3-day grace
    # period on the receipt page; after that, silently settle rather than
    # reappear on the active list. User preference (session 89): silent
    # settle is safer than silent reappearance — a reappeared row could
    # lead to double-buying, while a settled row that the user didn't
    # actually receive surfaces naturally next time they plan that meal.
    conn.execute(
        text("""UPDATE grocery_items SET status = 'settled', receipt_acknowledged = 1
           WHERE user_id = :user_id
             AND status = 'ordered'
             AND receipt_status = 'not_fulfilled'
             AND receipt_acknowledged = 0
             AND submitted_at IS NOT NULL
             AND submitted_at::timestamptz < NOW() - INTERVAL '3 days'"""),
        {"user_id": user_id},
    )

    # Prune checked/removed items older than 3 days.
    # Only prune non-meal items (extras, regulars). Meal-sourced items are
    # managed by _refresh_trip_meal_items which preserves checked state and
    # cleans up when meals leave the plan. Pruning meal items causes them to
    # be re-added as active on the next refresh.
    conn.execute(
        text("""DELETE FROM grocery_items WHERE user_id = :user_id
           AND source != 'meal'
           AND (checked = 1 OR have_it = 1 OR removed = 1)
           AND COALESCE(checked_at, have_it_at, removed_at)::timestamptz < NOW() - INTERVAL '3 days'"""),
        {"user_id": user_id},
    )
    conn.commit()

    return trip


class _EffectiveNeed(NamedTuple):
    """The subset of a fresh meal-need not already served by an external row
    (in-flight Kroger order, receipt-tagged row bound to an active meal).
    Per-meal-id resolution: 'frozen pizza' for meal A may be covered while
    meal B's frozen pizza is still uncovered."""
    meal_ids: set[int]
    for_meals: str
    meal_ids_str: str
    count: int


def _parse_meal_ids_csv(s: str | None) -> set[int]:
    return {int(x) for x in (s or "").split(",") if x.strip().isdigit()}


def _build_fresh_meal_items(
    conn, user_id: str, mw, resolve
) -> tuple[dict[str, dict], dict[int, str]]:
    """Compute the desired meal-source state from the active plan.

    Keyed on compare_key so plural/singular variants collapse — recipe says
    "apples", existing row says "apple", they're the same item. Sides in
    build_grocery_list are labeled by their parent meal's name, so the
    parent's id is what ends up tracked against side ingredients.

    Returns (fresh_by_key, meal_id_to_name).
    """
    from mealrunner.grocery import build_grocery_list, split_by_store
    from mealrunner.normalize import compare_key

    grocery_meals = [m for m in mw.meals if m.on_grocery]

    meal_ids_by_name: dict[str, list[int]] = {}
    for m in grocery_meals:
        if m.id is not None:
            meal_ids_by_name.setdefault(m.recipe_name, []).append(m.id)

    fresh: dict[str, dict] = {}
    if grocery_meals:
        gl = build_grocery_list(conn, grocery_meals, mw.start_date, mw.end_date, user_id=user_id)
        for items in split_by_store(gl).values():
            for item in items:
                name_lower = item.ingredient_name.lower()
                mids: set[int] = set()
                for mn in item.meals:
                    for mid in meal_ids_by_name.get(mn, []):
                        mids.add(mid)
                fresh[compare_key(name_lower)] = {
                    "name": name_lower,
                    "shopping_group": resolve(name_lower),
                    "for_meals": ",".join(item.meals) if item.meals else "",
                    "meal_ids": mids,
                    "meal_count": len(item.meals),
                }

    meal_id_to_name: dict[int, str] = {
        mid: name for name, ids in meal_ids_by_name.items() for mid in ids
    }
    return fresh, meal_id_to_name


def _load_meal_sync_existing(conn, user_id: str) -> dict[str, dict]:
    """Existing rows the meal sync is allowed to mutate.

    Includes have_it/checked/removed rows so a have-it'd "butter" stops the
    sync from inserting a fresh active "butter" every refresh — Branch 3
    in apply preserves the user's choice for meals already on the plan.
    Excludes ordered + receipt-tagged rows; those are owned by the order
    and receipt flows. ORDER BY (have_it + checked + removed) ASC means
    active rows win the per-key first-write when multiple rows share a
    canonical name.
    """
    from mealrunner.normalize import compare_key
    rows = conn.execute(
        text("""SELECT id, name, source, checked, checked_at, have_it, have_it_at,
                       removed, removed_at, for_meals, meal_ids, receipt_status
                FROM grocery_items
                WHERE user_id = :user_id
                  AND ordered = 0 AND submitted_at IS NULL
                  AND COALESCE(receipt_status, '') = ''
                ORDER BY (have_it + checked + removed) ASC, id DESC"""),
        {"user_id": user_id},
    ).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        key = compare_key(r["name"])
        if key not in out:
            out[key] = r
    return out


def _load_covered_meal_ids(
    conn, user_id: str, all_active_meal_ids: set[int]
) -> dict[str, set[int]]:
    """Per canonical key, the meal_ids already served by an in-flight order
    or a receipt-tagged row. Per-meal-id (not per-name): "frozen pizza"
    bought for meal A still leaves meal B uncovered.

    Active-plan intersect filter is critical — receipt-tagged rows from
    PRIOR meal occurrences (or pre-Phase-B legacy rows with meal_ids='')
    used to silently block a re-added meal name from populating its
    ingredients (feedback id=108, 2026-05-03). They contribute no coverage
    here.
    """
    from mealrunner.normalize import compare_key
    rows = conn.execute(
        text("""SELECT name, meal_ids FROM grocery_items
                WHERE user_id = :user_id
                  AND (
                    (ordered = 1 AND COALESCE(receipt_status, '') = '')
                    OR COALESCE(receipt_status, '') != ''
                  )"""),
        {"user_id": user_id},
    ).fetchall()
    covered: dict[str, set[int]] = {}
    for r in rows:
        relevant = _parse_meal_ids_csv(r["meal_ids"]) & all_active_meal_ids
        if relevant:
            covered.setdefault(compare_key(r["name"]), set()).update(relevant)
    return covered


def _effective_need_for(
    info: dict, covered_for_key: set[int], meal_id_to_name: dict[int, str]
) -> _EffectiveNeed:
    eff_ids = info["meal_ids"] - covered_for_key
    eff_names = sorted({
        meal_id_to_name[mid] for mid in eff_ids if mid in meal_id_to_name
    })
    return _EffectiveNeed(
        meal_ids=eff_ids,
        for_meals=",".join(eff_names),
        meal_ids_str=",".join(str(i) for i in sorted(eff_ids)),
        count=len(eff_names),
    )


def _delete_phantom_meal_rows(
    conn,
    existing_map: dict[str, dict],
    fresh_meal_items: dict[str, dict],
    covered_meal_ids_by_key: dict[str, set[int]],
    meal_id_to_name: dict[int, str],
) -> None:
    """Drop active meal-source rows whose fresh_need is FULLY covered by
    external rows — nothing left to serve. Mutates existing_map.

    Tightened (vs. earlier "any covering row deletes the active row"): the
    coarser check would delete a row still needed by an uncovered sibling
    meal — the "buy then re-add a meal that wants the same ingredient"
    bug class.
    """
    drop: list[str] = []
    for key, row in existing_map.items():
        if row["source"] != "meal" or key not in fresh_meal_items:
            continue
        eff = _effective_need_for(
            fresh_meal_items[key],
            covered_meal_ids_by_key.get(key, set()),
            meal_id_to_name,
        )
        if not eff.meal_ids:
            drop.append(key)
    for key in drop:
        conn.execute(
            text("DELETE FROM grocery_items WHERE id = :id"),
            {"id": existing_map[key]["id"]},
        )
        del existing_map[key]


def _drop_orphaned_meal_rows(
    conn, existing_map: dict[str, dict], fresh_meal_items: dict[str, dict]
) -> None:
    """Rows whose canonical name no longer appears in any fresh_need:
       - source='meal': delete (their meal has left the plan, including
         have_it/checked/removed rows whose state was about a gone meal).
       - source!='meal' with stale meal_ids: clear the meal fields, leave
         the row otherwise alone — the user added it themselves.
    """
    for key, row in existing_map.items():
        if key in fresh_meal_items:
            continue
        if row["source"] == "meal":
            conn.execute(
                text("DELETE FROM grocery_items WHERE id = :id"),
                {"id": row["id"]},
            )
        elif row["meal_ids"]:
            conn.execute(
                text("""UPDATE grocery_items SET
                       for_meals = '', meal_ids = '', meal_count = 0
                   WHERE id = :id"""),
                {"id": row["id"]},
            )


def _apply_meal_sync(
    conn,
    user_id: str,
    fresh_meal_items: dict[str, dict],
    existing_map: dict[str, dict],
    covered_meal_ids_by_key: dict[str, set[int]],
    meal_id_to_name: dict[int, str],
) -> None:
    """For each fresh_need, resolve against existing_map.

    Three branches when matching an existing meal-source row, by meal_ids
    history:
      1. Legacy (old empty): row pre-dates session-54 meal_ids tracking.
         Populate meal_ids; don't touch state.
      2. New occurrence (eff has an id not in old): a fresh meal instance
         is pulling this in. Reset per-buy state. Detection uses EFFECTIVE
         (uncovered) ids, not full fresh_need — otherwise newly-inserted
         rows would be immediately reset on the next sync because they
         were inserted with only the uncovered subset.
      3. Same occurrences as before: routine sync, preserve state.

    Non-meal source rows get meal context attached without state changes
    (use full fresh_need — the user added them and we don't want covered
    meals to silently drop from attribution). No-existing-row needs an
    INSERT, skipped when every meal_id is already covered.
    """
    for key, info in fresh_meal_items.items():
        eff = _effective_need_for(
            info, covered_meal_ids_by_key.get(key, set()), meal_id_to_name
        )
        row = existing_map.get(key)

        if row is None:
            if not eff.meal_ids:
                continue
            conn.execute(
                text("""INSERT INTO grocery_items
                   (user_id, name, shopping_group, source, for_meals, meal_ids, meal_count)
                   VALUES (:user_id, :name, :group, 'meal', :for_meals, :meal_ids, :meal_count)"""),
                {"user_id": user_id, "name": info["name"], "group": info["shopping_group"],
                 "for_meals": eff.for_meals, "meal_ids": eff.meal_ids_str,
                 "meal_count": eff.count},
            )
            continue

        if row["source"] != "meal":
            full_meal_ids_str = ",".join(str(i) for i in sorted(info["meal_ids"]))
            conn.execute(
                text("""UPDATE grocery_items SET
                       for_meals = :for_meals, meal_ids = :meal_ids,
                       meal_count = :meal_count
                   WHERE id = :id"""),
                {"for_meals": info["for_meals"], "meal_ids": full_meal_ids_str,
                 "meal_count": info["meal_count"], "id": row["id"]},
            )
            continue

        old_meal_ids = _parse_meal_ids_csv(row["meal_ids"])
        # Branch 2 (new uncovered occurrence) resets per-buy state. Branches
        # 1 (legacy: old empty) and 3 (same: eff ⊆ old) skip the reset.
        reset_sql = ""
        if old_meal_ids and eff.meal_ids - old_meal_ids:
            reset_sql = """checked = 0, checked_at = NULL,
                           have_it = 0, have_it_at = NULL,
                           removed = 0, removed_at = NULL,
                           receipt_status = '', receipt_item = '',
                           receipt_upc = '', receipt_price = NULL,
                           receipt_acknowledged = 0,
                           product_upc = '', product_name = '',
                           product_brand = '', product_size = '',
                           product_price = NULL, product_image = '',
                           selected_at = NULL, ordered_at = NULL,
                           status = 'active',"""
        conn.execute(
            text(f"""UPDATE grocery_items SET
                   {reset_sql}
                   for_meals = :for_meals, meal_ids = :meal_ids,
                   meal_count = :meal_count, shopping_group = :group
               WHERE id = :id"""),
            {"for_meals": eff.for_meals, "meal_ids": eff.meal_ids_str,
             "meal_count": eff.count, "group": info["shopping_group"], "id": row["id"]},
        )


def _refresh_trip_meal_items(conn, user_id: str, mw) -> None:
    """Re-derive meal-sourced grocery items while preserving extras and
    user-set state.

    Pipeline:
      1. Build the desired meal-source state from the current plan.
      2. Load existing rows the sync is allowed to mutate.
      3. Compute which (canonical_name, meal_id) pairs are already covered
         by an in-flight Kroger order or a receipt-tagged row bound to a
         meal still on the plan.
      4. Drop phantom meal-source rows whose effective need is fully covered.
      5. Drop or de-attribute rows whose meal has left the plan.
      6. INSERT/UPDATE per fresh_need against existing_map.

    Occurrence tracking via meal_ids: a fresh meal_id appearing for a
    canonical name means a brand-new meal occurrence — Branch 2 in step 6
    resets per-buy state. "Hot Dogs on 4/10" and "Hot Dogs on 4/26" share
    a name but get different meal_ids.
    """
    resolve = _build_group_resolver(conn, user_id)
    fresh_meal_items, meal_id_to_name = _build_fresh_meal_items(
        conn, user_id, mw, resolve
    )
    existing_map = _load_meal_sync_existing(conn, user_id)
    all_active_meal_ids = set(meal_id_to_name.keys())
    covered_meal_ids_by_key = _load_covered_meal_ids(
        conn, user_id, all_active_meal_ids
    )
    _delete_phantom_meal_rows(
        conn, existing_map, fresh_meal_items,
        covered_meal_ids_by_key, meal_id_to_name,
    )
    _drop_orphaned_meal_rows(conn, existing_map, fresh_meal_items)
    _apply_meal_sync(
        conn, user_id, fresh_meal_items, existing_map,
        covered_meal_ids_by_key, meal_id_to_name,
    )
    conn.commit()


@router.get("/grocery")
async def get_grocery(request: Request):
    """Get the grocery list from the active trip.

    Write endpoints that already ran `_ensure_active_trip` themselves can set
    `request.state._skip_ensure_active = True` before returning via this
    helper to avoid a second sync pass within the same request. The
    side-effect cleanups inside `_ensure_active_trip` (stale-order TTL,
    checked/removed prune) ran on the first pass and are idempotent — no
    correctness loss from skipping.
    """
    from mealrunner.planner import load_rolling_week

    user_id = request.state.user_id
    conn = _conn()
    mw = load_rolling_week(conn, user_id)
    if not getattr(request.state, "_skip_ensure_active", False):
        _ensure_active_trip(conn, mw, user_id)

    # Read all items from the trip
    rows = conn.execute(
        text("SELECT * FROM grocery_items WHERE user_id = :user_id ORDER BY shopping_group, name"),
        {"user_id": user_id},
    ).fetchall()

    from datetime import datetime, timedelta, timezone

    items_by_group: dict[str, list[dict]] = {}
    checked_names: list[str] = []
    ordered_names: list[str] = []
    have_it_names: list[str] = []
    removed_names: list[str] = []
    recently_checked: list[dict] = []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)

    for r in rows:
        group = r["shopping_group"] or "Other"
        for_meals_str = r["for_meals"]
        for_meals = [m for m in for_meals_str.split(",") if m] if for_meals_str else []
        try:
            added_at = r["added_at"]
        except (KeyError, Exception):
            added_at = None
        try:
            notes = r["notes"]
        except (KeyError, Exception):
            notes = ""

        # Single source of truth: the status column. See database.py for the
        # value set. All the old multi-column gates (checked + have_it +
        # removed + receipt_status combinations) collapsed into this one read.
        is_active = (r["status"] == "active")
        if is_active:
            items_by_group.setdefault(group, []).append({
                "id": r["id"],
                "name": r["name"],
                "for_meals": for_meals,
                "meal_count": r["meal_count"],
                "quantity": r["quantity"] if "quantity" in r.keys() else 1,
                "source": r["source"],
                "added_at": added_at,
                "notes": notes or "",
            })
        # Only flag CURRENTLY-active ordered rows. Historical rows with
        # ordered=1 (bought, matched on receipt, etc.) still carry the flag
        # forever; without the is_active gate they push their name into
        # ordered_names and the frontend's orderedSet then suppresses a
        # genuinely new active row of the same name from the grocery view.
        # Symptom: item appears on the Order page but not on Grocery.
        if r["ordered"] and is_active:
            ordered_names.append(r["name"].lower())
        if r["checked"]:
            checked_names.append(r["name"].lower())
            t = _parse_ts(r["checked_at"] if "checked_at" in r.keys() else None)
            if t and t > cutoff:
                recently_checked.append({"id": r["id"], "name": r["name"], "type": "bought"})
        elif r.get("removed"):
            removed_names.append(r["name"].lower())
            t = _parse_ts(r["removed_at"] if "removed_at" in r.keys() else None)
            if t and t > cutoff:
                recently_checked.append({"id": r["id"], "name": r["name"], "type": "removed"})
        elif r.get("have_it"):
            have_it_names.append(r["name"].lower())
            t = _parse_ts(r["have_it_at"] if "have_it_at" in r.keys() else None)
            if t and t > cutoff:
                recently_checked.append({"id": r["id"], "name": r["name"], "type": "have_it"})
        elif r.get("submitted_at"):
            t = _parse_ts(r["submitted_at"])
            if t and t > cutoff:
                recently_checked.append({"id": r["id"], "name": r["name"], "type": "ordered"})

    return {
        "start_date": mw.start_date,
        "end_date": mw.end_date,
        "items_by_group": items_by_group,
        "checked": checked_names,
        "ordered": ordered_names,
        "have_it": have_it_names,
        "removed": removed_names,
        "recently_checked": recently_checked,
    }


@router.post("/grocery/add")
async def add_grocery_item(body: dict, request: Request):
    """Add a free-form item to the user's grocery list.

    Partial-unique semantics on canonical name: if an active row whose
    compare_key matches already exists (any source — extra/regular/pantry/meal,
    not ordered/checked/have-it/removed), no-op. Compare via compare_key so
    "apple" doesn't add a second row when "apples" is already on the list.
    """
    user_id = request.state.user_id
    raw = body.get("name", "").strip()
    if not raw:
        return {"ok": False}

    conn = _conn()
    name, ingredient_id = _normalize_name(conn, raw)

    # If the seed didn't recognize it, fall back to whatever form the user
    # has used before for the same canonical name. So "mini cucumber" entered
    # today resolves to "mini cucumbers" if that's what the user typed last
    # week — even if the row from last week is checked off or in their
    # have-it'd history. Stops the row from flipping spelling between adds.
    from mealrunner.normalize import compare_key, resolve_user_canonical
    if ingredient_id is None:
        name = resolve_user_canonical(conn, user_id, name)
    key = compare_key(name)

    active_rows = conn.execute(
        text("""SELECT name FROM grocery_items
                WHERE user_id = :user_id AND status = 'active'"""),
        {"user_id": user_id},
    ).fetchall()
    on_list = {compare_key(r["name"]) for r in active_rows}

    if key not in on_list:
        group = _infer_item_group(conn, name, user_id)
        conn.execute(
            text("""INSERT INTO grocery_items
                  (user_id, name, shopping_group, source, for_meals, meal_count)
                  VALUES (:user_id, :name, :group, 'extra', '', 0)"""),
            {"user_id": user_id, "name": name, "group": group},
        )
        conn.commit()

    return await get_grocery(request)


@router.post("/grocery/note")
async def update_grocery_note(body: dict, request: Request):
    """Update the note on a grocery item. Lookup by row id."""
    user_id = request.state.user_id
    conn = _conn()
    item_id = body.get("id")
    notes = body.get("notes", "")
    if not item_id:
        return {"ok": False}
    conn.execute(
        text("""UPDATE grocery_items SET notes = :notes
           WHERE id = :id AND user_id = :uid"""),
        {"notes": notes, "id": item_id, "uid": user_id},
    )
    conn.commit()
    return await get_grocery(request)


@router.post("/grocery/quantity")
async def update_grocery_quantity(body: dict, request: Request):
    """Update the quantity on a grocery item. Clamped to [1, 99]."""
    user_id = request.state.user_id
    conn = _conn()
    item_id = body.get("id")
    qty = body.get("quantity")
    if not item_id or qty is None:
        return {"ok": False}
    try:
        qty = max(1, min(99, int(qty)))
    except (TypeError, ValueError):
        return {"ok": False}
    conn.execute(
        text("""UPDATE grocery_items SET quantity = :qty
           WHERE id = :id AND user_id = :uid"""),
        {"qty": qty, "id": item_id, "uid": user_id},
    )
    conn.commit()
    return await get_grocery(request)


@router.post("/grocery/recategorize")
async def recategorize_item(body: dict, request: Request):
    """Move an item to a different shopping group. Persists as a user override.

    Lookup target row by id; the cross-trip override in `user_item_groups` is
    keyed on (user_id, item_name) and stays name-based since it must apply to
    future trips with the same name.
    """
    user_id = request.state.user_id
    conn = _conn()
    item_id = body.get("id")
    group = body.get("shopping_group", "").strip()
    if not item_id or not group:
        return {"ok": False}

    # Fetch the target row to get its name (needed for the persistent override)
    row = conn.execute(
        text("SELECT name FROM grocery_items WHERE id = :id AND user_id = :uid"),
        {"id": item_id, "uid": user_id},
    ).fetchone()
    if not row:
        return {"ok": False}
    name_lower = row["name"].lower()

    # Save override for future trips (keyed on name)
    conn.execute(
        text("""INSERT INTO user_item_groups (user_id, item_name, shopping_group)
           VALUES (:user_id, :name, :group)
           ON CONFLICT (user_id, item_name) DO UPDATE SET shopping_group = :group, updated_at = CURRENT_TIMESTAMP"""),
        {"user_id": user_id, "name": name_lower, "group": group},
    )

    # Update the specific trip row by id
    conn.execute(
        text("UPDATE grocery_items SET shopping_group = :group WHERE id = :id AND user_id = :uid"),
        {"group": group, "id": item_id, "uid": user_id},
    )
    conn.commit()
    return await get_grocery(request)


@router.post("/grocery/toggle/{id:int}")
async def toggle_grocery_item(id: int, request: Request):
    """Mark a grocery row as checked. Lookup by row id.

    One-way: this endpoint marks-checked. Un-checking goes through /grocery/undo.
    """
    user_id = request.state.user_id
    real_uid = getattr(request.state, 'real_user_id', user_id)
    conn = _conn()

    row = conn.execute(
        text("""SELECT id, name, checked, ordered, source FROM grocery_items
                WHERE id = :id AND user_id = :user_id
                LIMIT 1"""),
        {"id": id, "user_id": user_id},
    ).fetchone()

    if row:
        item_name = row["name"]
        if real_uid != user_id:
            print(f"[grocery] toggle '{item_name}' by household member {real_uid} → owner {user_id}", flush=True)
        conn.execute(
            text("UPDATE grocery_items SET checked = 1, checked_at = CURRENT_TIMESTAMP, status = 'bought' WHERE id = :id"),
            {"id": row["id"]},
        )
        # If checking off an item not ordered via Kroger, it's in-store
        if not row["ordered"]:
            conn.execute(
                text("""UPDATE grocery_state SET order_source = CASE
                       WHEN order_source IN ('none', 'in_store') THEN 'in_store'
                       ELSE 'mixed'
                   END WHERE user_id = :user_id"""),
                {"user_id": user_id},
            )
        # Track last_bought_at on the unified staples table (any mode). Sources
        # 'regular' / 'pantry' both correspond to a staple row.
        if row["source"] in ("regular", "pantry"):
            conn.execute(
                text("""UPDATE staples SET last_bought_at = CURRENT_TIMESTAMP
                        WHERE user_id = :uid
                          AND (LOWER(name) = LOWER(:name)
                               OR ingredient_id IN (
                                   SELECT id FROM ingredients WHERE LOWER(name) = LOWER(:name)
                               ))"""),
                {"uid": user_id, "name": item_name},
            )
        conn.commit()

    return await get_grocery(request)


@router.delete("/grocery/item/{id:int}")
async def remove_grocery_item(id: int, request: Request):
    """Remove a grocery row by id. Meal-sourced rows set removed=1 (prevents
    re-add by refresh); extra/regular rows are deleted outright."""
    user_id = request.state.user_id
    real_uid = getattr(request.state, 'real_user_id', user_id)
    conn = _conn()

    row = conn.execute(
        text("""SELECT id, name, source FROM grocery_items
                WHERE id = :id AND user_id = :user_id
                LIMIT 1"""),
        {"id": id, "user_id": user_id},
    ).fetchone()

    if row:
        if real_uid != user_id:
            print(f"[grocery] remove '{row['name']}' by household member {real_uid} → owner {user_id}", flush=True)
        if row["source"] == "meal":
            conn.execute(
                text("UPDATE grocery_items SET removed = 1, removed_at = CURRENT_TIMESTAMP, status = 'removed' WHERE id = :id"),
                {"id": row["id"]},
            )
        else:
            conn.execute(
                text("DELETE FROM grocery_items WHERE id = :id"),
                {"id": row["id"]},
            )
    conn.commit()
    return {"ok": True}


@router.post("/grocery/undo/{item_id:int}")
async def undo_grocery_item(item_id: int, request: Request):
    """Reset a specific completed grocery row back to active state.

    Takes the row's `id` rather than its name because multiple completed rows
    can share a name (have-it'd, then bought, then have-it'd again etc.) and
    the user is undoing one specific action from the Recently Checked list.
    """
    user_id = request.state.user_id
    conn = _conn()
    conn.execute(
        text("""UPDATE grocery_items SET
               checked = 0, checked_at = NULL,
               have_it = 0, have_it_at = NULL,
               removed = 0, removed_at = NULL,
               ordered = 0, ordered_at = NULL, submitted_at = NULL,
               selected_at = NULL, product_upc = '', product_name = '',
               product_brand = '', product_size = '', product_price = NULL,
               product_image = '',
               receipt_status = '', receipt_acknowledged = 0,
               receipt_item = '', receipt_upc = '', receipt_price = NULL,
               status = 'active'
           WHERE id = :id AND user_id = :user_id"""),
        {"id": item_id, "user_id": user_id},
    )
    conn.commit()
    return await get_grocery(request)


@router.post("/grocery/buy-elsewhere/{id:int}")
async def buy_elsewhere_grocery_item(id: int, request: Request):
    """Toggle 'buying elsewhere' on a grocery row by id — removes from ordering
    flow but stays on grocery list."""
    user_id = request.state.user_id
    conn = _conn()

    row = conn.execute(
        text("""SELECT id, buy_elsewhere FROM grocery_items
                WHERE id = :id AND user_id = :user_id
                LIMIT 1"""),
        {"id": id, "user_id": user_id},
    ).fetchone()

    if row:
        if row["buy_elsewhere"]:
            # Undo: return to active ordering flow
            conn.execute(
                text("UPDATE grocery_items SET buy_elsewhere = 0, buy_elsewhere_at = NULL WHERE id = :id"),
                {"id": row["id"]},
            )
        else:
            conn.execute(
                text("UPDATE grocery_items SET buy_elsewhere = 1, buy_elsewhere_at = CURRENT_TIMESTAMP WHERE id = :id"),
                {"id": row["id"]},
            )
    conn.commit()
    return await get_order(request)


@router.post("/grocery/have-it/{id:int}")
async def have_it_grocery_item(id: int, request: Request):
    """Mark a grocery row as already on hand. Lookup by row id.

    Un-have-it is via /grocery/undo.
    """
    user_id = request.state.user_id
    real_uid = getattr(request.state, 'real_user_id', user_id)
    conn = _conn()

    row = conn.execute(
        text("""SELECT id, name FROM grocery_items
                WHERE id = :id AND user_id = :user_id
                LIMIT 1"""),
        {"id": id, "user_id": user_id},
    ).fetchone()

    suggest_staple = None
    if row:
        item_name = row["name"]
        if real_uid != user_id:
            print(f"[grocery] have-it '{item_name}' by household member {real_uid} → owner {user_id}", flush=True)
        conn.execute(
            text("UPDATE grocery_items SET have_it = 1, have_it_at = CURRENT_TIMESTAMP, status = 'have_it' WHERE id = :id"),
            {"id": row["id"]},
        )
        # Check if this item has been marked "have it" 3+ times — suggest as staple
        from mealrunner.staples import list_staples
        staple_names = {s.name.lower() for s in list_staples(conn, user_id)}
        name_lower = item_name.strip().lower()
        if name_lower not in staple_names:
            have_it_count = conn.execute(
                text("""SELECT COUNT(*) as cnt FROM grocery_items ti
                   WHERE ti.user_id = :uid AND LOWER(ti.name) = LOWER(:name) AND ti.have_it = 1"""),
                {"uid": user_id, "name": item_name},
            ).fetchone()
            if have_it_count and have_it_count["cnt"] >= 3:
                suggest_staple = item_name
    conn.commit()
    result = await get_grocery(request)
    if suggest_staple:
        result["suggest_staple"] = suggest_staple
    return result


@router.post("/grocery/add-staples")
async def add_staples_to_grocery(body: dict, request: Request):
    """Add selected staples to the active trip.

    Body: {"selected": [names], "mode": "every_trip" | "keep_on_hand"}.

    The mode determines which grocery_state flag gets advanced
    (regulars_added or pantry_checked) and which `source` value the
    grocery_items rows are tagged with — 'regular' for every_trip,
    'pantry' for keep_on_hand. The source distinction is preserved
    so existing meal-sync / TTL logic that branches on source keeps
    working without a data migration.
    """
    from mealrunner.planner import load_rolling_week
    from mealrunner.staples import EVERY_TRIP, KEEP_ON_HAND

    user_id = request.state.user_id
    selected = body.get("selected", [])
    mode = body.get("mode", EVERY_TRIP)
    if mode not in (EVERY_TRIP, KEEP_ON_HAND):
        return {"ok": False, "error": "invalid mode"}

    source = "regular" if mode == EVERY_TRIP else "pantry"

    conn = _conn()
    mw = load_rolling_week(conn, user_id)
    trip = _ensure_active_trip(conn, mw, user_id)

    # Dedup against the user's active rows on compare_key so "apple" doesn't
    # get added when "apples" is already on the list.
    from mealrunner.normalize import compare_key
    active_rows = conn.execute(
        text("""SELECT name FROM grocery_items
                WHERE user_id = :user_id AND status = 'active'"""),
        {"user_id": user_id},
    ).fetchall()
    on_list = {compare_key(r["name"]) for r in active_rows}

    for name in selected:
        name_lower = name.lower()
        key = compare_key(name_lower)
        if key in on_list:
            continue
        group = _infer_item_group(conn, name_lower, user_id)
        conn.execute(
            text("""INSERT INTO grocery_items
                 (user_id, name, shopping_group, source, for_meals, meal_count)
                 VALUES (:user_id, :name, :group, :source, '', 0)"""),
            {"user_id": user_id, "name": name_lower, "group": group, "source": source},
        )
        on_list.add(key)

    if mode == EVERY_TRIP:
        conn.execute(
            text("UPDATE grocery_state SET regulars_added = 1, regulars_added_at = CURRENT_TIMESTAMP WHERE user_id = :user_id"),
            {"user_id": user_id},
        )
    else:
        conn.execute(
            text("UPDATE grocery_state SET pantry_checked = 1, pantry_checked_at = CURRENT_TIMESTAMP WHERE user_id = :user_id"),
            {"user_id": user_id},
        )
    conn.commit()

    request.state._skip_ensure_active = True
    return await get_grocery(request)


@router.get("/bundles")
async def list_bundles(request: Request):
    """List the user's bundles with their items."""
    user_id = request.state.user_id
    conn = _conn()
    rows = conn.execute(
        text("SELECT id, name FROM bundles WHERE user_id = :uid ORDER BY name"),
        {"uid": user_id},
    ).fetchall()
    bundles_out = []
    for r in rows:
        items = conn.execute(
            text("SELECT id, name FROM bundle_items WHERE bundle_id = :bid ORDER BY position, id"),
            {"bid": r["id"]},
        ).fetchall()
        bundles_out.append({
            "id": r["id"],
            "name": r["name"],
            "items": [{"id": i["id"], "name": i["name"]} for i in items],
        })
    return {"bundles": bundles_out}


@router.post("/bundles")
async def create_bundle(body: dict, request: Request):
    """Create a new bundle. Body: {name}. Returns {id, name, items: []}."""
    user_id = request.state.user_id
    name = (body.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "name required"}
    conn = _conn()
    existing = conn.execute(
        text("SELECT id FROM bundles WHERE user_id = :uid AND LOWER(name) = LOWER(:name)"),
        {"uid": user_id, "name": name},
    ).fetchone()
    if existing:
        return {"ok": False, "error": "bundle with this name already exists"}
    row = conn.execute(
        text("INSERT INTO bundles (user_id, name) VALUES (:uid, :name) RETURNING id"),
        {"uid": user_id, "name": name},
    ).fetchone()
    conn.commit()
    return {"ok": True, "id": row["id"], "name": name, "items": []}


@router.delete("/bundles/{bundle_id}")
async def delete_bundle(bundle_id: int, request: Request):
    user_id = request.state.user_id
    conn = _conn()
    conn.execute(
        text("DELETE FROM bundles WHERE id = :bid AND user_id = :uid"),
        {"bid": bundle_id, "uid": user_id},
    )
    conn.commit()
    return {"ok": True}


@router.post("/bundles/{bundle_id}/items")
async def add_bundle_item(bundle_id: int, body: dict, request: Request):
    """Add an item to a bundle. Body: {name}."""
    user_id = request.state.user_id
    name = (body.get("name") or "").strip().lower()
    if not name:
        return {"ok": False, "error": "name required"}
    conn = _conn()
    owned = conn.execute(
        text("SELECT id FROM bundles WHERE id = :bid AND user_id = :uid"),
        {"bid": bundle_id, "uid": user_id},
    ).fetchone()
    if not owned:
        return {"ok": False, "error": "not found"}
    pos_row = conn.execute(
        text("SELECT COALESCE(MAX(position), -1) + 1 AS next_pos FROM bundle_items WHERE bundle_id = :bid"),
        {"bid": bundle_id},
    ).fetchone()
    next_pos = pos_row["next_pos"]
    row = conn.execute(
        text("INSERT INTO bundle_items (bundle_id, name, position) VALUES (:bid, :name, :pos) RETURNING id"),
        {"bid": bundle_id, "name": name, "pos": next_pos},
    ).fetchone()
    conn.commit()
    return {"ok": True, "id": row["id"], "name": name}


@router.delete("/bundles/{bundle_id}/items/{item_id}")
async def delete_bundle_item(bundle_id: int, item_id: int, request: Request):
    user_id = request.state.user_id
    conn = _conn()
    conn.execute(
        text("""DELETE FROM bundle_items WHERE id = :iid AND bundle_id IN (
                SELECT id FROM bundles WHERE id = :bid AND user_id = :uid)"""),
        {"iid": item_id, "bid": bundle_id, "uid": user_id},
    )
    conn.commit()
    return {"ok": True}


@router.post("/grocery/add-bundle")
async def add_bundle_to_grocery(body: dict, request: Request):
    """Add a bundle's items to the active trip.

    Body: {bundle_id}. Each item is deduped against existing active rows
    by compare_key — if "rice" is already on the list (regardless of source),
    we don't add a second "rice" row. Items are tagged source='bundle'.
    """
    from mealrunner.planner import load_rolling_week
    from mealrunner.normalize import compare_key

    user_id = request.state.user_id
    bundle_id = body.get("bundle_id")
    if not bundle_id:
        return {"ok": False, "error": "bundle_id required"}

    conn = _conn()
    bundle = conn.execute(
        text("SELECT id FROM bundles WHERE id = :bid AND user_id = :uid"),
        {"bid": bundle_id, "uid": user_id},
    ).fetchone()
    if not bundle:
        return {"ok": False, "error": "not found"}

    items = conn.execute(
        text("SELECT name FROM bundle_items WHERE bundle_id = :bid ORDER BY position, id"),
        {"bid": bundle_id},
    ).fetchall()

    mw = load_rolling_week(conn, user_id)
    _ensure_active_trip(conn, mw, user_id)

    active_rows = conn.execute(
        text("""SELECT name FROM grocery_items
                WHERE user_id = :user_id AND status = 'active'"""),
        {"user_id": user_id},
    ).fetchall()
    on_list = {compare_key(r["name"]) for r in active_rows}

    added = 0
    for it in items:
        name = it["name"].lower().strip()
        if not name:
            continue
        key = compare_key(name)
        if key in on_list:
            continue
        group = _infer_item_group(conn, name, user_id)
        conn.execute(
            text("""INSERT INTO grocery_items
                 (user_id, name, shopping_group, source, for_meals, meal_count)
                 VALUES (:user_id, :name, :group, 'bundle', '', 0)"""),
            {"user_id": user_id, "name": name, "group": group},
        )
        on_list.add(key)
        added += 1
    conn.commit()

    request.state._skip_ensure_active = True
    result = await get_grocery(request)
    if isinstance(result, dict):
        result["bundle_added"] = added
    return result


@router.post("/grocery/build")
async def build_my_list(request: Request, body: dict = None):
    """Refresh grocery list from current meals."""
    from mealrunner.planner import load_rolling_week

    user_id = request.state.user_id
    conn = _conn()
    mw = load_rolling_week(conn, user_id)
    trip = _ensure_active_trip(conn, mw, user_id)
    _refresh_trip_meal_items(conn, user_id, mw)
    conn.commit()

    request.state._skip_ensure_active = True
    return await get_grocery(request)


# ── Order ────────────────────────────────────────────────


@router.get("/order")
async def get_order(request: Request):
    """Get order state: pending items, selected items, and summary.

    Write endpoints (e.g. /order/select, /order/deselect) that already ran
    `_ensure_active_trip` themselves can set `request.state._skip_ensure_active
    = True` before returning via this helper. Avoids the second sync pass
    that previously caused phantom-row inserts on the trailing call. See
    `get_grocery` for the same pattern.
    """
    from mealrunner.planner import load_rolling_week

    user_id = request.state.user_id
    conn = _conn()
    mw = load_rolling_week(conn, user_id)
    if not getattr(request.state, "_skip_ensure_active", False):
        _ensure_active_trip(conn, mw, user_id)

    rows = conn.execute(
        text("""SELECT * FROM grocery_items WHERE user_id = :user_id
           AND status = 'active'
           ORDER BY shopping_group, name"""),
        {"user_id": user_id},
    ).fetchall()

    pending = []
    selected = []
    buy_elsewhere = []
    for r in rows:
        try:
            notes = r["notes"] or ""
        except (KeyError, Exception):
            notes = ""
        try:
            row_qty = r["quantity"]
        except (KeyError, Exception):
            row_qty = 1
        item = {
            "id": r["id"],
            "name": r["name"],
            "shopping_group": r["shopping_group"],
            "source": r["source"],
            "for_meals": [m for m in r["for_meals"].split(",") if m] if r["for_meals"] else [],
            "notes": notes,
            "quantity": row_qty,
        }
        if r["buy_elsewhere"]:
            buy_elsewhere.append(item)
            continue
        if r["product_upc"]:
            try:
                qty = r["quantity"]
            except (KeyError, Exception):
                qty = 1
            item["product"] = {
                "upc": r["product_upc"],
                "name": r["product_name"],
                "brand": r["product_brand"],
                "size": r["product_size"],
                "price": r["product_price"],
                "image": r["product_image"],
                "quantity": qty,
            }
            selected.append(item)
        else:
            pending.append(item)

    total_price = sum(
        r["product_price"] * (r["quantity"] if "quantity" in r.keys() else 1)
        for r in rows
        if r["product_upc"] and r["product_price"] and not r["buy_elsewhere"]
    )

    # Background prewarm disabled: _build_search_response does `conn = _conn()`
    # after its release_db_during_io block, which relies on the request
    # contextvar. In a daemon thread there's no contextvar, so _conn() checks
    # out a fresh pool connection that's never returned. ~30 pending items =
    # pool exhausted = every subsequent request waits 30s and times out.
    # First-search latency is the trade-off until this can be reworked.

    return {
        "pending": pending,
        "selected": selected,
        "buy_elsewhere": buy_elsewhere,
        "total_items": len(selected),
        "total_price": round(total_price, 2),
    }


def _bg_prewarm_order(user_id: str, item_names: list[str]):
    """Background thread: pre-warm _search_cache for pending order items.

    Calls the same helper the user-facing endpoint uses and stashes the full
    response in _search_cache under the same key. When the user actually
    types a search a moment later, it's an in-memory hit and skips the
    Kroger / OFF / DB enrichment pipeline entirely.
    """
    from mealrunner.database import get_connection
    from mealrunner.stores import get_kroger_location_id
    import time as _time

    try:
        with get_connection() as bg_conn:
            location_id = get_kroger_location_id(bg_conn, user_id)
            if not location_id:
                return

            warmed = 0
            ff = "curbside"  # frontend defaults to curbside; delivery prewarms on first miss
            start = 1

            for name in item_names:
                try:
                    cache_key = f"{name.lower().strip()}:{ff}:{start}"
                    now = _time.time()
                    if cache_key in _search_cache:
                        ts, _ = _search_cache[cache_key]
                        if now - ts < _SEARCH_CACHE_TTL:
                            continue  # already fresh, skip

                    response = _build_search_response(bg_conn, user_id, name, ff, start, location_id)
                    _cache_search_response(cache_key, response)
                    warmed += 1
                    _time.sleep(0.3)
                except Exception as e:
                    print(f"[prewarm] Error for '{name}': {e}", flush=True)

            print(f"[prewarm] Warmed {warmed}/{len(item_names)} items for user {user_id[:8]}...", flush=True)
    except Exception as e:
        print(f"[prewarm] Background error: {e}", flush=True)


_search_cache: dict[str, tuple[float, dict]] = {}  # {term: (timestamp, response)}
_SEARCH_CACHE_TTL = 1200  # 20 minutes — matches a typical shopping session
_SEARCH_CACHE_MAX = 200
_SEARCH_PAGE_SIZE = 20  # bumped from 12: gives the proximity filter + sort UI a bigger candidate pool


def _proximity_filter(search_term: str, products: list, max_gap: int = 2) -> list:
    """Drop products that don't have the search-term tokens appearing as a
    near-contiguous subsequence in the product name. Catches things like
    "Mr. Peanut's stick butter" leaking into a "peanut butter" search.

    Returns a list of (kroger_position, product) tuples so the frontend
    can use Kroger's original ranking as a tiebreaker in MR Rank.
    """
    import re

    def _toks(s: str) -> list[str]:
        return [w for w in re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split() if w]

    s_tokens = _toks(search_term)
    if not s_tokens:
        return [(i, p) for i, p in enumerate(products)]

    kept: list[tuple[int, object]] = []
    for idx, p in enumerate(products):
        haystack = _toks(f"{p.brand or ''} {p.description or ''}")
        if not haystack:
            continue
        # Locate each search token in haystack and check the spread
        positions = []
        ok = True
        for st in s_tokens:
            found = -1
            for i, w in enumerate(haystack):
                if w == st or w.startswith(st) or st.startswith(w):
                    found = i
                    break
            if found < 0:
                ok = False
                break
            positions.append(found)
        if not ok:
            continue
        # Tokens must appear in order with a small max gap between adjacent
        if any(positions[i + 1] - positions[i] < 1 or positions[i + 1] - positions[i] > max_gap + 1
               for i in range(len(positions) - 1)):
            continue
        kept.append((idx, p))
    return kept


def _baseline_prices_for_upcs(conn, upcs: list[str], location_id: str, fulfillment: str | None = None, days: int = 90) -> dict[str, float]:
    """Median observed price per UPC at this location over the last N days,
    scoped to a fulfillment mode (curbside vs delivery) when given.

    Used as the "your usual" reference on each product row so the user can
    see whether today's price is below or above their typical experience.

    Mode filter: rows tagged with the same fulfillment OR rows tagged NULL
    (historical / receipt-source data that pre-dates the fulfillment column).
    Better to mix in untagged-but-recent than to return nothing.

    Returns {upc: median_price} for UPCs that have at least one observation.
    """
    if not upcs:
        return {}
    ph = ", ".join(f":u{i}" for i in range(len(upcs)))
    params = {f"u{i}": u for i, u in enumerate(upcs)}
    params["loc"] = location_id
    params["interval"] = f"{days} days"
    ff_clause = ""
    if fulfillment:
        params["ff"] = fulfillment
        ff_clause = "AND (fulfillment = :ff OR fulfillment IS NULL)"
    rows = conn.execute(
        text(f"""SELECT upc,
                        percentile_cont(0.5) WITHIN GROUP (ORDER BY price) AS median_price
                 FROM product_prices
                 WHERE upc IN ({ph})
                   AND location_id = :loc
                   AND price IS NOT NULL AND price > 0
                   AND fetched_at >= NOW() - (:interval)::interval
                   {ff_clause}
                 GROUP BY upc"""),
        params,
    ).fetchall()
    return {r["upc"]: float(r["median_price"]) for r in rows if r["median_price"] is not None}


def _parse_unit_price(size_str: str, price: float | None) -> tuple[float | None, str | None]:
    """Parse Kroger's size string and return (unit_price, unit_label) or
    (None, None) when the format isn't one we handle. Frontend renders "—"
    for the None case so the row doesn't break on unparseable inputs.

    Supported patterns (Kroger's most common ~90%):
      - "16 oz", "32 fl oz", "1 lb", "16 ct" — simple weight/volume/count
      - "16 ct / 22.5 oz" — multipack, treats the second number as the total
      - "8 pk / 12 fl oz" — pack of N at Y each, total = N*Y
      - "1/2 gal" — fractional volume
    """
    if not size_str or price is None or price <= 0:
        return None, None
    import re

    s = size_str.lower().strip()
    s = re.sub(r"\([^)]*\)", "", s).strip()  # strip "(approx.)" etc

    def _label(unit: str) -> str:
        u = unit.replace(".", "")
        if u in ("fl oz", "floz"):
            return "/fl oz"
        if u in ("lbs", "lb"):
            return "/lb"
        return f"/{u}"

    # Multipack: "X pk / Y <unit>" → total = X * Y
    m = re.match(r"(\d+(?:\.\d+)?)\s*pk\s*/\s*(\d+(?:\.\d+)?)\s*(fl\.?\s*oz|oz|ml|l|lb|lbs)\b", s)
    if m:
        x, y, unit = float(m.group(1)), float(m.group(2)), m.group(3)
        total = x * y
        if total > 0:
            return round(price / total, 3), _label(unit)

    # Multipack: "X ct / Y <unit>" → total = Y (Y is reported as the aggregate)
    m = re.match(r"(\d+(?:\.\d+)?)\s*ct\s*/\s*(\d+(?:\.\d+)?)\s*(fl\.?\s*oz|oz|ml|l|lb|lbs)\b", s)
    if m:
        y, unit = float(m.group(2)), m.group(3)
        if y > 0:
            return round(price / y, 3), _label(unit)

    # Fractional volume: "1/2 gal", "1/4 gal"
    m = re.match(r"(\d+)\s*/\s*(\d+)\s*(gal|gallon)", s)
    if m:
        num, denom = float(m.group(1)), float(m.group(2))
        if denom > 0:
            gallons = num / denom
            fl_oz = gallons * 128
            if fl_oz > 0:
                return round(price / fl_oz, 3), "/fl oz"

    # Simple: "X <unit>" — weight, volume, count
    m = re.match(r"(\d+(?:\.\d+)?)\s*(fl\.?\s*oz|oz|lb|lbs|g|kg|ml|l|ct|each|gal)\b", s)
    if m:
        x, unit = float(m.group(1)), m.group(2)
        if x > 0:
            return round(price / x, 3), _label(unit) if unit not in ("each",) else "/each"

    return None, None


def _build_search_response(conn, user_id: str, item_name: str, ff: str, start: int, user_location_id: str) -> dict:
    """Build the full /order/search response for a single item.

    Shared by the user-facing endpoint and the background prewarm thread so
    both populate `_search_cache` with identical shapes. Caller owns cache
    read/write and rate limiting; this function does the work.
    """
    from concurrent.futures import ThreadPoolExecutor
    from mealrunner.kroger import (
        search_products_fast, fill_prices, _lookup_food_score,
        get_preferred_products,
    )

    # Use the item name as-is for the Kroger search. The ingredient 'root' field
    # is for dedup (e.g., "apple juice" and "orange juice" → "juice"), not for search.
    search_term = item_name.strip().lower()

    # Get preferences first (enrichment happens after search updates product_scores)
    prefs = get_preferred_products(conn, user_id, item_name, limit=3)

    # Search Kroger
    try:
        products = search_products_fast(search_term, limit=_SEARCH_PAGE_SIZE, start=start, fulfillment=ff, location_id=user_location_id)
        # Drop products whose name doesn't contain the search-term tokens as
        # a near-contiguous subsequence ("Mr. Peanut's stick butter" leaks
        # into a "peanut butter" search via Kroger's loose match — kill it
        # before we enrich/score, so we don't waste API calls on noise).
        kept = _proximity_filter(search_term, products, max_gap=2)
        products = [p for _, p in kept]
    except Exception as e:
        import traceback
        traceback.print_exc()
        products = []

    # Check cache for prices (today) and scores (90 days)
    import datetime as _dt
    _SCORE_TTL_DAYS = 90
    _today = _dt.date.today()
    _score_cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=_SCORE_TTL_DAYS)

    cached = {}
    if products:
        upcs = [p.upc for p in products]
        placeholders = ", ".join(f":p{i}" for i in range(len(upcs)))
        params = {f"p{i}": upc for i, upc in enumerate(upcs)}
        rows = conn.execute(
            text(f"SELECT upc, nova_group, nutriscore, price, promo_price, "
                 f"in_stock, curbside, score_fetched_at, price_fetched_at "
                 f"FROM product_scores "
                 f"WHERE upc IN ({placeholders})"),
            params,
        ).fetchall()
        cached = {r["upc"]: dict(r) for r in rows}

    # --- Prices: use today's cache or fill from Kroger ---
    # Only price/promo are cached-overwriteable; in_stock/curbside/delivery
    # come from the live search response (line above) and are mode-correct
    # for the current request. Reading them from the cache would (a) clobber
    # the freshest signal we have, and (b) carry the mode from whenever the
    # cache was last written (curbside-hardcoded prewarm thread, or a prior
    # search at a different mode). Result was items showing in_stock=true in
    # delivery mode when only curbside was actually fulfillable, and stale
    # inventory bleeding across same-day searches.
    need_price = []
    for p in products:
        c = cached.get(p.upc)
        if c and c["price_fetched_at"] and c["price_fetched_at"].date() == _today:
            p.price = c["price"] if c["price"] is not None else p.price
            p.promo_price = c["promo_price"]
        else:
            need_price.append(p)

    if need_price:
        try:
            fill_prices(need_price, location_id=user_location_id)
        except Exception as e:
            print(f"[search] fill_prices failed: {e}")

    # --- Scores: use cached or fetch from Open Food Facts ---
    need_scores = []
    for p in products:
        c = cached.get(p.upc)
        if c and c["nova_group"] is not None and c["score_fetched_at"] > _score_cutoff:
            p.nova_group = c["nova_group"]
            p.nutriscore = c["nutriscore"] or ""
        else:
            need_scores.append(p)

    def _fetch_score(p):
        nova, nutri = _lookup_food_score(p.description, p.brand)
        p.nova_group = nova
        p.nutriscore = nutri or ""

    if need_scores:
        # Release the DB connection while we wait on Open Food Facts so
        # the pool slot is freed for other concurrent requests. Lookup is
        # parallel (max 6 workers) but each call is 200-1000ms; total
        # block can run >1s on a cache-cold search.
        with release_db_during_io():
            with ThreadPoolExecutor(max_workers=6) as pool:
                pool.map(_fetch_score, need_scores)
        conn = _conn()

    # --- Save everything to cache ---
    # Sort by upc so concurrent writers (this loop + the 6h polling thread +
    # another tab's search) all acquire row locks in the same order. Prevents
    # the cross-process deadlock that 503'd searches in the prod log.
    for p in sorted(products, key=lambda x: x.upc):
        conn.execute(
            text("""INSERT INTO product_scores
               (upc, nova_group, nutriscore, score_fetched_at, price, promo_price, in_stock, curbside, delivery, price_fetched_at)
               VALUES (:upc, :nova_group, :nutriscore, CURRENT_TIMESTAMP, :price, :promo_price, :in_stock, :curbside, :delivery, CURRENT_TIMESTAMP)
               ON CONFLICT(upc) DO UPDATE SET
               nova_group=COALESCE(excluded.nova_group, product_scores.nova_group),
               nutriscore=CASE WHEN excluded.nova_group IS NOT NULL THEN excluded.nutriscore ELSE product_scores.nutriscore END,
               score_fetched_at=CASE WHEN excluded.nova_group IS NOT NULL THEN excluded.score_fetched_at ELSE product_scores.score_fetched_at END,
               price=excluded.price, promo_price=excluded.promo_price,
               in_stock=excluded.in_stock, curbside=excluded.curbside, delivery=excluded.delivery,
               price_fetched_at=excluded.price_fetched_at"""),
            {"upc": p.upc, "nova_group": p.nova_group, "nutriscore": p.nutriscore or "",
             "price": p.price, "promo_price": p.promo_price,
             "in_stock": int(p.in_stock), "curbside": int(p.curbside), "delivery": int(p.delivery)},
        )
    conn.commit()

    # Log prices for tracking
    _log_prices(conn, [{"upc": p.upc, "price": p.price, "promo_price": p.promo_price, "in_stock": int(p.in_stock)} for p in products if p.price], user_location_id, "search", user_id, fulfillment=ff)

    from mealrunner.brands import get_parent_company
    from mealrunner.violations import get_company_violations

    # Enrich preferences with freshly-updated product_scores
    pref_upcs = [p.upc for p in prefs]
    pref_scores = {}
    if pref_upcs:
        ph = ", ".join(f":pu{i}" for i in range(len(pref_upcs)))
        ps = {f"pu{i}": u for i, u in enumerate(pref_upcs)}
        pref_score_rows = conn.execute(
            text(f"SELECT upc, nova_group, nutriscore, price, promo_price, in_stock, curbside, delivery FROM product_scores WHERE upc IN ({ph})"),
            ps,
        ).fetchall()
        pref_scores = {r["upc"]: dict(r) for r in pref_score_rows}

    # Use search results to get fresh brand/category for preferences
    search_products_by_upc = {p.upc: p for p in products if p.upc}

    # Build pref_list. Wrapped in a top-level try/except so any failure in
    # the authoritative-availability enrichment (Kroger UPC lookup,
    # fill_prices, product_scores upsert) doesn't take down the whole
    # search endpoint — prior selections just come back empty for that
    # request, which degrades gracefully instead of crashing ordering.
    pref_list: list[dict] = []
    try:
        # For any pref UPC not in today's search results, look it up directly
        # at the user's store. The "Prior selections" row promises one-click
        # re-orders — showing an item we can't confirm Kroger carries right
        # now (foreign-chain UPCs like Publix receipts, or Kroger SKUs that
        # have been discontinued) leads the user to pick something that
        # fails later in their Kroger cart. A per-UPC catalog lookup is
        # authoritative.
        unknown_pref_upcs = [
            p.upc for p in prefs
            if p.upc and p.upc not in search_products_by_upc
        ]
        pref_direct_results: dict[str, object] = {}
        if unknown_pref_upcs:
            from concurrent.futures import ThreadPoolExecutor as _TPE

            def _lookup_upc(upc: str):
                try:
                    matches = search_products_fast(
                        upc, limit=1, fulfillment=ff, location_id=user_location_id,
                    )
                    for m in matches:
                        if m.upc == upc:
                            return upc, m
                except Exception as e:
                    print(f"[order/search] pref lookup failed for {upc}: {e}")
                return upc, None

            # Release the DB connection while doing the per-UPC Kroger
            # lookups + fill_prices — this is a parallel block (max 3
            # workers) but each call is 300-800ms; total can run >1s.
            with release_db_during_io():
                with _TPE(max_workers=3) as _pool:
                    for _upc, _match in _pool.map(_lookup_upc, unknown_pref_upcs):
                        if _match is not None:
                            pref_direct_results[_upc] = _match

                # Kroger's catalog search often omits price; backfill from the
                # per-product endpoint so product_scores gets real numbers.
                _direct_matches = list(pref_direct_results.values())
                if _direct_matches:
                    try:
                        fill_prices(_direct_matches, location_id=user_location_id)
                    except Exception as e:
                        print(f"[order/search] fill_prices for pref lookups failed: {e}")
            conn = _conn()

            # Refresh product_scores for confirmed-available prefs so next time
            # they come back through the normal cache path without a lookup.
            # Sorted by upc to match the lock-acquire order of every other
            # product_scores writer — prevents cross-process deadlocks.
            for _upc, m in sorted(pref_direct_results.items()):
                try:
                    conn.execute(
                        text("""INSERT INTO product_scores
                                (upc, price, promo_price, in_stock, curbside, delivery, price_fetched_at)
                                VALUES (:upc, :price, :promo_price, :in_stock, :curbside, :delivery, CURRENT_TIMESTAMP)
                                ON CONFLICT(upc) DO UPDATE SET
                                  price=excluded.price,
                                  promo_price=excluded.promo_price,
                                  in_stock=excluded.in_stock,
                                  curbside=excluded.curbside,
                                  delivery=excluded.delivery,
                                  price_fetched_at=excluded.price_fetched_at"""),
                        {"upc": _upc, "price": m.price, "promo_price": m.promo_price,
                         "in_stock": int(m.in_stock) if m.in_stock is not None else None,
                         "curbside": int(m.curbside) if m.curbside is not None else None,
                         "delivery": int(m.delivery) if m.delivery is not None else None},
                    )
                except Exception as e:
                    print(f"[order/search] product_scores upsert failed for {_upc}: {e}")
            conn.commit()
            # Re-read pref_scores so the loop below sees the fresh rows.
            # Skip empty-string UPCs — they'd match only the synthetic '' upc
            # row (which won't exist) and add noise.
            _non_empty = [u for u in pref_upcs if u]
            if _non_empty:
                ph = ", ".join(f":pu{i}" for i in range(len(_non_empty)))
                ps = {f"pu{i}": u for i, u in enumerate(_non_empty)}
                pref_score_rows = conn.execute(
                    text(f"SELECT upc, nova_group, nutriscore, price, promo_price, "
                         f"in_stock, curbside, delivery "
                         f"FROM product_scores WHERE upc IN ({ph})"),
                    ps,
                ).fetchall()
                pref_scores = {r["upc"]: dict(r) for r in pref_score_rows}

        # UPCs Kroger has confirmed carrying right now, either from the current
        # "black beans"-style search or the targeted UPC lookup above.
        confirmed_upcs = set(search_products_by_upc.keys()) | set(pref_direct_results.keys())

        for p in prefs:
            # Drop preferences Kroger didn't acknowledge. Covers both
            # non-Kroger receipt UPCs (Publix etc.) and UPCs that have been
            # silently discontinued since the user last picked them.
            if not p.upc or p.upc not in confirmed_upcs:
                continue

            search_p = search_products_by_upc.get(p.upc) or pref_direct_results.get(p.upc)
            sc = pref_scores.get(p.upc, {})

            brand = search_p.brand if search_p and search_p.brand else p.brand
            cat = search_p.categories[0] if search_p and search_p.categories else None
            available = bool(search_p.in_stock)
            has_curbside = bool(search_p.curbside)
            has_delivery = bool(search_p.delivery)

            # Drop items that aren't orderable in the user's current mode.
            # Prior selections is a "pick and order now" row — stale picks
            # just cause frustration when the Kroger cart rejects them.
            if ff == "curbside" and not has_curbside and has_delivery:
                continue
            if ff == "delivery" and not has_delivery and has_curbside:
                continue
            if not available:
                continue

            parent = get_parent_company(brand, conn, category=cat) if brand else "We're not sure"
            violations = get_company_violations(conn, parent) if parent not in ("We're not sure",) else None
            pref_item = {
                "upc": p.upc,
                "name": p.description,
                "brand": brand,
                "size": p.size,
                "rating": p.rating,
                "image": f"https://www.kroger.com/product/images/medium/front/{p.upc}",
                "price": sc.get("price"),
                "promo_price": sc.get("promo_price"),
                "nova": sc.get("nova_group"),
                "nutriscore": sc.get("nutriscore", ""),
                "parent_company": parent,
                "in_stock": True,
                "unavailable_reason": None,
            }
            if violations:
                pref_item["violations"] = violations
            pref_list.append(pref_item)
    except Exception as e:
        import traceback
        print(f"[order/search] prior-selections enrichment failed for "
              f"'{item_name}': {type(e).__name__}: {e}")
        traceback.print_exc()
        pref_list = []

    # Look up user ratings for search result products in one batched query.
    # Endpoint only uses your_rating, so skip the up/down counts.
    product_ratings = {}
    _rating_upcs = [p.upc for p in products if p.upc]
    if _rating_upcs:
        _rph = ", ".join(f":pk{i}" for i in range(len(_rating_upcs)))
        _rparams = {f"pk{i}": u for i, u in enumerate(_rating_upcs)}
        _rparams["uid"] = user_id
        _rrows = conn.execute(
            text(f"SELECT product_key, rating FROM product_ratings WHERE user_id = :uid AND product_key IN ({_rph})"),
            _rparams,
        ).fetchall()
        product_ratings = {r["product_key"]: r["rating"] for r in _rrows}

    # Resolve parent companies first, then batch-load violations
    product_parents = {}
    unknown_brands_batch = set()
    for p in products:
        cat = p.categories[0] if p.categories else None
        parent = get_parent_company(p.brand, conn, category=cat)
        product_parents[p.upc or p.product_id] = parent
        if parent == "We're not sure" and p.brand:
            unknown_brands_batch.add(p.brand.strip())

    # Cache violation lookups by parent company
    violation_cache = {}
    for p in products:
        parent = product_parents[p.upc or p.product_id]
        if parent == "We're not sure":
            continue
        if parent and parent not in violation_cache:
            violation_cache[parent] = get_company_violations(conn, parent)

    # Batch the "your usual" baseline lookup for every product UPC at this
    # store, scoped to the current fulfillment mode, last 90 days. Pickup
    # and delivery prices for the same UPC routinely differ, so we keep
    # them separate when surfacing the user's "usual."
    baseline_by_upc = _baseline_prices_for_upcs(
        conn, [p.upc for p in products if p.upc], user_location_id, fulfillment=ff, days=90,
    )

    result = []
    for p in products:
        rating = product_ratings.get(p.upc, 0)
        parent = product_parents[p.upc or p.product_id]
        violations = violation_cache.get(parent) if parent not in ("We're not sure",) else None
        # Use promo when available for unit-price math — that's the price
        # the user actually pays. Falls back to regular price.
        effective_price = p.promo_price if p.promo_price else p.price
        unit_price, unit_label = _parse_unit_price(p.size, effective_price)
        item = {
            "upc": p.upc,
            "product_id": p.product_id,
            "name": p.description,
            "brand": p.brand,
            "size": p.size,
            "price": p.price,
            "promo_price": p.promo_price,
            "baseline_price": baseline_by_upc.get(p.upc),
            "unit_price": unit_price,
            "unit_label": unit_label,
            "in_stock": p.in_stock,
            "curbside": p.curbside,
            "nova": p.nova_group,
            "nutriscore": p.nutriscore,
            "image": p.image_url,
            "rating": rating,
            "parent_company": parent,
            "categories": p.categories or [],
        }
        if violations:
            item["violations"] = violations
        result.append(item)

    # Log unknown brands for later research
    for brand in unknown_brands_batch:
        try:
            conn.execute(text(
                """INSERT INTO unknown_brands (brand) VALUES (:b)
                   ON CONFLICT (brand) DO UPDATE SET times_seen = unknown_brands.times_seen + 1, last_seen = CURRENT_TIMESTAMP"""
            ), {"b": brand})
        except Exception:
            pass
    if unknown_brands_batch:
        conn.commit()

    # Remove thumbs-down products, sort thumbs-up first
    result = [r for r in result if r["rating"] >= 0]
    result.sort(key=lambda r: -r["rating"])

    return {
        "item_name": item_name,
        "search_term": search_term,
        "preferences": pref_list if start == 1 else [],  # only show prefs on first page
        "products": result,
        "start": start,
        "has_more": len(products) >= _SEARCH_PAGE_SIZE - 4,  # filter may drop a few; if we got most of a page, more likely exists
    }


def _cache_search_response(cache_key: str, response: dict) -> None:
    """Stash a search response in the in-memory cache with LRU eviction.

    Empty results are not cached: a transient Kroger failure inside
    _build_search_response is caught silently as products=[], and the
    20-min cache would otherwise lock the user into "no products found"
    on a perfectly valid search term. Re-querying is cheap; let it.
    """
    if not response.get("products"):
        return
    import time as _time
    now = _time.time()
    expired = [k for k, (ts, _) in _search_cache.items() if now - ts >= _SEARCH_CACHE_TTL]
    for k in expired:
        del _search_cache[k]
    if len(_search_cache) >= _SEARCH_CACHE_MAX:
        oldest = min(_search_cache, key=lambda k: _search_cache[k][0])
        del _search_cache[oldest]
    _search_cache[cache_key] = (now, response)


@router.get("/order/search/{item_name:path}")
async def search_order_products(item_name: str, request: Request, fulfillment: str = "curbside", start: int = 1):
    """Search Kroger products for a grocery item. Returns products + preferences.
    fulfillment: 'curbside' (pickup) or 'delivery'. start: pagination offset (1-based)."""
    import time as _time
    from mealrunner.stores import get_kroger_location_id

    user_id = request.state.user_id

    # Rate limit: max 20 searches per user per minute
    throttled = _check_throttle(user_id, "order_search", 20, 60)
    if throttled:
        return throttled

    conn = _conn()

    user_location_id = get_kroger_location_id(conn, user_id)
    if not user_location_id:
        return {"error": "no_store", "message": "Set your Kroger store in Preferences", "prior_selections": [], "products": []}

    ff = fulfillment if fulfillment in ("curbside", "delivery") else "curbside"
    cache_key = f"{item_name.lower().strip()}:{ff}:{start}"
    now = _time.time()
    if cache_key in _search_cache:
        ts, resp = _search_cache[cache_key]
        if now - ts < _SEARCH_CACHE_TTL:
            return resp
        else:
            del _search_cache[cache_key]

    # _build_search_response is sync and makes 3 categories of blocking
    # external HTTP calls (Kroger product search, Kroger price backfill,
    # Open Food Facts NOVA/Nutri-Score lookups). Without the thread offload
    # this whole pipeline (300ms-1.5s per call) sits on the single uvicorn
    # event loop and freezes every other in-flight request — same shape as
    # the session-83 /order/submit fix.
    import anyio
    response = await anyio.to_thread.run_sync(
        lambda: _build_search_response(conn, user_id, item_name, ff, start, user_location_id)
    )
    _cache_search_response(cache_key, response)
    return response


@router.post("/order/select")
async def select_product(body: dict, request: Request):
    """Select a Kroger product for a grocery item."""
    from mealrunner.kroger import save_preference, KrogerProduct
    from mealrunner.planner import load_rolling_week

    user_id = request.state.user_id
    item_name = body.get("item_name")
    product = body.get("product")
    try:
        quantity = max(1, min(24, int(body.get("quantity", 1))))
    except (TypeError, ValueError):
        quantity = 1
    if not item_name or not product:
        return {"ok": False, "error": "item_name and product required"}

    conn = _conn()
    mw = load_rolling_week(conn, user_id)
    trip = _ensure_active_trip(conn, mw, user_id)

    # Check if this item already has a different product selected
    existing = conn.execute(
        text("""SELECT id, name, product_upc FROM grocery_items
           WHERE user_id = :user_id AND LOWER(name) = :item_name AND product_upc != '' AND product_upc != :upc
           AND ordered = 1 AND submitted_at IS NULL AND removed = 0
           LIMIT 1"""),
        {"user_id": user_id, "item_name": item_name.lower(), "upc": product["upc"]},
    ).fetchone()

    if existing:
        # Different product for same line — insert a sibling line "{base} (N)".
        # We suffix to keep them distinct on grocery/order/receipt
        # views. If the existing row's name already ends in "(N)", strip it
        # so we don't end up with "Greek Yogurt (2) (2)". Base off the stored
        # name so casing stays consistent across siblings.
        import re as _re
        existing_name = existing["name"].strip()
        sm = _re.match(r"^(.*?)\s*\((\d+)\)$", existing_name)
        base = sm.group(1) if sm else existing_name
        n = 2
        while True:
            candidate = f"{base} ({n})"
            taken = conn.execute(
                text("SELECT 1 FROM grocery_items WHERE user_id = :uid AND LOWER(name) = :name"),
                {"uid": user_id, "name": candidate.lower()},
            ).fetchone()
            if not taken:
                break
            n += 1
        new_name = candidate
        conn.execute(
            text("""INSERT INTO grocery_items
                   (user_id, name, source, shopping_group, for_meals, meal_count,
                    product_upc, product_name, product_brand, product_size, product_price, product_image,
                    quantity, ordered, ordered_at, selected_at)
               SELECT :user_id, :new_name, 'extra', shopping_group, for_meals, 0,
                    :upc, :pname, :brand, :size, :price, :image,
                    :quantity, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
               FROM grocery_items WHERE id = :existing_id"""),
            {"user_id": user_id, "existing_id": existing["id"], "new_name": new_name,
             "upc": product["upc"], "pname": product["name"], "brand": product.get("brand", ""),
             "size": product.get("size", ""), "price": product.get("price"),
             "image": product.get("image", ""),
             "quantity": quantity},
        )
    else:
        # First product or same product re-selected — update in place.
        # Clear any stale receipt_status from a prior trip's reconciliation
        # so this row passes the /order/submit chokepoint (which excludes
        # rows with a non-empty receipt_status). Otherwise a re-selected
        # not_fulfilled row stamps submitted_at but never reaches Kroger.
        conn.execute(
            text("""UPDATE grocery_items SET
                   product_upc = :upc, product_name = :name, product_brand = :brand,
                   product_size = :size, product_price = :price, product_image = :image,
                   quantity = :quantity,
                   ordered = 1, ordered_at = CURRENT_TIMESTAMP, selected_at = CURRENT_TIMESTAMP,
                   receipt_status = '', receipt_acknowledged = 0,
                   receipt_item = '', receipt_upc = '', receipt_price = NULL,
                   status = 'active'
               WHERE user_id = :user_id AND LOWER(name) = :item_name
                 AND status = 'active'"""),
            {"upc": product["upc"], "name": product["name"], "brand": product.get("brand", ""),
             "size": product.get("size", ""), "price": product.get("price"),
             "image": product.get("image", ""),
             "quantity": quantity,
             "user_id": user_id, "item_name": item_name.lower()},
        )
    # Update trip source based on what's happening
    conn.execute(
        text("""UPDATE grocery_state SET order_source = CASE
               WHEN order_source IN ('none', 'kroger') THEN 'kroger'
               ELSE 'mixed'
           END WHERE user_id = :user_id"""),
        {"user_id": user_id},
    )
    conn.commit()

    # Log price for tracking
    from mealrunner.stores import get_kroger_location_id
    sel_location = get_kroger_location_id(conn, user_id) or ""
    _log_prices(conn, [{"upc": product["upc"], "price": product.get("price"), "promo_price": None}], sel_location, "select", user_id)

    # Save preference for future searches
    kp = KrogerProduct(
        product_id="", upc=product["upc"],
        description=product["name"], brand=product.get("brand", ""),
        size=product.get("size", ""),
    )
    save_preference(conn, user_id, item_name, kp, source="picked")

    # Background: look up this UPC at nearby stores for price comparison
    upc = product.get("upc", "")
    if upc and sel_location:
        import threading

        def _bg_nearby_prices(bg_upc, bg_user_id):
            from mealrunner.database import get_connection
            from mealrunner.stores import get_nearby_stores
            from mealrunner.pricing import _poll_single_product
            import time as _time
            try:
                with get_connection() as bg_conn:
                    nearby = get_nearby_stores(bg_conn, bg_user_id)
                    for store in nearby:
                        try:
                            price_data = _poll_single_product(bg_upc, store["location_id"])
                            if price_data:
                                bg_conn.execute(
                                    text("""INSERT INTO product_prices
                                        (upc, location_id, store_chain, price, promo_price, in_stock, source, user_id, fulfillment)
                                        VALUES (:upc, :loc, 'kroger', :price, :promo, :stock, 'nearby', :uid, 'curbside')"""),
                                    {"upc": bg_upc, "loc": store["location_id"],
                                     "price": price_data["price"],
                                     "promo": price_data.get("promo_price"),
                                     "stock": price_data.get("in_stock"),
                                     "uid": bg_user_id},
                                )
                            _time.sleep(0.5)
                        except Exception:
                            pass
                    bg_conn.commit()
            except Exception:
                pass

        threading.Thread(target=_bg_nearby_prices, args=(upc, user_id), daemon=True).start()

        # Also backfill missing prices for other selected items at home store
        def _bg_backfill_prices(bg_user_id, bg_location):
            from mealrunner.database import get_connection
            from mealrunner.kroger import BASE_URL, _headers
            import requests as _requests
            import time as _time
            try:
                with get_connection() as bg_conn:
                    missing = bg_conn.execute(
                        text("""SELECT id, product_upc FROM grocery_items
                            WHERE user_id = :uid AND product_upc != '' AND product_price IS NULL
                            AND submitted_at IS NULL AND removed = 0"""),
                        {"uid": bg_user_id},
                    ).fetchall()
                    if not missing:
                        return
                    headers = _headers()
                    for row in missing:
                        for attempt in range(3):
                            try:
                                resp = _requests.get(
                                    f"{BASE_URL}/products",
                                    params={"filter.term": row["product_upc"],
                                            "filter.locationId": bg_location, "filter.limit": 1},
                                    headers=headers, timeout=10,
                                )
                                if resp.status_code == 429:
                                    _time.sleep(1.0 * (attempt + 1))
                                    continue
                                if resp.status_code == 200:
                                    items = resp.json().get("data", [])
                                    if items:
                                        sub = items[0].get("items", [{}])[0] if items[0].get("items") else {}
                                        price = sub.get("price", {}).get("regular")
                                        if price is not None:
                                            bg_conn.execute(
                                                text("UPDATE grocery_items SET product_price = :price WHERE id = :id"),
                                                {"price": price, "id": row["id"]},
                                            )
                                    break
                            except Exception:
                                pass
                            _time.sleep(0.5 * (attempt + 1))
                    bg_conn.commit()
            except Exception:
                pass

        threading.Thread(target=_bg_backfill_prices, args=(user_id, sel_location), daemon=True).start()

    request.state._skip_ensure_active = True
    return await get_order(request)


@router.post("/order/deselect/{item_name:path}")
async def deselect_product(item_name: str, request: Request):
    """Remove product selection for a grocery item."""
    from mealrunner.planner import load_rolling_week

    user_id = request.state.user_id
    conn = _conn()
    mw = load_rolling_week(conn, user_id)
    trip = _ensure_active_trip(conn, mw, user_id)

    conn.execute(
        text("""UPDATE grocery_items SET
               product_upc = '', product_name = '', product_brand = '',
               product_size = '', product_price = NULL, product_image = '',
               ordered = 0, ordered_at = NULL, selected_at = NULL,
               status = 'active'
           WHERE user_id = :user_id AND LOWER(name) = :name
             AND status = 'active'"""),
        {"user_id": user_id, "name": item_name.lower()},
    )
    conn.commit()

    request.state._skip_ensure_active = True
    return await get_order(request)


@router.delete("/order/preference/{upc}")
async def delete_preference(upc: str, request: Request):
    """Remove a product preference (prior selection) by UPC."""
    user_id = request.state.user_id
    conn = _conn()
    conn.execute(
        text("DELETE FROM product_preferences WHERE user_id = :uid AND upc = :upc"),
        {"uid": user_id, "upc": upc},
    )
    conn.commit()
    return {"ok": True}


@router.get("/order/price-comparison")
async def price_comparison(request: Request):
    """Compare current order prices across nearby Kroger stores."""
    from mealrunner.stores import get_kroger_location_id, get_nearby_stores
    from mealrunner.planner import load_rolling_week

    user_id = request.state.user_id
    conn = _conn()

    home_loc = get_kroger_location_id(conn, user_id)
    if not home_loc:
        return {"comparisons": []}

    nearby = get_nearby_stores(conn, user_id)
    if not nearby:
        # Auto-populate: prefer user's home zip, fall back to store's zip from Kroger API
        zip_code = None
        home_zip_row = conn.execute(
            text("SELECT value FROM settings WHERE user_id = :uid AND key = 'home_zip'"),
            {"uid": user_id},
        ).fetchone()
        if home_zip_row:
            zip_code = home_zip_row["value"]
        if not zip_code:
            # Offload the Kroger /locations call so a slow Kroger response
            # doesn't freeze the event loop for every other in-flight request.
            from mealrunner.kroger import _headers, BASE_URL
            import requests as _requests
            import anyio

            def _fetch_home_location():
                try:
                    return _requests.get(f"{BASE_URL}/locations/{home_loc}", headers=_headers(), timeout=(3, 7))
                except Exception:
                    return None

            resp = await anyio.to_thread.run_sync(_fetch_home_location)
            if resp is not None and resp.ok:
                try:
                    zip_code = resp.json().get("data", {}).get("address", {}).get("zipCode", "")
                except Exception:
                    pass
        if zip_code:
            from mealrunner.stores import refresh_nearby_stores
            import anyio
            try:
                # refresh_nearby_stores does its own Kroger /locations search
                # + DB writes. Offload the whole thing so it doesn't block.
                await anyio.to_thread.run_sync(refresh_nearby_stores, conn, user_id, home_loc, zip_code)
                nearby = get_nearby_stores(conn, user_id)
            except Exception:
                pass
        if not nearby:
            return {"comparisons": []}

    mw = load_rolling_week(conn, user_id)
    trip = _ensure_active_trip(conn, mw, user_id)

    # Get selected order items with prices. Match the same active-set filters
    # as /order so we don't count stale picks (have-it, checked off, or otherwise
    # no longer in the user's "to be ordered" list — the perpetual-trip model
    # keeps historical rows around with product_upc still populated).
    rows = conn.execute(text("""
        SELECT product_upc, product_price, quantity FROM grocery_items
        WHERE user_id = :uid AND product_upc != '' AND product_price IS NOT NULL
        AND submitted_at IS NULL AND removed = 0 AND buy_elsewhere = 0
        AND checked = 0 AND have_it = 0 AND skipped = 0
    """), {"uid": user_id}).fetchall()

    if not rows:
        return {"comparisons": []}

    upcs = [r["product_upc"] for r in rows]
    items_total = len(upcs)

    # Build home price map: upc -> total cost (price * qty)
    home_prices = {}
    qty_map = {}
    for r in rows:
        home_prices[r["product_upc"]] = r["product_price"] * (r["quantity"] or 1)
        qty_map[r["product_upc"]] = r["quantity"] or 1

    comparisons = []
    for store in nearby:
        # Get latest price per UPC at this store (within 7 days)
        placeholders = ", ".join(f":u{i}" for i in range(len(upcs)))
        params = {f"u{i}": u for i, u in enumerate(upcs)}
        params["loc"] = store["location_id"]

        price_rows = conn.execute(text(f"""
            SELECT DISTINCT ON (upc) upc, price, promo_price
            FROM product_prices
            WHERE location_id = :loc AND upc IN ({placeholders})
            AND fetched_at::timestamptz > NOW() - INTERVAL '7 days'
            ORDER BY upc, fetched_at DESC
        """), params).fetchall()

        if not price_rows:
            continue

        nearby_total = 0.0
        home_total = 0.0
        matched = 0
        for pr in price_rows:
            upc = pr["upc"]
            if upc not in home_prices:
                continue
            nearby_price = pr["promo_price"] if pr["promo_price"] else pr["price"]
            if nearby_price is None:
                continue
            qty = qty_map.get(upc, 1)
            nearby_total += nearby_price * qty
            home_total += home_prices[upc]
            matched += 1

        if matched == 0:
            continue

        comparisons.append({
            "location_id": store["location_id"],
            "name": store["name"],
            "address": store["address"],
            "savings": round(home_total - nearby_total, 2),
            "items_compared": matched,
            "items_total": items_total,
        })

    comparisons.sort(key=lambda c: -c["savings"])

    # Background: fetch missing nearby prices so next request is more complete
    # Find UPCs that had no price at ANY nearby store
    all_matched_upcs = set()
    for store in nearby:
        placeholders = ", ".join(f":u{i}" for i in range(len(upcs)))
        params = {f"u{i}": u for i, u in enumerate(upcs)}
        params["loc"] = store["location_id"]
        found = conn.execute(text(f"""
            SELECT DISTINCT upc FROM product_prices
            WHERE location_id = :loc AND upc IN ({placeholders})
            AND fetched_at::timestamptz > NOW() - INTERVAL '7 days'
        """), params).fetchall()
        all_matched_upcs.update(r["upc"] for r in found)

    missing_upcs = [u for u in upcs if u not in all_matched_upcs]
    if missing_upcs:
        import threading

        def _bg_fill_nearby(bg_upcs, bg_nearby, bg_user_id):
            from mealrunner.database import get_connection
            from mealrunner.pricing import _poll_single_product
            import time as _time
            try:
                with get_connection() as bg_conn:
                    for upc in bg_upcs:
                        for store in bg_nearby:
                            try:
                                price_data = _poll_single_product(upc, store["location_id"])
                                if price_data:
                                    bg_conn.execute(
                                        text("""INSERT INTO product_prices
                                            (upc, location_id, store_chain, price, promo_price, in_stock, source, user_id, fulfillment)
                                            VALUES (:upc, :loc, 'kroger', :price, :promo, :stock, 'nearby', :uid, 'curbside')"""),
                                        {"upc": upc, "loc": store["location_id"],
                                         "price": price_data["price"],
                                         "promo": price_data.get("promo_price"),
                                         "stock": price_data.get("in_stock"),
                                         "uid": bg_user_id},
                                    )
                                _time.sleep(0.5)
                            except Exception:
                                pass
                    bg_conn.commit()
            except Exception:
                pass

        nearby_copy = [dict(s) for s in nearby]
        threading.Thread(target=_bg_fill_nearby, args=(missing_upcs, nearby_copy, user_id), daemon=True).start()

    return {"comparisons": comparisons}


@router.post("/order/submit")
async def submit_order(request: Request):
    """Submit all selected products to Kroger cart.

    Accepts optional JSON body: { "kroger_user_id": "<user_id>" }
    If provided, verifies the user is in the same household and uses their token.
    If not provided, tries the current user first, then falls back to any
    household member with a linked account.
    """
    from mealrunner.kroger import add_to_cart, get_user_token_from_db
    from mealrunner.planner import load_rolling_week

    user_id = request.state.user_id
    real_user_id = request.state.real_user_id
    conn = _conn()
    mw = load_rolling_week(conn, user_id)
    trip = _ensure_active_trip(conn, mw, user_id)

    # Submit chokepoint: only pull rows that are genuinely active (not in a
    # closed state). select_product's UPDATE matches by LOWER(name) without a
    # state filter, so a hidden row sharing a name with the active pick can
    # get its product_upc re-stamped — without these guards, Kroger would
    # receive the same UPC twice and double the cart quantity.
    rows = conn.execute(
        text("""SELECT product_upc, quantity FROM grocery_items
           WHERE user_id = :user_id AND product_upc != '' AND ordered = 1 AND submitted_at IS NULL
             AND checked = 0 AND have_it = 0 AND removed = 0
             AND COALESCE(receipt_status, '') = ''"""),
        {"user_id": user_id},
    ).fetchall()

    if not rows:
        return {"ok": False, "error": "No products selected"}

    # Determine which Kroger account to use
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    kroger_user_id = body.get("kroger_user_id")
    token = None

    # Token fetch may trigger a sync OAuth refresh POST to Kroger when
    # the access token has expired (30-min lifetime). Run each call off
    # the event loop so a slow refresh doesn't freeze every other in-flight
    # request — same shape as the add_to_cart offload below.
    import anyio

    if kroger_user_id:
        if kroger_user_id == real_user_id:
            # Using own account — no access check needed
            token = await anyio.to_thread.run_sync(get_user_token_from_db, conn, real_user_id)
        else:
            # Using another member's account — verify household + allow_household
            hh_row = conn.execute(
                text("SELECT household_id FROM household_members WHERE user_id = :uid"),
                {"uid": real_user_id},
            ).fetchone()
            if hh_row:
                member = conn.execute(
                    text("""SELECT hm.user_id FROM household_members hm
                        JOIN user_kroger_tokens ukt ON ukt.user_id = hm.user_id
                        WHERE hm.household_id = :hh_id AND hm.user_id = :target_uid
                          AND ukt.allow_household = 1"""),
                    {"hh_id": hh_row["household_id"], "target_uid": kroger_user_id},
                ).fetchone()
                if member:
                    token = await anyio.to_thread.run_sync(get_user_token_from_db, conn, kroger_user_id)
        if not token:
            return {"ok": False, "error": "Selected account is not available."}
    else:
        # Try current user first
        token = await anyio.to_thread.run_sync(get_user_token_from_db, conn, real_user_id)
        if not token:
            # Fall back to any household member's token that has opted in
            hh_row = conn.execute(
                text("SELECT household_id FROM household_members WHERE user_id = :uid"),
                {"uid": real_user_id},
            ).fetchone()
            if hh_row:
                hh_tokens = conn.execute(
                    text("""SELECT hm.user_id FROM household_members hm
                        JOIN user_kroger_tokens ukt ON ukt.user_id = hm.user_id
                        WHERE hm.household_id = :hh_id AND ukt.allow_household = 1
                        ORDER BY hm.role ASC LIMIT 1"""),
                    {"hh_id": hh_row["household_id"]},
                ).fetchone()
                if hh_tokens:
                    token = await anyio.to_thread.run_sync(get_user_token_from_db, conn, hh_tokens["user_id"])

        if not token:
            return {"ok": False, "error": "No linked store account. Connect in Preferences."}

    items = [{"upc": r["product_upc"], "qty": r["quantity"]} for r in rows]
    # Mark submitted BEFORE calling Kroger — if the process dies mid-request,
    # items won't re-appear on the order page for a duplicate submit.
    # WHERE clause MUST mirror the SELECT chokepoint above. Otherwise a row
    # excluded from the Kroger payload (e.g. stale receipt_status from a
    # prior trip) still gets stamped here — vanishing from the order page
    # while Kroger never received it.
    conn.execute(
        text("""UPDATE grocery_items SET submitted_at = CURRENT_TIMESTAMP,
                status = 'ordered'
            WHERE user_id = :user_id
              AND product_upc != '' AND ordered = 1 AND submitted_at IS NULL
              AND checked = 0 AND have_it = 0 AND removed = 0
              AND COALESCE(receipt_status, '') = ''"""),
        {"user_id": user_id},
    )
    conn.commit()
    try:
        # add_to_cart is a sync HTTP PUT with 15s timeout. Without offload it
        # would block the uvicorn event loop, freezing the whole app for every
        # other in-flight request. Release the DB conn while we're out, too.
        import anyio
        with release_db_during_io():
            await anyio.to_thread.run_sync(lambda: add_to_cart(items, token=token))
        return {"ok": True, "count": len(items)}
    except Exception as e:
        # Roll back submitted_at so user can retry. Mirror the SELECT filter
        # above — only clear rows that match the same submit pool, otherwise
        # a Kroger error would NULL submitted_at on legitimately-finalized
        # receipt-reconciled rows.
        conn = _conn()  # release_db_during_io swapped in a fresh request conn
        conn.execute(
            text("""UPDATE grocery_items SET submitted_at = NULL,
                    status = 'active'
                WHERE user_id = :user_id AND product_upc != '' AND ordered = 1
                  AND checked = 0 AND have_it = 0 AND removed = 0
                  AND COALESCE(receipt_status, '') = ''"""),
            {"user_id": user_id},
        )
        conn.commit()
        logger.exception("Failed to add items to cart")
        return {"ok": False, "error": "Failed to add items to cart"}


# ── Receipt ───────────────────────────────────────────────


def _friendly_receipt_name(raw: str) -> str:
    """Clean a receipt item description for display as a candidate chip.
    Drops trademark glyphs and the size suffix after the last comma so
    'Mezzetta Family Recipes Marinara Sauce, 24.5 oz' becomes
    'Mezzetta Family Recipes Marinara Sauce'."""
    if not raw:
        return ""
    s = raw
    for ch in ("®", "™", "©"):  # ®, ™, ©
        s = s.replace(ch, "")
    if "," in s:
        s = s.rsplit(",", 1)[0]
    return s.strip()


def _candidate_extras_for_grocery(grocery_name: str, extras: list[dict], top_n: int = 3) -> list[dict]:
    """Return up to top_n receipt extras that share any meaningful token
    with the grocery item name. Loose inclusion predicate (any 3+ char
    stem-aware overlap) so the UI can surface plausible matches the
    strict auto-matcher's 0.6 threshold rejected. User picks which (if
    any) is the real match — no threshold tuning chase."""
    import re

    def _toks(s: str) -> set[str]:
        return {w for w in re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split() if len(w) >= 3}

    g_words = _toks(grocery_name)
    if not g_words:
        return []
    scored: list[tuple[int, dict]] = []
    for e in extras:
        e_words = _toks(e.get("item_name", ""))
        overlap = 0
        for gw in g_words:
            for ew in e_words:
                # Stem-aware: "beans" matches "bean", "blend" matches "blended"
                if gw.startswith(ew) or ew.startswith(gw):
                    overlap += 1
                    break
        if overlap > 0:
            scored.append((overlap, e))
    scored.sort(key=lambda x: -x[0])
    return [
        {
            "item_name": e["item_name"],
            "display_name": _friendly_receipt_name(e["item_name"]),
            "price": e.get("price"),
            "upc": e.get("upc", ""),
        }
        for _, e in scored[:top_n]
    ]


@router.get("/receipt")
async def get_receipt(request: Request):
    """Get receipt/reconciliation state for the active trip."""
    user_id = request.state.user_id
    conn = _conn()
    trip = _get_active_trip(conn, user_id)
    if not trip:
        return {"has_trip": False}

    order_source = trip["order_source"] if "order_source" in trip.keys() else "none"
    has_receipt = bool(trip["receipt_data"]) if "receipt_data" in trip.keys() and trip["receipt_data"] else False

    # Note: the auto-acknowledge sweep that used to run here on every
    # receipt-page GET now lives in _process_receipt — it fires when a new
    # receipt is uploaded instead of mutating state on a read endpoint.

    # Get trip items, excluding acknowledged matched/substituted/dismissed
    # rows (purchase log, not queue). Scope to the last 5 days — groceries
    # are bought for meals 1-5 days out, so anything older is stale state
    # from a prior trip's reconciliation that never got cleaned up.
    rows = conn.execute(
        text("""SELECT * FROM grocery_items WHERE user_id = :user_id
           AND NOT (receipt_status IN ('matched', 'substituted', 'dismissed', 'not_fulfilled')
                    AND receipt_acknowledged = 1)
           AND added_at >= NOW() - INTERVAL '5 days'
           ORDER BY shopping_group, name"""),
        {"user_id": user_id},
    ).fetchall()

    # Collect UPCs that will need ratings (matched/substituted items)
    items = []
    has_ordered = False
    has_checked = False
    for r in rows:
        if r["checked"]:
            has_checked = True
        if r["ordered"]:
            has_ordered = True
        try:
            have_it = bool(r["have_it"])
        except (KeyError, Exception):
            have_it = False
        try:
            removed = bool(r["removed"])
        except (KeyError, Exception):
            removed = False
        try:
            acknowledged = bool(r["receipt_acknowledged"])
        except (KeyError, Exception):
            acknowledged = False
        try:
            notes = r["notes"] or ""
        except (KeyError, Exception):
            notes = ""
        for_meals_str = r["for_meals"] or ""
        for_meals = [m for m in for_meals_str.split(",") if m]
        items.append({
            "id": r["id"],
            "name": r["name"],
            "shopping_group": r["shopping_group"],
            "source": r["source"],
            "for_meals": for_meals,
            "notes": notes,
            "checked": bool(r["checked"]),
            "ordered": bool(r["ordered"]),
            "have_it": have_it,
            "removed": removed,
            "product_upc": r["product_upc"],
            "product_name": r["product_name"],
            "product_brand": r["product_brand"],
            "product_size": r["product_size"],
            "product_price": r["product_price"],
            "product_image": r["product_image"],
            "receipt_item": r["receipt_item"],
            "receipt_price": r["receipt_price"],
            "receipt_upc": r["receipt_upc"],
            "receipt_status": r["receipt_status"],
            "receipt_acknowledged": acknowledged,
        })

    # Categorize
    matched = [i for i in items if i["receipt_status"] == "matched"]
    substituted = [i for i in items if i["receipt_status"] == "substituted"]
    not_fulfilled = [i for i in items if i["receipt_status"] == "not_fulfilled"]
    unresolved = [i for i in items if not i["receipt_status"]]

    # Fetch your_rating for reconciled items (matched + substituted) in one
    # round-trip. Per-item product_ratings lookup used to fire N queries here
    # — visible delay on receipts with many matched items.
    from mealrunner.kroger import _make_product_key
    reconciled = matched + substituted
    keys_by_item = {}
    for item in reconciled:
        upc = item.get("receipt_upc") or item.get("product_upc") or ""
        brand = item.get("product_brand") or ""
        desc = item.get("receipt_item") or item.get("product_name") or ""
        pk = _make_product_key(upc, brand, desc)
        item["product_key"] = pk
        keys_by_item[id(item)] = pk

    rating_by_key: dict[str, int] = {}
    distinct_keys = {pk for pk in keys_by_item.values() if pk}
    if distinct_keys:
        placeholders = ", ".join(f":k{i}" for i in range(len(distinct_keys)))
        params = {f"k{i}": k for i, k in enumerate(distinct_keys)}
        params["user_id"] = user_id
        rating_rows = conn.execute(
            text(f"SELECT product_key, rating FROM product_ratings "
                 f"WHERE user_id = :user_id AND product_key IN ({placeholders})"),
            params,
        ).fetchall()
        rating_by_key = {r["product_key"]: r["rating"] for r in rating_rows}

    for item in reconciled:
        item["rating"] = rating_by_key.get(keys_by_item[id(item)], 0)

    # Fetch extra items (unmatched receipt items)
    try:
        extras_rows = conn.execute(
            text("SELECT item_name, price, upc, brand FROM receipt_extra_items WHERE user_id = :user_id AND dismissed = 0 ORDER BY id"),
            {"user_id": user_id},
        ).fetchall()
        extras = [{"item_name": r["item_name"], "price": r["price"], "upc": r["upc"], "brand": r["brand"]} for r in extras_rows]
    except Exception:
        extras = []

    # Attach candidate matches to each not_fulfilled item so the receipt
    # page can render "Possible matches?" chips. Loose inclusion lets the
    # user rescue cases the strict matcher missed (hyphen-collapse on
    # tri-blend/tri-bean, frozen pizza vs Pepperoni Pizza, etc.) without
    # us chasing every normalizer edge case in the auto-matcher itself.
    for item in not_fulfilled:
        item["candidate_matches"] = _candidate_extras_for_grocery(item["name"], extras)

    return {
        "has_trip": True,
        "order_source": order_source,
        "has_receipt": has_receipt,
        "has_ordered": has_ordered,
        "has_checked": has_checked,
        "matched": matched,
        "substituted": substituted,
        "not_fulfilled": not_fulfilled,
        "unresolved": unresolved,
        "extras": extras,
    }


_EMPTY_RECEIPT_META = {"store": "", "store_location": "", "order_date": "",
                       "order_number": "", "total_price": None}


def _parse_receipt_by_type(receipt_type: str, content: str, grocery_names: list[str] | None = None):
    """Internal: parse receipt content by type. Only called from trusted code paths.

    Returns (items, footer_count, metadata):
      - footer_count: chain-printed total item count from the receipt
        (e.g. Kroger PDF "58 Items"); None if the parser can't extract one.
      - metadata: dict with store, store_location, order_date, order_number,
        total_price. Empty/None for parsers that don't extract them.
    """
    from mealrunner.reconcile import (
        parse_receipt_text, parse_receipt_pdf, parse_receipt_image,
        parse_receipt_email,
    )
    if receipt_type == "pdf_path":
        return parse_receipt_pdf(content)
    elif receipt_type == "image_path":
        return parse_receipt_image(content, grocery_names=grocery_names)
    elif receipt_type == "eml_path":
        return parse_receipt_email(content), None, dict(_EMPTY_RECEIPT_META)
    else:
        return parse_receipt_text(content), None, dict(_EMPTY_RECEIPT_META)


async def _process_receipt(receipt_type: str, content: str, request: Request):
    """Shared receipt processing: parse, match, store. Called by both upload endpoints."""
    from mealrunner.reconcile import diff_order, diff_grocery_list

    user_id = request.state.user_id
    conn = _conn()
    trip = _get_active_trip(conn, user_id)
    if not trip:
        return {"ok": False, "error": "No active trip"}

    # Bought→settled auto-promotion lives in _ensure_active_trip now (3-day
    # uniform timeout). Removed the duplicate 10-day matched/substituted
    # sweep that used to run here.

    # Gather grocery names for image receipts (enables single-call matching)
    # Scope to unchecked items: submitted (sent to store) + active (might have grabbed in-store)
    grocery_names = None
    if receipt_type == "image_path":
        try:
            name_rows = conn.execute(
                text("""SELECT name FROM grocery_items WHERE user_id = :user_id
                   AND receipt_status IN ('', 'not_fulfilled')"""),
                {"user_id": user_id},
            ).fetchall()
            grocery_names = [r["name"] for r in name_rows]
        except Exception:
            pass

    # Parse receipt
    try:
        receipt_items, footer_count, receipt_meta = _parse_receipt_by_type(receipt_type, content, grocery_names=grocery_names)
    except Exception as e:
        logger.exception("Failed to parse receipt")
        return {"ok": False, "error": "Failed to parse receipt"}

    if not receipt_items:
        return {"ok": False, "error": "No items found on receipt"}

    # Append receipt data (support multiple receipts per trip). Each entry is
    # a dict with metadata + items. Legacy entries stored as a bare list of
    # items get wrapped on read so the shape is uniform going forward.
    import json
    new_entry = {
        "store": receipt_meta.get("store", ""),
        "store_location": receipt_meta.get("store_location", ""),
        "order_date": receipt_meta.get("order_date", ""),
        "order_number": receipt_meta.get("order_number", ""),
        "total_price": receipt_meta.get("total_price"),
        "footer_count": footer_count,
        "items": receipt_items,
    }
    existing_data = trip["receipt_data"] if "receipt_data" in trip.keys() and trip["receipt_data"] else None
    if existing_data:
        try:
            all_receipts = json.loads(existing_data)
            if not isinstance(all_receipts, list):
                all_receipts = []
            # (Removed legacy bare-list wrapping migration — prod has no
            # legacy entries left; new entries are always dict-shaped.)
            # Dedup: if the same receipt was uploaded twice (user tapped
            # upload twice, page refresh mid-parse, etc.), skip the whole
            # pipeline. Strongest signal is order_number alone; for
            # parsers that can't extract it (image/eml without an order#),
            # fall back to (store, order_date, total_price). If none of
            # these are present, treat as new and process normally.
            def _entry_key(e):
                onum = (e.get("order_number") or "").strip()
                if onum:
                    return ("by_order", onum)
                store = (e.get("store") or "").strip().lower()
                date = (e.get("order_date") or "").strip()
                total = e.get("total_price")
                if store and date and total is not None:
                    return ("by_meta", store, date, round(float(total), 2))
                return None
            new_key = _entry_key(new_entry)
            if new_key is not None:
                for existing_entry in all_receipts:
                    if _entry_key(existing_entry) == new_key:
                        logger.info(
                            "Duplicate receipt upload skipped (key=%r) user=%s",
                            new_key, user_id,
                        )
                        return {
                            "ok": True,
                            "duplicate": True,
                            "matched": 0,
                            "not_fulfilled": 0,
                        }
            all_receipts.append(new_entry)
        except (json.JSONDecodeError, TypeError):
            all_receipts = [new_entry]
    else:
        all_receipts = [new_entry]
    conn.execute(
        text("UPDATE grocery_state SET receipt_data = :data, receipt_parsed_at = CURRENT_TIMESTAMP WHERE user_id = :user_id"),
        {"data": json.dumps(all_receipts), "user_id": user_id},
    )

    # Every receipt item flows through matching. The earlier cross-trip
    # extras-name filter was removed because it silently blocked legitimate
    # re-matches (e.g. LaCroix saved as an extra in May would prevent
    # today's parse from matching it). Dedup now happens at the
    # extras-INSERT step instead.
    new_receipt_items = list(receipt_items)

    # Matcher candidate scope. Active, ordered, and bought rows are eligible.
    # Settled / removed / have_it / dismissed are permanently terminal and
    # never re-enter reconciliation. No age cutoff — bought rows auto-settle
    # after 3 days via _ensure_active_trip, so the status filter alone bounds
    # the window. Manual-bought items (swipe-checked as you unpack) are in
    # scope so a later receipt upload still reconciles against them.
    rows = conn.execute(
        text("""SELECT * FROM grocery_items WHERE user_id = :user_id
           AND status IN ('active', 'ordered', 'bought')
           ORDER BY name"""),
        {"user_id": user_id},
    ).fetchall()

    # Check if receipt items have pre-matched grocery_match metadata (from image parser)
    has_pre_matches = any(ri.get("grocery_match") for ri in new_receipt_items)

    # Apply pre-matches from image parser before standard matching
    if has_pre_matches:
        grocery_items_by_name = {r["name"].lower(): r for r in rows}
        pre_matched_trip_names = set()
        still_unmatched = []
        for ri in new_receipt_items:
            gm = ri.get("grocery_match", "")
            if gm and gm.lower() in grocery_items_by_name:
                r = grocery_items_by_name[gm.lower()]
                # Prefer raw (the actual line text from the receipt) over item
                # (which is the grocery name for matched image-parser items).
                receipt_text = ri.get("raw") or ri.get("item", "")
                # Stamp every recent unacknowledged row sharing this name.
                # The per-meal duplicate model means one canonical purchase
                # ("hot dog buns") can have multiple sibling rows — one per
                # planned meal. A single LIMIT 1 stamp leaves the others
                # stuck in not_fulfilled limbo and creates the "21 things
                # missing from receipt, mostly dupes" experience. The 21-day
                # / ack=0 scope keeps historical rows from prior trips out.
                conn.execute(
                    text("""UPDATE grocery_items SET
                           receipt_item = :receipt_item, receipt_price = :receipt_price,
                           receipt_upc = :receipt_upc, receipt_status = 'matched',
                           receipt_acknowledged = 0, status = 'bought'
                       WHERE id IN (
                           SELECT id FROM grocery_items
                           WHERE user_id = :user_id AND LOWER(name) = LOWER(:name)
                             AND COALESCE(receipt_status, '') IN ('', 'not_fulfilled')
                             AND have_it = 0 AND removed = 0
                             AND receipt_acknowledged = 0
                             AND added_at >= NOW() - INTERVAL '21 days'
                       )"""),
                    {"receipt_item": receipt_text,
                     "receipt_price": ri.get("price"),
                     "receipt_upc": ri.get("upc", ""),
                     "user_id": user_id, "name": gm},
                )
                pre_matched_trip_names.add(gm.lower())
            else:
                still_unmatched.append(ri)
        # Update rows to exclude pre-matched items
        rows = [r for r in rows if r["name"].lower() not in pre_matched_trip_names]
        new_receipt_items = still_unmatched
        total_matched = len(pre_matched_trip_names)
        total_not_fulfilled = 0
    else:
        total_matched = 0
        total_not_fulfilled = 0

    # Split remaining items: ordered (have UPCs) use diff_order, checked use diff_grocery_list
    upc_rows = [r for r in rows if r["product_upc"]]
    name_rows = [r for r in rows if not r["product_upc"]]
    receipt_remaining = list(new_receipt_items)

    # Pass 1: match ordered items by UPC
    upc_unmatched_names = []  # submitted items that failed UPC + fuzzy match — get a second chance
    if upc_rows:
        submitted = [{"upc": r["product_upc"], "product": r["product_name"], "item": r["name"]} for r in upc_rows]
        diff = diff_order(submitted, receipt_remaining)

        for m in diff["matched"]:
            r = m["receipt"]
            # UPC match = exact product; name match = different UPC = substitution
            status = "matched" if m.get("match") == "upc" else "substituted"
            # Multi-row stamp same as the pre-match path above. Recent /
            # ack=0 / not-finished scoping keeps historical rows untouched.
            conn.execute(
                text("""UPDATE grocery_items SET
                       receipt_item = :receipt_item, receipt_price = :receipt_price, receipt_upc = :receipt_upc,
                       receipt_status = :rs, receipt_acknowledged = 0, status = 'bought'
                   WHERE id IN (
                       SELECT id FROM grocery_items
                       WHERE user_id = :user_id AND LOWER(name) = LOWER(:name)
                         AND COALESCE(receipt_status, '') IN ('', 'not_fulfilled')
                         AND have_it = 0 AND removed = 0
                         AND receipt_acknowledged = 0
                         AND added_at >= NOW() - INTERVAL '21 days'
                   )"""),
                {"receipt_item": r.get("item", ""), "receipt_price": r.get("price"),
                 "receipt_upc": r.get("upc", ""),
                 "rs": status,
                 "user_id": user_id, "name": m["submitted"]["item"]},
            )
        total_matched += len(diff["matched"])

        # Don't mark as not_fulfilled yet — give them a second chance via grocery list matching
        upc_unmatched_names = [r.get("item", r.get("product", "")) for r in diff["removed"]]

        # Remaining receipt items for pass 2
        receipt_remaining = diff.get("added", [])

    # Pass 2: match by grocery name (includes name-only items + UPC items that failed pass 1)
    all_name_candidates = [r["name"] for r in name_rows] + upc_unmatched_names
    if all_name_candidates and receipt_remaining:
        diff2 = diff_grocery_list(all_name_candidates, receipt_remaining)

        for m in diff2["matched"]:
            r = m["receipt"]
            # Multi-row stamp same as the pre-match / Pass 1 paths above.
            conn.execute(
                text("""UPDATE grocery_items SET
                       receipt_item = :receipt_item, receipt_price = :receipt_price, receipt_upc = :receipt_upc,
                       receipt_status = 'matched', receipt_acknowledged = 0, status = 'bought'
                   WHERE id IN (
                       SELECT id FROM grocery_items
                       WHERE user_id = :user_id AND LOWER(name) = LOWER(:name)
                         AND COALESCE(receipt_status, '') IN ('', 'not_fulfilled')
                         AND have_it = 0 AND removed = 0
                         AND receipt_acknowledged = 0
                         AND added_at >= NOW() - INTERVAL '21 days'
                   )"""),
                {"receipt_item": r.get("item", ""), "receipt_price": r.get("price"),
                 "receipt_upc": r.get("upc", ""),
                 "user_id": user_id, "name": m["grocery_name"]},
            )
        total_matched += len(diff2["matched"])

        # Remaining receipt items after pass 2
        matched_grocery_names = {m["grocery_name"].lower() for m in diff2["matched"]}
        receipt_remaining = diff2.get("unmatched", [])

        # Reset unmatched UPC items to not_fulfilled — they were actually
        # sent to Kroger and didn't come back. Plain name_rows (no
        # product_upc) are NOT touched: the user never ordered them via
        # Kroger, so "not_fulfilled" doesn't apply. Tagging them was the
        # source of phantom rows reappearing on the grocery list (the
        # tagged row becomes invisible to meal sync, and meal sync inserts
        # a fresh sibling).
        # submitted_at preserved so the auto-settle housekeeping can clock the
        # 3-day window from the order submit. Clearing it would lose the only
        # timestamp tying this row to its place in the order flow.
        _not_fulfilled_sql = """UPDATE grocery_items SET receipt_status = 'not_fulfilled',
               ordered = 0,
               product_upc = '', product_name = '', product_brand = '',
               product_size = '', product_price = NULL, product_image = '',
               receipt_item = '', receipt_upc = '', receipt_price = NULL,
               status = 'ordered'"""
        for uname in upc_unmatched_names:
            if uname.lower() not in matched_grocery_names:
                conn.execute(
                    text(_not_fulfilled_sql + " WHERE user_id = :user_id AND LOWER(name) = LOWER(:name)"),
                    {"user_id": user_id, "name": uname},
                )
                total_not_fulfilled += 1
    elif all_name_candidates:
        # No receipt items left — only UPC-ordered items get the
        # not_fulfilled tag. See comment above for why name_rows are
        # left alone.
        # submitted_at preserved so the auto-settle housekeeping can clock the
        # 3-day window from the order submit. Clearing it would lose the only
        # timestamp tying this row to its place in the order flow.
        _not_fulfilled_sql = """UPDATE grocery_items SET receipt_status = 'not_fulfilled',
               ordered = 0,
               product_upc = '', product_name = '', product_brand = '',
               product_size = '', product_price = NULL, product_image = '',
               receipt_item = '', receipt_upc = '', receipt_price = NULL,
               status = 'ordered'"""
        for uname in upc_unmatched_names:
            conn.execute(
                text(_not_fulfilled_sql + " WHERE user_id = :user_id AND LOWER(name) = LOWER(:name)"),
                {"user_id": user_id, "name": uname},
            )
            total_not_fulfilled += 1

    # Auto-save preferences for matched items
    from mealrunner.kroger import save_preference, KrogerProduct, _make_product_key
    all_matched_items = conn.execute(
        text("""SELECT name, receipt_item, receipt_upc, product_upc, product_brand
           FROM grocery_items WHERE user_id = :user_id AND receipt_status = 'matched'"""),
        {"user_id": user_id},
    ).fetchall()
    for mi in all_matched_items:
        receipt_name = mi["receipt_item"] or mi["name"]
        upc = mi["receipt_upc"] or mi["product_upc"] or ""
        brand = mi["product_brand"] or ""
        try:
            pref_product = KrogerProduct(
                product_id="", upc=upc, description=receipt_name,
                brand=brand, size="",
            )
            save_preference(conn, user_id, mi["name"].lower(), pref_product, source="receipt")
        except Exception:
            pass

    # Log receipt prices for tracking
    from mealrunner.stores import get_kroger_location_id as _get_loc
    rcpt_location = _get_loc(conn, user_id) or ""
    rcpt_prices = []
    for mi in all_matched_items:
        upc = mi["receipt_upc"] or mi["product_upc"] or ""
        if upc:
            rcpt_prices.append({"upc": upc, "price": None, "promo_price": None})
    # Also log receipt items that have prices from the parsed receipt
    receipt_items_with_prices = conn.execute(
        text("SELECT receipt_upc, receipt_price FROM grocery_items WHERE user_id = :uid AND receipt_status IN ('matched', 'substituted') AND receipt_price IS NOT NULL"),
        {"uid": user_id},
    ).fetchall()
    for ri in receipt_items_with_prices:
        if ri["receipt_upc"]:
            rcpt_prices.append({"upc": ri["receipt_upc"], "price": ri["receipt_price"], "promo_price": None})
    if rcpt_prices:
        _log_prices(conn, rcpt_prices, rcpt_location, "receipt", user_id)

    # Save unmatched receipt items as extras. Per-row inserts with no silent
    # excepts: a swallowed batch failure had been dropping every receipt's
    # extras for the entire app's history (zero rows ever written across all
    # users). Per-row INSERTs surface any real error instead of pretending it
    # was a no-op. Dedupe at insert using _extras_dedup_key so ligature /
    # punctuation / unicode variants collapse to the same key.
    extras_attempted = 0
    extras_written = 0
    logger.info(
        "Extras-write: receipt_remaining=%d user=%s",
        len(receipt_remaining), user_id,
    )
    if receipt_remaining:
        existing_extras = conn.execute(
            text("SELECT item_name FROM receipt_extra_items WHERE user_id = :uid"),
            {"uid": user_id},
        ).fetchall()
        existing_extra_keys = {_extras_dedup_key(r["item_name"]) for r in existing_extras}
        for ri in receipt_remaining:
            display_name = ri.get("item") or ri.get("raw") or ""
            if not display_name:
                continue
            key = _extras_dedup_key(display_name)
            if key in existing_extra_keys:
                continue
            extras_attempted += 1
            conn.execute(
                text("""INSERT INTO receipt_extra_items (user_id, item_name, price, upc, brand)
                   VALUES (:user_id, :item_name, :price, :upc, :brand)"""),
                {"user_id": user_id, "item_name": display_name,
                 "price": ri.get("price"), "upc": ri.get("upc", ""),
                 "brand": ri.get("brand", "")},
            )
            extras_written += 1
            existing_extra_keys.add(key)
        logger.info(
            "Extras-write: attempted=%d written=%d user=%s",
            extras_attempted, extras_written, user_id,
        )

    conn.commit()

    result = {
        "ok": True,
        "matched": total_matched,
        "not_fulfilled": total_not_fulfilled,
        "extras_remaining": len(receipt_remaining),
        "extras_written": extras_written,
    }
    if footer_count is not None:
        parsed_qty = sum((ri.get("qty") or 1) for ri in receipt_items)
        result["item_count_footer"] = footer_count
        result["item_count_parsed"] = parsed_qty
        if footer_count != parsed_qty:
            gap = footer_count - parsed_qty
            result["item_count_gap"] = gap
            logger.warning(
                "Receipt item-count gap (%s): footer=%d parsed=%d gap=%+d user=%s",
                receipt_type, footer_count, parsed_qty, gap, user_id,
            )
    return result


@router.post("/receipt/upload")
async def upload_receipt(body: dict, request: Request):
    """Upload and parse a receipt. Public endpoint accepts text only."""
    # Rate limit: max 10 receipt uploads per user per minute
    user_id = request.state.user_id
    throttled = _check_throttle(user_id, "receipt_upload", 10, 60)
    if throttled:
        return throttled

    receipt_type = body.get("type", "text")
    content = body.get("content", "")

    # Block path-based types from the public endpoint (server-side file read)
    if receipt_type in ("pdf_path", "image_path", "eml_path"):
        return {"ok": False, "error": "File path types not accepted. Use /receipt/upload-file instead."}

    return await _process_receipt(receipt_type, content, request)


@router.post("/receipt/upload-file")
async def upload_receipt_file(request: Request, file: UploadFile = File(...)):
    """Upload a receipt file (PDF, image, or .eml) and parse + reconcile it."""
    import tempfile
    import os

    user_id = request.state.user_id

    # Rate limit: max 10 receipt uploads per user per minute (shared with /receipt/upload)
    throttled = _check_throttle(user_id, "receipt_upload", 10, 60)
    if throttled:
        return throttled

    conn = _conn()
    trip = _get_active_trip(conn, user_id)
    if not trip:
        return {"ok": False, "error": "No active trip"}

    # Save uploaded file to temp location
    suffix = os.path.splitext(file.filename or "")[1].lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        logger.info("Receipt upload: filename=%r bytes=%d suffix=%s", file.filename, len(content), suffix)
        tmp_path = tmp.name

    try:
        # Route to correct parser (path types are safe here — we control the temp file)
        if suffix == ".pdf":
            rtype, rcontent = "pdf_path", tmp_path
        elif suffix == ".eml":
            rtype, rcontent = "eml_path", tmp_path
        elif suffix in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            rtype, rcontent = "image_path", tmp_path
        else:
            rtype, rcontent = "text", content.decode("utf-8", errors="replace")

        return await _process_receipt(rtype, rcontent, request)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


@router.post("/receipt/resolve")
async def resolve_receipt_item(body: dict, request: Request):
    """Resolve a receipt item. {id: int, status: 'matched'|'substituted'|'not_fulfilled'|'recover'|'dismissed'}"""
    user_id = request.state.user_id
    conn = _conn()

    item_id = body.get("id")
    status = body.get("status")
    if item_id is None or not status:
        return {"ok": False, "error": "id and status required"}

    ALLOWED_STATUSES = {"matched", "substituted", "not_fulfilled", "recover", "dismissed"}
    if status not in ALLOWED_STATUSES:
        return JSONResponse(status_code=400, content={"ok": False, "error": f"Invalid status '{status}'. Must be one of: {', '.join(sorted(ALLOWED_STATUSES))}"})

    # Any user-driven resolve action acknowledges the item — it leaves the
    # receipt-page queue regardless of which action was taken.
    if status == "recover":
        # Put item back on the active grocery list (un-order it, clear submitted so it can be re-ordered).
        # checked is cleared because the stale-order soft-delete may have set it.
        conn.execute(
            text("""UPDATE grocery_items SET ordered = 0, submitted_at = NULL, receipt_status = '',
                   receipt_acknowledged = 1,
                   checked = 0, checked_at = NULL,
                   product_upc = '', product_name = '', product_brand = '', product_size = '',
                   product_price = NULL, product_image = '',
                   status = 'active'
               WHERE id = :id AND user_id = :user_id"""),
            {"id": item_id, "user_id": user_id},
        )
    elif status == "dismissed":
        # Acknowledged as not needed — mark so it doesn't keep prompting
        conn.execute(
            text("""UPDATE grocery_items SET receipt_status = 'dismissed', receipt_acknowledged = 1,
                   status = 'dismissed'
               WHERE id = :id AND user_id = :user_id"""),
            {"id": item_id, "user_id": user_id},
        )
    elif status == "matched":
        # User confirmed the match — done. Settles immediately.
        conn.execute(
            text("""UPDATE grocery_items SET receipt_status = 'matched',
                   receipt_acknowledged = 1,
                   checked = 1, checked_at = CURRENT_TIMESTAMP, ordered = 0,
                   status = 'settled'
               WHERE id = :id AND user_id = :user_id"""),
            {"id": item_id, "user_id": user_id},
        )
    elif status == "not_fulfilled":
        # User clicked "Didn't get it" — fully reset to a plain active grocery
        # row. Carrying receipt_status='not_fulfilled' past this point is
        # residue with no consumer; every filter site otherwise has to
        # special-case it (see audit, session 89). Receipt-side state is
        # cleared along with order/product state and the stale-order
        # soft-delete's checked flag.
        conn.execute(
            text("""UPDATE grocery_items SET receipt_status = '',
                   receipt_acknowledged = 0,
                   receipt_item = '', receipt_upc = '', receipt_price = NULL,
                   ordered = 0, submitted_at = NULL,
                   checked = 0, checked_at = NULL,
                   product_upc = '', product_name = '', product_brand = '',
                   product_size = '', product_price = NULL, product_image = '',
                   status = 'active'
               WHERE id = :id AND user_id = :user_id"""),
            {"id": item_id, "user_id": user_id},
        )
    else:
        # Substituted ack — user confirmed the substitution. Settles immediately.
        conn.execute(
            text("""UPDATE grocery_items SET receipt_status = :rs, receipt_acknowledged = 1,
                   status = 'settled'
               WHERE id = :id AND user_id = :user_id"""),
            {"rs": status, "id": item_id, "user_id": user_id},
        )
    conn.commit()
    return {"ok": True}


@router.post("/receipt/match-extra")
async def match_extra_to_grocery(body: dict, request: Request):
    """Manually match an unmatched receipt item to a specific grocery row.
    {extra_name: str, grocery_id: int, receipt_price: float?, receipt_upc: str?}"""
    user_id = request.state.user_id
    conn = _conn()

    extra_name = body.get("extra_name", "").strip()
    grocery_id = body.get("grocery_id")
    receipt_price = body.get("receipt_price")
    receipt_upc = body.get("receipt_upc", "")

    if not extra_name or grocery_id is None:
        return {"ok": False, "error": "extra_name and grocery_id required"}

    # Update the chosen grocery row with receipt data and mark as matched + checked
    conn.execute(
        text("""UPDATE grocery_items SET
               receipt_item = :receipt_item, receipt_price = :receipt_price,
               receipt_upc = :receipt_upc, receipt_status = 'matched',
               receipt_acknowledged = 1,
               checked = 1, checked_at = CURRENT_TIMESTAMP, ordered = 0,
               status = 'settled'
           WHERE id = :grocery_id AND user_id = :user_id"""),
        {"receipt_item": extra_name, "receipt_price": receipt_price,
         "receipt_upc": receipt_upc,
         "grocery_id": grocery_id, "user_id": user_id},
    )

    # Remove from receipt_extra_items
    conn.execute(
        text("DELETE FROM receipt_extra_items WHERE user_id = :uid AND LOWER(item_name) = LOWER(:name)"),
        {"uid": user_id, "name": extra_name},
    )

    conn.commit()
    return {"ok": True}


@router.post("/receipt/dismiss-extra")
async def dismiss_extra(body: dict, request: Request):
    """Dismiss an unmatched receipt extra item."""
    user_id = request.state.user_id
    conn = _conn()
    trip = _get_active_trip(conn, user_id)
    if not trip:
        return {"ok": False}

    name = body.get("name", "").strip()
    if not name:
        return {"ok": False}

    # Hard-delete to match /receipt/match-extra's behavior. Soft-dismiss
    # (dismissed=1) left a ghost row that the insert-dedup couldn't see
    # past, silently blocking the same item from re-appearing on future
    # receipts. The `dismissed` column is now unused — left in place
    # rather than migrated to avoid disturbing other readers.
    conn.execute(
        text("DELETE FROM receipt_extra_items WHERE user_id = :uid AND LOWER(item_name) = LOWER(:name)"),
        {"uid": user_id, "name": name},
    )
    conn.commit()
    return {"ok": True}


@router.get("/purchases")
async def get_purchases(request: Request):
    """Get purchase history from permanent tables (survives trip item pruning)."""
    user_id = request.state.user_id
    conn = _conn()

    # Pull from product_preferences (every product the user has interacted with)
    # joined with product_ratings for thumbs up/down
    rows = conn.execute(
        text("""SELECT pp.search_term, pp.upc, pp.product_description, pp.size,
               pp.times_picked, pp.last_picked, pp.source, pp.rating,
               pp.brand, pp.product_key
           FROM product_preferences pp
           WHERE pp.user_id = :uid
           ORDER BY pp.last_picked DESC NULLS LAST, pp.product_description"""),
        {"uid": user_id},
    ).fetchall()

    purchases = []
    for r in rows:
        purchases.append({
            "name": r["search_term"],
            "receipt_item": r["product_description"],
            "receipt_price": None,
            "product_name": r["product_description"],
            "product_brand": r["brand"],
            "product_size": r["size"],
            "product_price": None,
            "product_image": "",
            "receipt_status": r["source"],
            "product_key": r["product_key"],
            "upc": r["upc"],
            "brand": r["brand"],
            "rating": r["rating"],
            "date": r["last_picked"] or "",
        })

    return {"purchases": purchases}


@router.post("/product/rate")
async def rate_product_endpoint(body: dict, request: Request):
    """Rate a product: {upc, rating, product_description?, brand?, product_key?}"""
    from mealrunner.kroger import rate_product, _make_product_key

    user_id = request.state.user_id
    upc = body.get("upc", "").strip()
    rating = body.get("rating")
    brand = body.get("brand", "").strip()
    product_key = body.get("product_key", "").strip()
    desc = body.get("product_description", "").strip()

    # Compute product_key if not provided
    if not product_key:
        product_key = _make_product_key(upc, brand, desc)

    if not product_key or rating not in (1, -1, 0):
        return {"ok": False, "error": "product identifier and rating (1, -1, or 0) required"}

    conn = _conn()
    rate_product(conn, upc, rating, desc, user_id, brand=brand, product_key=product_key)
    return {"ok": True, "product_key": product_key, "rating": rating}


@router.get("/product/favorites")
async def get_favorites(request: Request):
    """Get all rated products for the current user."""
    user_id = request.state.user_id
    conn = _conn()
    rows = conn.execute(
        text(
            "SELECT id, upc, product_description, brand, product_key, rating, updated_at "
            "FROM product_ratings WHERE user_id = :uid AND rating != 0 "
            "ORDER BY rating DESC, updated_at DESC"
        ),
        {"uid": user_id},
    ).fetchall()
    return {
        "items": [
            {
                "id": r["id"],
                "upc": r["upc"],
                "description": r["product_description"],
                "brand": r["brand"],
                "product_key": r["product_key"],
                "rating": r["rating"],
            }
            for r in rows
        ]
    }


# ── Regulars ─────────────────────────────────────────────


@router.get("/staples")
async def get_staples(request: Request):
    """List all staples for the user, optionally filtered by mode.

    Query: ?mode=every_trip or ?mode=keep_on_hand to filter; omit for all.
    """
    from mealrunner.staples import list_staples, VALID_MODES

    user_id = request.state.user_id
    conn = _conn()
    mode = request.query_params.get("mode")
    if mode is not None and mode not in VALID_MODES:
        return {"ok": False, "error": "invalid mode"}
    staples = list_staples(conn, user_id, mode=mode)
    resolve = _build_group_resolver(conn, user_id)
    return {
        "staples": [
            {
                "id": s.id,
                "name": s.name,
                "ingredient_id": s.ingredient_id,
                "shopping_group": resolve(s.name),
                "store_pref": s.store_pref,
                "mode": s.mode,
            }
            for s in staples
        ]
    }


@router.post("/staples")
async def post_staple(body: dict, request: Request):
    """Add a staple (or update mode on an existing one).

    Body: {name, mode: 'every_trip'|'keep_on_hand', shopping_group?, store_pref?}.
    """
    from mealrunner.staples import add_staple, VALID_MODES

    user_id = request.state.user_id
    conn = _conn()
    name = (body.get("name") or "").strip()
    mode = body.get("mode", "every_trip")
    if not name:
        return {"ok": False, "error": "name required"}
    if mode not in VALID_MODES:
        return {"ok": False, "error": "invalid mode"}

    s = add_staple(
        conn, user_id, name, mode,
        shopping_group=body.get("shopping_group", ""),
        store_pref=body.get("store_pref", "either"),
    )
    # Adding a staple is an explicit "I want this" signal — dismiss any
    # pending learning suggestion to remove or re-add it.
    conn.execute(
        text("INSERT INTO learning_dismissed (name, user_id) VALUES (:name, :user_id) ON CONFLICT DO NOTHING"),
        {"name": s.name.lower(), "user_id": user_id},
    )
    conn.commit()
    return {
        "id": s.id,
        "name": s.name,
        "shopping_group": s.shopping_group,
        "store_pref": s.store_pref,
        "mode": s.mode,
    }


@router.patch("/staples/{staple_id}")
async def patch_staple(staple_id: int, body: dict, request: Request):
    """Update a staple's mode and/or shopping group.

    Mode flip (every_trip ↔ keep_on_hand) is the replacement for the old
    "Move to pantry" / "Move to regulars" delete+add dance — same row,
    same id, just a different attribute.
    """
    from mealrunner.staples import update_staple, VALID_MODES

    user_id = request.state.user_id
    conn = _conn()
    mode = body.get("mode")
    group = body.get("shopping_group")
    if mode is not None and mode not in VALID_MODES:
        return {"ok": False, "error": "invalid mode"}

    s = update_staple(conn, user_id, staple_id, mode=mode, shopping_group=group)
    if s is None:
        return {"ok": False}

    if group is not None and s.name:
        # Persist as user override for grocery list rows too.
        conn.execute(
            text("""INSERT INTO user_item_groups (user_id, item_name, shopping_group)
               VALUES (:user_id, :name, :group)
               ON CONFLICT (user_id, item_name) DO UPDATE SET shopping_group = :group, updated_at = CURRENT_TIMESTAMP"""),
            {"user_id": user_id, "name": s.name.lower(), "group": group},
        )
        conn.commit()

    return {
        "id": s.id,
        "name": s.name,
        "shopping_group": s.shopping_group,
        "store_pref": s.store_pref,
        "mode": s.mode,
    }


@router.delete("/staples/{staple_id}")
async def delete_staple(staple_id: int, request: Request):
    """Delete a staple. The 'don't re-suggest' signal goes to learning_dismissed
    so the learning loop doesn't immediately re-suggest the item the user just
    removed."""
    from mealrunner.staples import remove_staple

    user_id = request.state.user_id
    conn = _conn()
    row = conn.execute(
        text("SELECT name FROM staples WHERE id = :id AND user_id = :user_id"),
        {"id": staple_id, "user_id": user_id},
    ).fetchone()
    if not row:
        return {"ok": False}
    remove_staple(conn, user_id, staple_id)
    conn.execute(
        text("INSERT INTO learning_dismissed (name, user_id) VALUES (:name, :user_id) ON CONFLICT DO NOTHING"),
        {"name": row["name"].lower(), "user_id": user_id},
    )
    conn.commit()
    return {"ok": True}


@router.get("/grocery/suggestions")
async def grocery_suggestions(request: Request):
    """Return all known item names for autocomplete (ingredients + the user's staples)."""
    from mealrunner.staples import list_staples

    user_id = request.state.user_id
    conn = _conn()
    names: set[str] = set()

    # All ingredients
    rows = conn.execute(text("SELECT name FROM ingredients")).fetchall()
    for row in rows:
        names.add(row["name"].lower())

    # User's staples (both modes)
    for s in list_staples(conn, user_id):
        if s.name:
            names.add(s.name.lower())

    return {"suggestions": sorted(names)}


# ── Recipes ──────────────────────────────────────────────


@router.get("/recipes")
async def get_recipes(request: Request):
    from mealrunner.recipes import list_recipes

    conn = _conn()
    recipes = list_recipes(conn, user_id=request.state.user_id)
    return {"recipes": [_recipe_dict(r) for r in recipes]}


@router.post("/recipes")
async def add_recipe(body: dict, request: Request):
    """Add a new recipe (name only, stub)."""
    conn = _conn()
    user_id = request.state.user_id
    name = body.get("name", "").strip().title()
    if not name:
        return {"ok": False}

    recipe_type = body.get("recipe_type", "meal")
    if recipe_type not in ("meal", "side"):
        recipe_type = "meal"

    existing = conn.execute(
        text("SELECT id FROM recipes WHERE LOWER(name) = :name AND user_id = :user_id AND recipe_type = :rtype"),
        {"name": name.lower(), "user_id": user_id, "rtype": recipe_type},
    ).fetchone()
    if existing:
        return {"ok": True, "id": existing["id"], "exists": True}

    defaults = {"effort": "medium", "cleanup": "medium"} if recipe_type == "meal" else {"effort": "easy", "cleanup": "easy"}
    cursor = conn.execute(
        text("""INSERT INTO recipes (name, cuisine, effort, cleanup, outdoor, kid_friendly, premade,
           prep_minutes, cook_minutes, servings, user_id, recipe_type)
           VALUES (:name, 'other', :effort, :cleanup, 0, 1, 0, 0, 0, 4, :user_id, :rtype)
           RETURNING id"""),
        {"name": name, "user_id": user_id, "rtype": recipe_type, **defaults},
    )
    recipe_id = cursor.fetchone()["id"]

    # Auto-add default ingredient for sides when name matches a known ingredient
    if recipe_type == "side":
        from mealrunner.planner import _auto_add_side_ingredient
        _auto_add_side_ingredient(conn, recipe_id, name)

    conn.commit()
    return {"ok": True, "id": recipe_id}


VALID_CUISINES = {"italian", "mexican", "asian", "american", "other"}


@router.post("/recipes/{recipe_id}/cuisine")
async def set_recipe_cuisine(recipe_id: int, body: dict, request: Request):
    """Set a meal's cuisine. The only recipe attribute users can impose; the
    cuisine filter in the picker keys off it."""
    conn = _conn()
    user_id = request.state.user_id
    cuisine = (body.get("cuisine") or "").strip().lower()
    if cuisine not in VALID_CUISINES:
        return {"ok": False, "error": "invalid cuisine"}
    conn.execute(
        text("UPDATE recipes SET cuisine = :c WHERE id = :id AND user_id = :u"),
        {"c": cuisine, "id": recipe_id, "u": user_id},
    )
    conn.commit()
    return {"ok": True}


@router.delete("/recipes/{recipe_id}")
async def delete_recipe(recipe_id: int, request: Request):
    """Remove a recipe. Won't delete if it's currently on the meal plan."""
    conn = _conn()
    user_id = request.state.user_id

    # Check if recipe is currently assigned to a meal in the rolling window
    in_use = conn.execute(
        text("SELECT COUNT(*) as cnt FROM meals WHERE recipe_id = :id AND user_id = :user_id"),
        {"id": recipe_id, "user_id": user_id},
    ).fetchone()
    if in_use["cnt"] > 0:
        return {"ok": False, "error": "Recipe is on your meal plan"}

    # Only delete if recipe belongs to this user
    recipe = conn.execute(
        text("SELECT id FROM recipes WHERE id = :id AND user_id = :user_id"),
        {"id": recipe_id, "user_id": user_id},
    ).fetchone()
    if not recipe:
        return {"ok": False, "error": "Recipe not found"}

    conn.execute(text("DELETE FROM recipe_ingredients WHERE recipe_id = :id"), {"id": recipe_id})
    conn.execute(text("DELETE FROM recipes WHERE id = :id AND user_id = :user_id"), {"id": recipe_id, "user_id": user_id})
    conn.commit()
    return {"ok": True}


@router.get("/recipes/{recipe_id}/ingredients")
async def get_recipe_ingredients(recipe_id: int, request: Request):
    """List ingredients for a recipe."""
    conn = _conn()
    user_id = request.state.user_id

    # Verify recipe belongs to this user
    own = conn.execute(
        text("SELECT id, notes FROM recipes WHERE id = :id AND user_id = :user_id"),
        {"id": recipe_id, "user_id": user_id},
    ).fetchone()
    if not own:
        return {"ingredients": [], "cooking_notes": ""}

    rows = conn.execute(
        text("""SELECT ri.id, i.name, i.aisle
           FROM recipe_ingredients ri
           JOIN ingredients i ON i.id = ri.ingredient_id
           WHERE ri.recipe_id = :recipe_id
           ORDER BY i.name"""),
        {"recipe_id": recipe_id},
    ).fetchall()
    try:
        cooking_notes = own["notes"] or ""
    except Exception:
        cooking_notes = ""
    return {"ingredients": [{"id": r["id"], "name": r["name"], "aisle": r["aisle"]} for r in rows],
            "cooking_notes": cooking_notes}


@router.post("/recipes/{recipe_id}/notes")
async def update_recipe_notes(recipe_id: int, body: dict, request: Request):
    """Save cooking notes for a recipe."""
    conn = _conn()
    user_id = request.state.user_id
    notes = body.get("notes", "")
    conn.execute(
        text("UPDATE recipes SET notes = :notes WHERE id = :id AND user_id = :user_id"),
        {"notes": notes, "id": recipe_id, "user_id": user_id},
    )
    conn.commit()
    return {"ok": True}


@router.post("/recipes/{recipe_id}/ingredients")
async def add_recipe_ingredient(recipe_id: int, body: dict, request: Request):
    """Add an ingredient to a recipe by name. Creates ingredient if it doesn't exist."""
    conn = _conn()
    user_id = request.state.user_id

    # Verify recipe belongs to this user
    own = conn.execute(
        text("SELECT id FROM recipes WHERE id = :id AND user_id = :user_id"),
        {"id": recipe_id, "user_id": user_id},
    ).fetchone()
    if not own:
        return {"ok": False, "error": "Recipe not found"}

    raw_name = body.get("name", "").strip()
    if not raw_name:
        return {"ok": False}

    # Normalize to canonical ingredient name
    name, matched_id = _normalize_name(conn, raw_name)

    if matched_id:
        ingredient_id = matched_id
    else:
        # Find exact or create
        row = conn.execute(
            text("SELECT id FROM ingredients WHERE LOWER(name) = :name"),
            {"name": name},
        ).fetchone()
        if row:
            ingredient_id = row["id"]
        else:
            from mealrunner.normalize import invalidate_cache
            group = _infer_item_group(conn, name, request.state.user_id)
            cursor = conn.execute(
                text("""INSERT INTO ingredients (name, aisle, default_unit)
                   VALUES (:name, :aisle, 'count')
                   RETURNING id"""),
                {"name": name, "aisle": group},
            )
            ingredient_id = cursor.fetchone()["id"]
            invalidate_cache()

    # Check if already linked
    existing = conn.execute(
        text("SELECT id FROM recipe_ingredients WHERE recipe_id = :rid AND ingredient_id = :iid"),
        {"rid": recipe_id, "iid": ingredient_id},
    ).fetchone()
    if existing:
        conn.commit()
        return {"ok": True, "exists": True}

    conn.execute(
        text("""INSERT INTO recipe_ingredients (recipe_id, ingredient_id, quantity, unit)
           VALUES (:rid, :iid, 1, 'count')"""),
        {"rid": recipe_id, "iid": ingredient_id},
    )
    conn.commit()
    result = {"ok": True, "name": name}
    if name.lower() != raw_name.lower():
        result["renamed_from"] = raw_name

    # Suggest adding as a staple if this is a known pantry-staple ingredient
    # and the user doesn't already have a staple row for it (either mode).
    staple = conn.execute(
        text("SELECT id, name FROM ingredients WHERE id = :id AND is_pantry_staple = 1"),
        {"id": ingredient_id},
    ).fetchone()
    if staple:
        already = conn.execute(
            text("SELECT id FROM staples WHERE user_id = :uid AND ingredient_id = :iid"),
            {"uid": user_id, "iid": ingredient_id},
        ).fetchone()
        if not already:
            result["suggest_staple"] = {"name": staple["name"], "ingredient_id": staple["id"]}

    return result


@router.delete("/recipes/{recipe_id}/ingredients/{ri_id}")
async def remove_recipe_ingredient(recipe_id: int, ri_id: int, request: Request):
    """Remove an ingredient from a recipe."""
    conn = _conn()
    user_id = request.state.user_id

    # Verify recipe belongs to this user
    own = conn.execute(
        text("SELECT id FROM recipes WHERE id = :id AND user_id = :user_id"),
        {"id": recipe_id, "user_id": user_id},
    ).fetchone()
    if not own:
        return {"ok": False, "error": "Recipe not found"}

    conn.execute(
        text("DELETE FROM recipe_ingredients WHERE id = :id AND recipe_id = :rid"),
        {"id": ri_id, "rid": recipe_id},
    )
    conn.commit()
    return {"ok": True}


# ── Stores ─────────────────────────────────────────────


@router.get("/stores")
async def get_stores(request: Request):
    """List configured stores."""
    from mealrunner.stores import list_stores

    user_id = request.state.user_id
    return {"stores": list_stores(_conn(), user_id)}


@router.post("/stores")
async def add_store(body: dict, request: Request):
    """Add a store."""
    from mealrunner.stores import add_store as do_add

    user_id = request.state.user_id
    name = body.get("name", "").strip()
    key = body.get("key", name[:1].lower() if name else "x")
    mode = body.get("mode", "in-person")
    api_type = body.get("api", "none")

    try:
        store = do_add(_conn(), user_id, name, key, mode, api_type)
        return {"ok": True, "store": store}
    except ValueError as e:
        logger.error("Failed to add store: %s", e)
        return {"ok": False, "error": "Failed to add store"}


@router.delete("/stores/{key}")
async def remove_store(key: str, request: Request):
    """Remove a store by key."""
    from mealrunner.stores import remove_store as do_remove

    user_id = request.state.user_id
    removed = do_remove(_conn(), user_id, key)
    return {"ok": bool(removed), "name": removed}


@router.get("/stores/nearby")
async def get_nearby(request: Request):
    """Get saved nearby/comparison stores."""
    from mealrunner.stores import get_nearby_stores

    user_id = request.state.user_id
    conn = _conn()
    stores = get_nearby_stores(conn, user_id)
    return {"stores": stores}


@router.post("/stores/nearby")
async def save_nearby(body: dict, request: Request):
    """Save user-selected nearby/comparison stores."""
    from mealrunner.stores import save_nearby_stores

    user_id = request.state.user_id
    conn = _conn()
    stores = body.get("stores", [])
    # Validate each store has required fields
    valid = [{"location_id": s["location_id"], "name": s["name"], "address": s.get("address", "")}
             for s in stores if s.get("location_id") and s.get("name")]
    count = save_nearby_stores(conn, user_id, valid)
    return {"ok": True, "count": count}


# ── Onboarding ─────────────────────────────────────────


@router.get("/onboarding/status")
async def onboarding_status(request: Request):
    """Check whether onboarding has been completed."""
    user_id = request.state.user_id
    real_user_id = getattr(request.state, 'real_user_id', user_id)
    conn = _conn()
    row = conn.execute(
        text("SELECT value FROM settings WHERE key = 'onboarding_complete' AND user_id = :user_id"),
        {"user_id": real_user_id},
    ).fetchone()
    result = {"completed": row is not None and row["value"] == "true"}
    # If this user is a household member, tell the frontend
    if real_user_id != user_id:
        owner_row = conn.execute(
            text("SELECT display_name, email FROM users WHERE id = :uid"),
            {"uid": user_id},
        ).fetchone()
        result["household_member"] = True
        result["household_owner_name"] = (owner_row["display_name"] or owner_row["email"].split("@")[0]) if owner_row else "your household"
    return result


@router.post("/onboarding/complete")
async def onboarding_complete(request: Request):
    """Mark onboarding as done."""
    real_user_id = getattr(request.state, 'real_user_id', request.state.user_id)
    conn = _conn()
    conn.execute(
        text("""INSERT INTO settings (user_id, key, value, updated_at)
           VALUES (:user_id, 'onboarding_complete', 'true', CURRENT_TIMESTAMP)
           ON CONFLICT (user_id, key) DO UPDATE SET value = 'true', updated_at = CURRENT_TIMESTAMP"""),
        {"user_id": real_user_id},
    )
    conn.commit()
    return {"ok": True}


@router.post("/meals/add-to-pool")
async def add_meal_to_pool(body: dict, request: Request):
    """Create a recipe stub (name only) for onboarding. No ingredients."""
    conn = _conn()
    user_id = request.state.user_id
    name = body.get("name", "").strip()
    if not name:
        return {"ok": False}

    # Check if recipe already exists for this user
    existing = conn.execute(
        text("SELECT id FROM recipes WHERE LOWER(name) = :name AND user_id = :user_id"),
        {"name": name.lower(), "user_id": user_id},
    ).fetchone()
    if existing:
        return {"ok": True, "id": existing["id"], "name": name}

    cursor = conn.execute(
        text("""INSERT INTO recipes (name, cuisine, effort, cleanup, outdoor, kid_friendly, premade,
           prep_minutes, cook_minutes, servings, user_id)
           VALUES (:name, '', 'medium', 'medium', 0, 1, 0, 0, 0, 4, :user_id)
           RETURNING id"""),
        {"name": name, "user_id": user_id},
    )
    conn.commit()
    return {"ok": True, "id": cursor.fetchone()["id"], "name": name}


@router.get("/onboarding/library")
async def get_onboarding_library(request: Request):
    """Return library meals and sides with ingredients for onboarding picker."""
    conn = _conn()
    meals = conn.execute(
        text("SELECT id, name FROM recipes WHERE user_id = '__library__' AND recipe_type = 'meal' ORDER BY name"),
    ).fetchall()
    sides = conn.execute(
        text("SELECT id, name FROM recipes WHERE user_id = '__library__' AND recipe_type = 'side' ORDER BY name"),
    ).fetchall()

    # Load ingredients for each recipe
    def _get_ingredients(recipe_id):
        rows = conn.execute(
            text("""SELECT i.name FROM recipe_ingredients ri
               JOIN ingredients i ON i.id = ri.ingredient_id
               WHERE ri.recipe_id = :rid ORDER BY i.name"""),
            {"rid": recipe_id},
        ).fetchall()
        return [r["name"] for r in rows]

    return {
        "meals": [{"id": r["id"], "name": r["name"], "ingredients": _get_ingredients(r["id"])} for r in meals],
        "sides": [{"id": r["id"], "name": r["name"], "ingredients": _get_ingredients(r["id"])} for r in sides],
    }


@router.get("/onboarding/staples")
async def get_onboarding_staples(request: Request):
    """Return pantry staple items grouped by aisle for onboarding checklist."""
    conn = _conn()
    rows = conn.execute(
        text("SELECT id, name, aisle FROM ingredients WHERE is_pantry_staple = 1 ORDER BY aisle, name"),
    ).fetchall()
    return {"staples": [{"id": r["id"], "name": r["name"], "aisle": r["aisle"]} for r in rows]}


@router.post("/onboarding/save-staples")
async def save_onboarding_staples(body: dict, request: Request):
    """Bulk-add staple items for the user.

    Body: {"names": [...], "mode": "every_trip" | "keep_on_hand"}.
    Defaults mode to 'keep_on_hand' to match the original onboarding
    semantic (the staples checklist was 'things you keep at home').
    """
    from mealrunner.staples import add_staple, VALID_MODES, KEEP_ON_HAND

    user_id = request.state.user_id
    conn = _conn()
    names = body.get("names", [])
    mode = body.get("mode", KEEP_ON_HAND)
    if mode not in VALID_MODES:
        return {"ok": False, "error": "invalid mode"}
    for name in names:
        name = name.strip()
        if not name:
            continue
        try:
            add_staple(conn, user_id, name, mode)
        except Exception:
            pass
    conn.commit()
    return {"ok": True, "count": len(names)}


@router.post("/onboarding/time-baseline")
async def save_time_baseline(body: dict, request: Request):
    """Save user's pre-mealrunner time estimate for value reporting."""
    user_id = request.state.user_id
    conn = _conn()
    value = body.get("value", "")
    conn.execute(
        text("""INSERT INTO settings (user_id, key, value) VALUES (:uid, 'onboarding_time_baseline', :val)
           ON CONFLICT (user_id, key) DO UPDATE SET value = :val"""),
        {"uid": user_id, "val": value},
    )
    conn.commit()
    return {"ok": True}


@router.post("/onboarding/select-recipes")
async def select_onboarding_recipes(body: dict, request: Request):
    """Copy selected library recipes to user's account and create custom stubs."""
    user_id = request.state.user_id
    conn = _conn()

    meal_ids = body.get("meal_ids", [])
    side_ids = body.get("side_ids", [])
    custom_meals = body.get("custom_meals", [])
    custom_sides = body.get("custom_sides", [])

    # Copy library recipes (deep copy: recipe + recipe_ingredients)
    for lib_id in meal_ids + side_ids:
        _copy_library_recipe(conn, lib_id, user_id)

    # Create custom meal stubs
    for name in custom_meals:
        name = name.strip()
        if not name:
            continue
        existing = conn.execute(
            text("SELECT id FROM recipes WHERE LOWER(name) = LOWER(:name) AND user_id = :uid"),
            {"name": name, "uid": user_id},
        ).fetchone()
        if not existing:
            conn.execute(text(
                """INSERT INTO recipes (name, cuisine, effort, cleanup, outdoor, kid_friendly, premade,
                   prep_minutes, cook_minutes, servings, user_id, recipe_type)
                   VALUES (:name, '', 'medium', 'medium', 0, 1, 0, 0, 0, 4, :uid, 'meal')"""
            ), {"name": name, "uid": user_id})

    # Create custom side stubs
    for name in custom_sides:
        name = name.strip()
        if not name:
            continue
        existing = conn.execute(
            text("SELECT id FROM recipes WHERE LOWER(name) = LOWER(:name) AND user_id = :uid"),
            {"name": name, "uid": user_id},
        ).fetchone()
        if not existing:
            conn.execute(text(
                """INSERT INTO recipes (name, cuisine, effort, cleanup, outdoor, kid_friendly, premade,
                   prep_minutes, cook_minutes, servings, user_id, recipe_type)
                   VALUES (:name, '', 'medium', 'medium', 0, 1, 0, 0, 0, 4, :uid, 'side')"""
            ), {"name": name, "uid": user_id})

    conn.commit()
    return {"ok": True}


def _copy_library_recipe(conn, lib_recipe_id: int, user_id: str) -> int | None:
    """Deep copy a library recipe to the user's account. Returns new recipe id."""
    lib = conn.execute(
        text("SELECT * FROM recipes WHERE id = :id AND user_id = '__library__'"),
        {"id": lib_recipe_id},
    ).fetchone()
    if not lib:
        return None

    # Check if user already has this recipe
    existing = conn.execute(
        text("SELECT id FROM recipes WHERE LOWER(name) = LOWER(:name) AND user_id = :uid"),
        {"name": lib["name"], "uid": user_id},
    ).fetchone()
    if existing:
        return existing["id"]

    result = conn.execute(text(
        """INSERT INTO recipes (name, cuisine, effort, cleanup, outdoor, kid_friendly, premade,
           prep_minutes, cook_minutes, servings, notes, user_id, recipe_type)
           VALUES (:name, :cuisine, :effort, :cleanup, :outdoor, :kid, :premade,
                    :prep, :cook, :servings, :notes, :uid, :recipe_type)
           RETURNING id"""
    ), {
        "name": lib["name"], "cuisine": lib["cuisine"], "effort": lib["effort"],
        "cleanup": lib["cleanup"], "outdoor": lib["outdoor"], "kid": lib["kid_friendly"],
        "premade": lib["premade"], "prep": lib["prep_minutes"], "cook": lib["cook_minutes"],
        "servings": lib["servings"], "notes": lib["notes"], "uid": user_id,
        "recipe_type": lib["recipe_type"],
    })
    new_id = result.fetchone()["id"]

    # Copy ingredients
    ingredients = conn.execute(
        text("SELECT * FROM recipe_ingredients WHERE recipe_id = :rid"),
        {"rid": lib_recipe_id},
    ).fetchall()
    for ing in ingredients:
        conn.execute(text(
            """INSERT INTO recipe_ingredients (recipe_id, ingredient_id, quantity, unit, prep_note, component)
               VALUES (:rid, :iid, :qty, :unit, :prep, :comp)"""
        ), {
            "rid": new_id, "iid": ing["ingredient_id"], "qty": ing["quantity"],
            "unit": ing["unit"], "prep": ing["prep_note"], "comp": ing["component"],
        })

    return new_id


# ── Meal History & Suggestions ─────────────────────────


@router.get("/meals/history")
async def get_meal_history(request: Request):
    """Get meal frequency stats from all history."""
    user_id = request.state.user_id
    conn = _conn()
    rows = conn.execute(
        text("""SELECT recipe_id, recipe_name, COUNT(*) as cook_count,
                  MAX(slot_date) as last_made
           FROM meals
           WHERE recipe_id IS NOT NULL AND user_id = :user_id
           GROUP BY recipe_id, recipe_name
           ORDER BY cook_count DESC"""),
        {"user_id": user_id},
    ).fetchall()
    return {
        "history": [
            {
                "recipe_id": r["recipe_id"],
                "recipe_name": r["recipe_name"],
                "cook_count": r["cook_count"],
                "last_made": r["last_made"],
            }
            for r in rows
        ]
    }



# ── Shopping Feedback ─────────────────────────────────────


@router.get("/feedback/patterns")
async def get_feedback_patterns(request: Request):
    """Detect shopping patterns from completed trips."""
    from mealrunner.feedback import detect_skipped_items, detect_extra_meal_links

    user_id = request.state.user_id
    conn = _conn()
    return {
        "skipped": detect_skipped_items(conn, user_id),
        "extra_links": detect_extra_meal_links(conn, user_id),
    }


@router.post("/feedback/dismiss")
async def dismiss_feedback(body: dict, request: Request):
    """Dismiss a feedback suggestion. Body: {item, meal, kind: 'skip'|'extra_link'}."""
    user_id = request.state.user_id
    item = body.get("item", "").strip().lower()
    meal = body.get("meal", "").strip().lower()
    kind = body.get("kind", "skip")
    if not item or not meal:
        return {"ok": False, "error": "item and meal required"}

    key = f"{item}::{meal}"
    conn = _conn()
    conn.execute(
        text("INSERT INTO learning_dismissed (name, kind, user_id) VALUES (:name, :kind, :user_id) ON CONFLICT DO NOTHING"),
        {"name": key, "kind": kind, "user_id": user_id},
    )
    conn.commit()
    return {"ok": True}


@router.post("/feedback/apply")
async def apply_feedback(body: dict, request: Request):
    """Apply a feedback override. Body: {item, meal, action: 'skip'|'add'}."""
    user_id = request.state.user_id
    item = body.get("item", "").strip().lower()
    meal = body.get("meal", "").strip()
    action = body.get("action", "skip")
    if not item or not meal or action not in ("skip", "add"):
        return {"ok": False, "error": "item, meal, and valid action required"}

    conn = _conn()
    conn.execute(
        text("""INSERT INTO meal_item_overrides (recipe_name, item_name, action, user_id)
           VALUES (:meal, :item, :action, :user_id)
           ON CONFLICT (recipe_name, item_name, user_id) DO UPDATE SET action = :action"""),
        {"meal": meal, "item": item, "action": action, "user_id": user_id},
    )
    conn.commit()

    # Also dismiss so it doesn't keep showing up
    kind = "skip" if action == "skip" else "extra_link"
    key = f"{item}::{meal.lower()}"
    conn.execute(
        text("INSERT INTO learning_dismissed (name, kind, user_id) VALUES (:name, :kind, :user_id) ON CONFLICT DO NOTHING"),
        {"name": key, "kind": kind, "user_id": user_id},
    )
    conn.commit()
    return {"ok": True}


@router.get("/feedback/overrides")
async def get_feedback_overrides(request: Request):
    """Get all active meal item overrides."""
    from mealrunner.feedback import get_overrides
    user_id = request.state.user_id
    conn = _conn()
    return {"overrides": get_overrides(conn, user_id)}


@router.delete("/feedback/overrides")
async def remove_feedback_override(body: dict, request: Request):
    """Remove an override. Body: {item, meal}."""
    user_id = request.state.user_id
    item = body.get("item", "").strip().lower()
    meal = body.get("meal", "").strip()
    if not item or not meal:
        return {"ok": False, "error": "item and meal required"}

    conn = _conn()
    conn.execute(
        text("DELETE FROM meal_item_overrides WHERE LOWER(recipe_name) = LOWER(:meal) AND item_name = :item AND user_id = :user_id"),
        {"meal": meal, "item": item, "user_id": user_id},
    )
    conn.commit()
    return {"ok": True}


# ── Community Data ────────────────────────────────────────


@router.post("/community-data")
async def submit_community_data(body: dict, request: Request):
    """Submit user-contributed data (brand ownership, etc.)."""
    import uuid
    data_type = body.get("data_type", "").strip()
    subject = body.get("subject", "").strip()
    suggested_value = body.get("suggested_value", "").strip()
    if not data_type or not subject or not suggested_value:
        return {"ok": False, "error": "All fields required"}

    real_user_id = request.state.real_user_id
    conn = _conn()

    # Look up household_id
    hh = conn.execute(
        text("SELECT household_id FROM household_members WHERE user_id = :uid"),
        {"uid": real_user_id},
    ).fetchone()
    household_id = hh["household_id"] if hh else ""

    conn.execute(
        text("""INSERT INTO community_data (id, user_id, household_id, data_type, subject, suggested_value)
           VALUES (:id, :uid, :hh, :dt, :subj, :val)"""),
        {"id": str(uuid.uuid4()), "uid": real_user_id, "hh": household_id,
         "dt": data_type, "subj": subject, "val": suggested_value},
    )
    conn.commit()
    return {"ok": True}


# ── Household ─────────────────────────────────────────────


@router.get("/household/members")
async def get_household_members(request: Request):
    """List members of the current user's household."""
    from mealrunner.web.auth import get_household_id

    real_user_id = getattr(request.state, 'real_user_id', request.state.user_id)
    conn = _conn()
    hh_id = get_household_id(conn, real_user_id)
    if not hh_id:
        return {"members": [], "household_id": None}

    rows = conn.execute(
        text("""SELECT hm.user_id, hm.role, u.email, u.display_name
               FROM household_members hm
               JOIN users u ON u.id = hm.user_id
               WHERE hm.household_id = :hh_id
               ORDER BY hm.role DESC, hm.joined_at"""),
        {"hh_id": hh_id},
    ).fetchall()

    return {
        "household_id": hh_id,
        "members": [
            {
                "user_id": r["user_id"],
                "email": r["email"],
                "display_name": r["display_name"] or r["email"].split("@")[0],
                "role": r["role"],
                "is_you": r["user_id"] == real_user_id,
            }
            for r in rows
        ],
    }


@router.post("/household/invite")
async def invite_to_household(body: dict, request: Request):
    """Invite someone to share your household."""
    from mealrunner.web.auth import get_household_id, send_magic_link_email, find_or_create_user, create_magic_link

    real_user_id = getattr(request.state, 'real_user_id', request.state.user_id)

    # Rate limit: max 5 invites per user per hour
    throttled = _check_throttle(real_user_id, "household_invite", 5, 3600)
    if throttled:
        return throttled

    email = body.get("email", "").strip().lower()
    if not email:
        return {"ok": False, "error": "Email required"}

    conn = _conn()
    hh_id = get_household_id(conn, real_user_id)
    if not hh_id:
        return {"ok": False, "error": "No household found"}

    # Check if already a member
    existing = conn.execute(
        text("""SELECT 1 FROM household_members hm
               JOIN users u ON u.id = hm.user_id
               WHERE hm.household_id = :hh_id AND LOWER(u.email) = :email"""),
        {"hh_id": hh_id, "email": email},
    ).fetchone()
    if existing:
        return {"ok": False, "error": "Already a household member"}

    # Create invite record
    conn.execute(
        text("""INSERT INTO household_invites (household_id, email, invited_by, status)
           VALUES (:hh_id, :email, :user_id, 'pending')"""),
        {"hh_id": hh_id, "email": email, "user_id": real_user_id},
    )

    # Add to allowed_emails so they can sign up
    conn.execute(
        text("INSERT INTO allowed_emails (email) VALUES (:email) ON CONFLICT DO NOTHING"),
        {"email": email},
    )

    # Create user + send magic link
    user_id = find_or_create_user(conn, email)
    token = create_magic_link(conn, user_id)
    # Offload Resend HTTP so a slow email provider doesn't block other requests.
    import anyio
    await anyio.to_thread.run_sync(send_magic_link_email, email, token)

    conn.commit()
    return {"ok": True}


@router.post("/beta/invite")
async def invite_to_beta(body: dict, request: Request):
    """Invite someone to try mealrunner (separate account, no household sharing)."""
    from mealrunner.web.auth import find_or_create_user, create_magic_link, send_magic_link_email

    # Rate limit: max 5 invites per user per hour
    user_id = request.state.user_id
    throttled = _check_throttle(user_id, "beta_invite", 5, 3600)
    if throttled:
        return throttled

    email = body.get("email", "").strip().lower()
    if not email:
        return {"ok": False, "error": "Email required"}

    conn = _conn()

    # Add to allowed_emails
    conn.execute(
        text("INSERT INTO allowed_emails (email) VALUES (:email) ON CONFLICT DO NOTHING"),
        {"email": email},
    )

    # Create user + send magic link
    user_id = find_or_create_user(conn, email)
    token = create_magic_link(conn, user_id)
    # Offload Resend HTTP so a slow email provider doesn't block other requests.
    import anyio
    await anyio.to_thread.run_sync(send_magic_link_email, email, token)

    conn.commit()
    return {"ok": True}


@router.get("/household/pending-invite")
async def get_pending_invite(request: Request):
    """Check if the current user has a pending household invite."""
    real_user_id = getattr(request.state, 'real_user_id', request.state.user_id)
    conn = _conn()

    # Get this user's email
    user = conn.execute(
        text("SELECT email FROM users WHERE id = :id"),
        {"id": real_user_id},
    ).fetchone()
    if not user:
        return {"invite": None}

    # Find pending invite
    invite = conn.execute(
        text("""SELECT hi.household_id, hi.invited_by, u.display_name, u.email AS inviter_email
               FROM household_invites hi
               JOIN users u ON u.id = hi.invited_by
               WHERE LOWER(hi.email) = LOWER(:email) AND hi.status = 'pending'
               ORDER BY hi.created_at DESC LIMIT 1"""),
        {"email": user["email"]},
    ).fetchone()
    if not invite:
        return {"invite": None}

    inviter_name = invite["display_name"] or invite["inviter_email"].split("@")[0]
    return {
        "invite": {
            "household_id": invite["household_id"],
            "inviter_name": inviter_name,
        }
    }


@router.post("/household/accept-invite")
async def accept_invite(request: Request):
    """Accept a pending household invite."""
    from mealrunner.web.app import _process_household_invite

    real_user_id = getattr(request.state, 'real_user_id', request.state.user_id)
    conn = _conn()

    user = conn.execute(
        text("SELECT email FROM users WHERE id = :id"),
        {"id": real_user_id},
    ).fetchone()
    if not user:
        return {"ok": False}

    _process_household_invite(conn, real_user_id, user["email"])
    return {"ok": True}


@router.post("/household/decline-invite")
async def decline_invite(request: Request):
    """Decline a pending household invite."""
    real_user_id = getattr(request.state, 'real_user_id', request.state.user_id)
    conn = _conn()

    user = conn.execute(
        text("SELECT email FROM users WHERE id = :id"),
        {"id": real_user_id},
    ).fetchone()
    if not user:
        return {"ok": False}

    conn.execute(
        text("""UPDATE household_invites SET status = 'declined'
               WHERE LOWER(email) = LOWER(:email) AND status = 'pending'"""),
        {"email": user["email"]},
    )
    conn.commit()
    return {"ok": True}


@router.delete("/household/members/{member_user_id}")
async def remove_household_member(member_user_id: str, request: Request):
    """Owner-only: remove a member from the household. Severs the share but
    does not delete the member's account — they can still log in and will see
    a fresh, empty workspace."""
    from mealrunner.web.auth import get_household_id

    real_user_id = getattr(request.state, 'real_user_id', request.state.user_id)
    conn = _conn()

    hh_id = get_household_id(conn, real_user_id)
    if not hh_id:
        return {"ok": False, "error": "No household"}

    caller = conn.execute(
        text("SELECT role FROM household_members WHERE household_id = :hh_id AND user_id = :uid"),
        {"hh_id": hh_id, "uid": real_user_id},
    ).fetchone()
    if not caller or caller["role"] != "owner":
        return JSONResponse({"ok": False, "error": "Only the owner can remove members"}, status_code=403)

    if member_user_id == real_user_id:
        return {"ok": False, "error": "Owner cannot remove self"}

    target = conn.execute(
        text("SELECT role FROM household_members WHERE household_id = :hh_id AND user_id = :uid"),
        {"hh_id": hh_id, "uid": member_user_id},
    ).fetchone()
    if not target:
        return {"ok": False, "error": "Member not in this household"}
    if target["role"] == "owner":
        return {"ok": False, "error": "Cannot remove the owner"}

    conn.execute(
        text("DELETE FROM household_members WHERE household_id = :hh_id AND user_id = :uid"),
        {"hh_id": hh_id, "uid": member_user_id},
    )
    conn.commit()
    return {"ok": True}


# ── Account ──────────────────────────────────────────────


@router.post("/account/update")
async def update_account(body: dict, request: Request):
    """Update current user's profile (first_name, last_name, display_name)."""
    real_user_id = getattr(request.state, 'real_user_id', request.state.user_id)
    conn = _conn()

    first_name = body.get("first_name")
    last_name = body.get("last_name")
    display_name = body.get("display_name")

    # If first/last provided, auto-generate display_name
    if first_name is not None or last_name is not None:
        fn = (first_name or "").strip()
        ln = (last_name or "").strip()
        conn.execute(
            text("UPDATE users SET first_name = :fn, last_name = :ln, display_name = :dn WHERE id = :id"),
            {"fn": fn, "ln": ln, "dn": f"{fn} {ln}".strip(), "id": real_user_id},
        )
        conn.commit()
    elif display_name is not None:
        display_name = display_name.strip() or None
        conn.execute(
            text("UPDATE users SET display_name = :name WHERE id = :id"),
            {"name": display_name, "id": real_user_id},
        )
        conn.commit()

    user = conn.execute(
        text("SELECT id, email, display_name, first_name, last_name FROM users WHERE id = :id"),
        {"id": real_user_id},
    ).fetchone()
    return {"ok": True, "email": user["email"], "display_name": user["display_name"],
            "first_name": user["first_name"], "last_name": user["last_name"]}


@router.post("/account/accept-tos")
async def accept_tos(body: dict, request: Request):
    """Record TOS acceptance with version and timestamp."""
    real_user_id = getattr(request.state, 'real_user_id', request.state.user_id)
    conn = _conn()
    version = body.get("version", "1.0")
    conn.execute(
        text("UPDATE users SET tos_accepted_at = CURRENT_TIMESTAMP, tos_version = :v WHERE id = :id"),
        {"v": version, "id": real_user_id},
    )
    conn.commit()
    return {"ok": True}


# ── Price Tracking Settings ───────────────────────────────


@router.get("/settings/price-tracking")
async def get_price_tracking(request: Request):
    """Get price tracking preferences."""
    user_id = request.state.user_id
    conn = _conn()
    rows = conn.execute(
        text("SELECT key, value FROM settings WHERE user_id = :uid AND key IN ('price_polling', 'price_sharing')"),
        {"uid": user_id},
    ).fetchall()
    prefs = {r["key"]: r["value"] == "1" for r in rows}
    return {
        "price_polling": prefs.get("price_polling", False),
        "price_sharing": prefs.get("price_sharing", False),
    }


@router.post("/settings/price-tracking")
async def set_price_tracking(body: dict, request: Request):
    """Update price tracking preferences."""
    user_id = request.state.user_id
    conn = _conn()
    for key in ("price_polling", "price_sharing"):
        if key in body:
            val = "1" if body[key] else "0"
            conn.execute(
                text("""INSERT INTO settings (user_id, key, value) VALUES (:uid, :key, :val)
                   ON CONFLICT (user_id, key) DO UPDATE SET value = :val, updated_at = CURRENT_TIMESTAMP"""),
                {"uid": user_id, "key": key, "val": val},
            )
    conn.commit()
    return {"ok": True}


@router.get("/price-tracking/best-day")
async def best_day_of_week(request: Request, scope: str = "trip"):
    """Return day-of-week price patterns for the user's basket.

    scope='trip' uses items currently on the active trip with a product UPC.
    scope='usuals' uses items the user has purchased (receipt-matched) in the last 12 weeks.

    Prices are drawn from poll-source rows only (the systematic background poll),
    so the day-of-week signal isn't contaminated by user-driven samples that cluster
    on the days the app happens to get used. Returns thin=True when there's too little
    data, flat=True when there's enough but no meaningful day-to-day spread (<1pp).
    """
    user_id = request.state.user_id
    conn = _conn()

    if scope == "usuals":
        upc_rows = conn.execute(
            text("""SELECT DISTINCT ti.receipt_upc AS upc
                    FROM grocery_items ti
                    WHERE ti.user_id = :uid
                      AND ti.receipt_status IN ('matched', 'substituted')
                      AND ti.receipt_upc != ''
                      AND ti.checked_at IS NOT NULL
                      AND ti.checked_at > NOW() - INTERVAL '84 days'"""),
            {"uid": user_id},
        ).fetchall()
    else:
        scope = "trip"
        upc_rows = conn.execute(
            text("""SELECT DISTINCT ti.product_upc AS upc
                    FROM grocery_items ti
                    WHERE ti.user_id = :uid
                      AND ti.product_upc != ''"""),
            {"uid": user_id},
        ).fetchall()

    upcs = [r["upc"] for r in upc_rows if r["upc"]]
    if not upcs:
        return {"scope": scope, "best_day": None, "by_day": [], "total_samples": 0,
                "thin": True, "basket_size": 0}

    placeholders = ",".join(f":u{i}" for i in range(len(upcs)))
    params = {f"u{i}": u for i, u in enumerate(upcs)}

    # For each (upc, dow), compute average price; then express each as % of that UPC's
    # overall mean to normalize across cheap/expensive items; then average across UPCs per dow.
    # POLL-ONLY: restrict to the systematic background poll. The other sources
    # (search/select/receipt/nearby) only fire when the user uses the app, so they
    # cluster on app-usage weekdays and inject a collection-timing artifact, not a
    # real price pattern. The poll runs server-side on a timer regardless of usage.
    rows = conn.execute(
        text(f"""WITH per_upc_dow AS (
                    SELECT upc, EXTRACT(DOW FROM fetched_at)::int AS dow,
                           AVG(price) AS avg_price, COUNT(*) AS n
                    FROM product_prices
                    WHERE upc IN ({placeholders}) AND price IS NOT NULL AND price > 0
                          AND source = 'poll'
                    GROUP BY upc, dow
                 ),
                 per_upc_mean AS (
                    SELECT upc, AVG(avg_price) AS mean FROM per_upc_dow GROUP BY upc
                 )
                 SELECT pud.dow,
                        AVG((pud.avg_price - pum.mean) / pum.mean * 100.0) AS pct_vs_mean,
                        SUM(pud.n) AS samples
                 FROM per_upc_dow pud
                 JOIN per_upc_mean pum ON pum.upc = pud.upc
                 WHERE pum.mean > 0
                 GROUP BY pud.dow
                 ORDER BY pud.dow"""),
        params,
    ).fetchall()

    by_day = [
        {"dow": r["dow"],
         "pct_vs_mean": float(r["pct_vs_mean"]) if r["pct_vs_mean"] is not None else 0.0,
         "samples": int(r["samples"])}
        for r in rows
    ]
    best = min(by_day, key=lambda d: d["pct_vs_mean"]) if by_day else None
    total_samples = sum(d["samples"] for d in by_day)
    spread = (max(d["pct_vs_mean"] for d in by_day)
              - min(d["pct_vs_mean"] for d in by_day)) if by_day else 0.0
    # Honesty gate: even with plenty of samples, a sub-1pp spread between the
    # cheapest and priciest day is noise, not a day worth planning around. Report
    # it as "flat" (prices hold steady across the week) rather than crowning a day.
    thin = total_samples < 20 or len(by_day) < 4
    MEANINGFUL_SPREAD = 1.0  # percentage points
    flat = (not thin) and spread < MEANINGFUL_SPREAD
    return {
        "scope": scope,
        "basket_size": len(upcs),
        "by_day": by_day,
        "best_day": best,
        "total_samples": total_samples,
        "spread": round(spread, 2),
        "thin": thin,
        "flat": flat,
    }


@router.get("/price-tracking/basket-trend")
async def basket_trend(request: Request):
    """Weekly basket totals over the last ~6 months.

    Sums BOTH matched/substituted trip items (using receipt_price, which is
    the line total — no quantity multiplication) AND unmatched receipt extras
    (receipt_extra_items.price). Both are real money on the receipt.
    """
    user_id = request.state.user_id
    conn = _conn()

    rows = conn.execute(
        text("""WITH matched AS (
                  SELECT date_trunc('week', ti.checked_at)::date AS week,
                         ti.receipt_price AS line_total
                  FROM grocery_items ti
                  WHERE ti.user_id = :uid
                    AND ti.receipt_status IN ('matched', 'substituted')
                    AND ti.receipt_price IS NOT NULL
                    AND ti.checked_at IS NOT NULL
                    AND ti.checked_at > NOW() - INTERVAL '180 days'
                ),
                extras AS (
                  SELECT date_trunc('week', re.created_at)::date AS week,
                         re.price AS line_total
                  FROM receipt_extra_items re
                  WHERE re.user_id = :uid
                    AND re.price IS NOT NULL
                    AND re.dismissed = 0
                    AND re.created_at > NOW() - INTERVAL '180 days'
                )
                SELECT week, SUM(line_total) AS total, COUNT(*) AS items
                FROM (SELECT * FROM matched UNION ALL SELECT * FROM extras) combined
                GROUP BY week
                ORDER BY week"""),
        {"uid": user_id},
    ).fetchall()

    all_weeks = [{"week": r["week"].isoformat(),
                  "total": round(float(r["total"]), 2),
                  "items": int(r["items"])}
                 for r in rows if r["total"] is not None]

    # A "real" shopping week has enough captured purchases to represent a full
    # trip. Weeks below this threshold are almost always partial data (old
    # receipts that were only partially matched, or mid-week stop-ins) and
    # drag the average down misleadingly.
    MIN_ITEMS = 10
    MIN_TOTAL = 50.0
    real_weeks = [w for w in all_weeks if w["items"] >= MIN_ITEMS or w["total"] >= MIN_TOTAL]

    pct_change = None
    if len(real_weeks) >= 2:
        first, last = real_weeks[0]["total"], real_weeks[-1]["total"]
        if first > 0:
            pct_change = round((last - first) / first * 100.0, 1)

    avg = round(sum(w["total"] for w in real_weeks) / len(real_weeks), 2) if real_weeks else 0

    return {
        "weeks": real_weeks,
        "average_weekly": avg,
        "pct_change_first_to_last": pct_change,
        "weeks_of_data": len(real_weeks),
        "weeks_excluded_thin": len(all_weeks) - len(real_weeks),
        "thin": len(real_weeks) < 4,
    }


@router.post("/settings/home-zip")
async def set_home_zip(body: dict, request: Request):
    """Save the user's home zip code."""
    user_id = request.state.user_id
    zip_code = body.get("zip", "").strip()
    if not zip_code:
        return {"ok": False, "error": "zip required"}
    conn = _conn()
    conn.execute(
        text("""INSERT INTO settings (user_id, key, value) VALUES (:uid, 'home_zip', :val)
           ON CONFLICT (user_id, key) DO UPDATE SET value = :val, updated_at = CURRENT_TIMESTAMP"""),
        {"uid": user_id, "val": zip_code},
    )
    conn.commit()
    return {"ok": True}


# ── Feedback ──────────────────────────────────────────────


@router.post("/feedback")
async def submit_feedback(body: dict, request: Request):
    """Save user feedback."""
    real_user_id = getattr(request.state, 'real_user_id', request.state.user_id)
    message = body.get("message", "").strip()
    page = body.get("page", "")
    if not message:
        return {"ok": False, "error": "Message required"}

    conn = _conn()
    conn.execute(
        text("""INSERT INTO user_feedback (user_id, message, page)
           VALUES (:user_id, :message, :page)"""),
        {"user_id": real_user_id, "message": message, "page": page},
    )
    conn.commit()
    return {"ok": True}


@router.get("/feedback/responses")
async def get_feedback_responses(request: Request):
    """Return unread feedback responses for the current user."""
    real_user_id = getattr(request.state, 'real_user_id', request.state.user_id)
    conn = _conn()
    rows = conn.execute(
        text("""SELECT id, message, response, responded_at
           FROM user_feedback
           WHERE user_id = :user_id AND status = 'responded' AND dismissed = 0
           ORDER BY responded_at DESC"""),
        {"user_id": real_user_id},
    ).fetchall()
    return {"responses": [{"id": r["id"], "message": r["message"],
                           "response": r["response"], "responded_at": r["responded_at"]} for r in rows]}


@router.post("/feedback/{feedback_id}/dismiss")
async def dismiss_feedback_response(feedback_id: int, request: Request):
    """Mark a feedback response as seen."""
    real_user_id = getattr(request.state, 'real_user_id', request.state.user_id)
    conn = _conn()
    conn.execute(
        text("UPDATE user_feedback SET dismissed = 1 WHERE id = :id AND user_id = :user_id"),
        {"id": feedback_id, "user_id": real_user_id},
    )
    conn.commit()
    return {"ok": True}


def _is_admin(conn, user_id: str) -> bool:
    """Admin = first registered user (household owner). Good enough for beta."""
    import os
    admin_id = os.environ.get("ADMIN_USER_ID")
    if admin_id:
        return user_id == admin_id
    row = conn.execute(text("SELECT id FROM users ORDER BY created_at LIMIT 1")).fetchone()
    return row and row["id"] == user_id


@router.get("/feedback/all")
async def get_all_feedback(request: Request):
    """Admin: list all feedback."""
    real_user_id = getattr(request.state, 'real_user_id', request.state.user_id)
    conn = _conn()
    if not _is_admin(conn, real_user_id):
        return {"ok": False, "error": "Not authorized"}
    rows = conn.execute(
        text("SELECT f.*, u.email FROM user_feedback f JOIN users u ON u.id = f.user_id ORDER BY f.created_at DESC"),
    ).fetchall()
    return {"feedback": [dict(r) for r in rows]}


@router.get("/admin/metrics")
async def get_admin_metrics(request: Request):
    """Admin: high-level usage metrics for the beta dashboard.

    Plain SELECTs on the request connection (same as /feedback/all). The request
    connection's fetchone() returns a RowMapping (dict-like, keyed by column NAME),
    so positional row[0] raises KeyError — read the single value via .values()
    instead. An earlier savepoint wrapper's broad except was silently swallowing
    that KeyError and zeroing every metric.
    """
    real_user_id = getattr(request.state, 'real_user_id', request.state.user_id)
    conn = _conn()
    if not _is_admin(conn, real_user_id):
        return {"ok": False, "error": "Not authorized"}

    def scalar(sql: str) -> int:
        row = conn.execute(text(sql)).fetchone()
        if not row:
            return 0
        val = next(iter(row.values()), None)
        return int(val) if val is not None else 0

    # NOTE on "active": last_login is a poor proxy because household members log
    # in once and stay signed in, so they never refresh it. A live (unexpired)
    # session is the truthful "currently has access / using it" signal and counts
    # household members. Engagement counts (meals/grocery/receipts) are aggregate,
    # not per-user, because a household member's activity is written under the
    # household owner's user_id — so distinct-user counts would undercount.
    metrics = {
        "users_total": scalar("SELECT COUNT(*) FROM users"),
        "users_new_7d": scalar("SELECT COUNT(*) FROM users WHERE created_at > NOW() - INTERVAL '7 days'"),
        "active_signed_in": scalar("SELECT COUNT(DISTINCT user_id) FROM sessions WHERE expires_at > NOW()"),
        "pending_activation": scalar("SELECT COUNT(*) FROM users WHERE last_login IS NULL"),
        "households": scalar("SELECT COUNT(DISTINCT household_id) FROM household_members"),
        "kroger_linked": scalar("SELECT COUNT(DISTINCT user_id) FROM user_kroger_tokens"),
        "meals_planned_7d": scalar("SELECT COUNT(*) FROM meals WHERE created_at > NOW() - INTERVAL '7 days'"),
        "grocery_items_7d": scalar("SELECT COUNT(*) FROM grocery_items WHERE added_at > NOW() - INTERVAL '7 days'"),
        "receipts_7d": scalar("SELECT COUNT(*) FROM grocery_state WHERE receipt_parsed_at > NOW() - INTERVAL '7 days'"),
        "invites_sent": scalar("SELECT COUNT(*) FROM household_invites"),
        "invites_accepted": scalar("SELECT COUNT(*) FROM household_invites WHERE status = 'accepted'"),
        "open_feedback": scalar("SELECT COUNT(*) FROM user_feedback WHERE COALESCE(dismissed, 0) = 0 AND status NOT IN ('responded', 'resolved', 'dismissed')"),
        "waitlist": scalar("SELECT COUNT(*) FROM waitlist"),
        "tip_subscribers": scalar("SELECT COUNT(*) FROM users WHERE active_tip_subscription_id IS NOT NULL"),
        "tips_total": scalar("SELECT COUNT(*) FROM tips WHERE status = 'succeeded'"),
        "tips_cents": scalar("SELECT COALESCE(SUM(amount_cents), 0) FROM tips WHERE status = 'succeeded'"),
    }
    return {"ok": True, "metrics": metrics}


@router.get("/admin/detail/{key}")
async def get_admin_detail(key: str, request: Request):
    """Admin: drill-down lists behind the dashboard cards (people-oriented)."""
    real_user_id = getattr(request.state, 'real_user_id', request.state.user_id)
    conn = _conn()
    if not _is_admin(conn, real_user_id):
        return {"ok": False, "error": "Not authorized"}

    def rows_of(sql: str) -> list[dict]:
        return [dict(r) for r in conn.execute(text(sql)).fetchall()]

    if key == "users":
        admin_id = _admin_user_id(conn)
        rows = rows_of("""
            SELECT u.id, u.email, u.created_at, u.last_login,
                   EXISTS(SELECT 1 FROM sessions s WHERE s.user_id = u.id AND s.expires_at > NOW()) AS active,
                   hm.role AS household_role
            FROM users u
            LEFT JOIN household_members hm ON hm.user_id = u.id
            ORDER BY u.created_at
        """)
        for r in rows:
            r["protected"] = r["id"] in (admin_id, real_user_id)  # owner/self get no revoke/delete buttons
            r.pop("id", None)
        return {"ok": True, "rows": rows}

    if key == "waitlist":
        return {"ok": True, "rows": rows_of(
            "SELECT email, requested_at FROM waitlist ORDER BY requested_at DESC")}

    if key == "invites":
        return {"ok": True, "rows": rows_of("""
            SELECT hi.id, hi.email, hi.status, hi.created_at, u.email AS invited_by
            FROM household_invites hi
            LEFT JOIN users u ON u.id = hi.invited_by
            ORDER BY hi.created_at DESC
        """)}

    if key == "kroger":
        return {"ok": True, "rows": rows_of("""
            SELECT DISTINCT u.email
            FROM user_kroger_tokens t JOIN users u ON u.id = t.user_id
            ORDER BY u.email
        """)}

    if key == "tips":
        return {"ok": True, "rows": rows_of("""
            SELECT u.email, t.amount_cents, t.mode, t.created_at
            FROM tips t JOIN users u ON u.id = t.user_id
            WHERE t.status = 'succeeded'
            ORDER BY t.created_at DESC
        """)}

    if key == "households":
        flat = rows_of("""
            SELECT hm.household_id, hm.role, u.email
            FROM household_members hm JOIN users u ON u.id = hm.user_id
            ORDER BY hm.household_id, (hm.role = 'owner') DESC, u.email
        """)
        groups: dict = {}
        for r in flat:
            g = groups.setdefault(r["household_id"], {
                "household_id": r["household_id"], "owner_email": None, "members": []})
            g["members"].append({"email": r["email"], "role": r["role"]})
            if r["role"] == "owner":
                g["owner_email"] = r["email"]
        return {"ok": True, "rows": list(groups.values())}

    return {"ok": False, "error": f"Unknown detail key: {key}"}


def _admin_user_id(conn) -> str | None:
    """The user_id that resolves as admin/owner — mirrors _is_admin."""
    import os
    aid = os.environ.get("ADMIN_USER_ID")
    if aid:
        return aid
    row = conn.execute(text("SELECT id FROM users ORDER BY created_at LIMIT 1")).fetchone()
    return row["id"] if row else None


def _resolve_user_id(conn, email: str) -> str | None:
    row = conn.execute(
        text("SELECT id FROM users WHERE LOWER(email) = LOWER(:e)"), {"e": email}
    ).fetchone()
    return row["id"] if row else None


# All tables holding per-user rows, child-first so the users row deletes last
# without tripping a foreign key. Keyed by :uid except the email-keyed pair.
_USER_DELETE_SQL = [
    "DELETE FROM sessions WHERE user_id = :uid",
    "DELETE FROM magic_links WHERE user_id = :uid",
    "DELETE FROM grocery_items WHERE user_id = :uid",
    "DELETE FROM grocery_state WHERE user_id = :uid",
    "DELETE FROM meals WHERE user_id = :uid",
    "DELETE FROM household_members WHERE user_id = :uid",
    "DELETE FROM household_invites WHERE invited_by = :uid OR LOWER(email) = LOWER(:email)",
    "DELETE FROM user_feedback WHERE user_id = :uid",
    "DELETE FROM user_kroger_tokens WHERE user_id = :uid",
    "DELETE FROM community_data WHERE user_id = :uid",
    "DELETE FROM receipt_extra_items WHERE user_id = :uid",
    "DELETE FROM tips WHERE user_id = :uid",
    "DELETE FROM product_preferences WHERE user_id = :uid",
    "DELETE FROM product_ratings WHERE user_id = :uid",
    "DELETE FROM staples WHERE user_id = :uid",
    "DELETE FROM learning_dismissed WHERE user_id = :uid",
    "DELETE FROM meal_item_overrides WHERE user_id = :uid",
    "DELETE FROM user_item_groups WHERE user_id = :uid",
    "DELETE FROM stores WHERE user_id = :uid",
    "DELETE FROM nearby_stores WHERE user_id = :uid",
    "DELETE FROM settings WHERE user_id = :uid",
    "DELETE FROM recipes WHERE user_id = :uid",
    "DELETE FROM allowed_emails WHERE LOWER(email) = LOWER(:email)",
    "DELETE FROM waitlist WHERE LOWER(email) = LOWER(:email)",
    "DELETE FROM users WHERE id = :uid",
]


@router.post("/admin/waitlist/approve")
async def admin_waitlist_approve(body: dict, request: Request):
    """Admin: approve a waitlisted email — allowlist it, send a magic link, clear it from the waitlist."""
    from mealrunner.web.auth import find_or_create_user, create_magic_link, send_magic_link_email
    real_user_id = getattr(request.state, 'real_user_id', request.state.user_id)
    conn = _conn()
    if not _is_admin(conn, real_user_id):
        return {"ok": False, "error": "Not authorized"}
    email = (body.get("email") or "").strip().lower()
    if not email:
        return {"ok": False, "error": "Email required"}
    conn.execute(text("INSERT INTO allowed_emails (email) VALUES (:e) ON CONFLICT DO NOTHING"), {"e": email})
    conn.execute(text("DELETE FROM waitlist WHERE LOWER(email) = LOWER(:e)"), {"e": email})
    user_id = find_or_create_user(conn, email)
    token = create_magic_link(conn, user_id)
    conn.commit()
    # Offload Resend HTTP so a slow email provider doesn't block other requests.
    import anyio
    try:
        await anyio.to_thread.run_sync(send_magic_link_email, email, token)
    except Exception:
        pass  # approval already persisted; a send hiccup shouldn't roll it back
    return {"ok": True}


@router.post("/admin/waitlist/dismiss")
async def admin_waitlist_dismiss(body: dict, request: Request):
    """Admin: remove an email from the waitlist without approving."""
    real_user_id = getattr(request.state, 'real_user_id', request.state.user_id)
    conn = _conn()
    if not _is_admin(conn, real_user_id):
        return {"ok": False, "error": "Not authorized"}
    email = (body.get("email") or "").strip().lower()
    if not email:
        return {"ok": False, "error": "Email required"}
    conn.execute(text("DELETE FROM waitlist WHERE LOWER(email) = LOWER(:e)"), {"e": email})
    conn.commit()
    return {"ok": True}


@router.post("/admin/invite/cancel")
async def admin_invite_cancel(body: dict, request: Request):
    """Admin: delete a still-pending household invite by id."""
    real_user_id = getattr(request.state, 'real_user_id', request.state.user_id)
    conn = _conn()
    if not _is_admin(conn, real_user_id):
        return {"ok": False, "error": "Not authorized"}
    invite_id = body.get("id")
    if not invite_id:
        return {"ok": False, "error": "Invite id required"}
    conn.execute(text("DELETE FROM household_invites WHERE id = :id AND status = 'pending'"), {"id": invite_id})
    conn.commit()
    return {"ok": True}


@router.post("/admin/user/revoke")
async def admin_user_revoke(body: dict, request: Request):
    """Admin: revoke access (soft) — drop from allowlist + force logout. Data kept; reversible by re-approving."""
    real_user_id = getattr(request.state, 'real_user_id', request.state.user_id)
    conn = _conn()
    if not _is_admin(conn, real_user_id):
        return {"ok": False, "error": "Not authorized"}
    email = (body.get("email") or "").strip().lower()
    if not email:
        return {"ok": False, "error": "Email required"}
    target_id = _resolve_user_id(conn, email)
    if not target_id:
        return {"ok": False, "error": "User not found"}
    if target_id in (real_user_id, _admin_user_id(conn)):
        return {"ok": False, "error": "Cannot revoke the owner account"}
    conn.execute(text("DELETE FROM allowed_emails WHERE LOWER(email) = LOWER(:e)"), {"e": email})
    conn.execute(text("DELETE FROM sessions WHERE user_id = :uid"), {"uid": target_id})
    conn.commit()
    return {"ok": True}


@router.post("/admin/user/delete")
async def admin_user_delete(body: dict, request: Request):
    """Admin: hard-delete an account and all its data. Irreversible. Owner/self protected."""
    real_user_id = getattr(request.state, 'real_user_id', request.state.user_id)
    conn = _conn()
    if not _is_admin(conn, real_user_id):
        return {"ok": False, "error": "Not authorized"}
    email = (body.get("email") or "").strip().lower()
    if not email:
        return {"ok": False, "error": "Email required"}
    target_id = _resolve_user_id(conn, email)
    if not target_id:
        return {"ok": False, "error": "User not found"}
    if target_id in (real_user_id, _admin_user_id(conn)):
        return {"ok": False, "error": "Cannot delete the owner account"}
    for sql in _USER_DELETE_SQL:
        conn.execute(text(sql), {"uid": target_id, "email": email})
    conn.commit()
    return {"ok": True}


@router.post("/account/delete")
async def delete_own_account(request: Request):
    """Self-serve account deletion — any signed-in user wipes their own account
    and data, then is logged out. Not admin-gated. The app owner is blocked
    (deleting the founding account would orphan the app and every household)."""
    real_user_id = getattr(request.state, 'real_user_id', request.state.user_id)
    conn = _conn()
    row = conn.execute(text("SELECT email FROM users WHERE id = :id"), {"id": real_user_id}).fetchone()
    if not row:
        return {"ok": False, "error": "User not found"}
    if real_user_id == _admin_user_id(conn):
        return {"ok": False, "error": "The owner account can't be self-deleted. Contact support."}
    email = row["email"]
    for sql in _USER_DELETE_SQL:
        conn.execute(text(sql), {"uid": real_user_id, "email": email})
    conn.commit()
    return {"ok": True}


@router.get("/admin/unknown-brands")
async def get_unknown_brands(request: Request):
    """Admin: list unknown brands sorted by frequency."""
    real_user_id = getattr(request.state, 'real_user_id', request.state.user_id)
    conn = _conn()
    if not _is_admin(conn, real_user_id):
        return {"ok": False, "error": "Not authorized"}
    rows = conn.execute(
        text("SELECT brand, times_seen, first_seen, last_seen FROM unknown_brands ORDER BY times_seen DESC"),
    ).fetchall()
    return {"brands": [dict(r) for r in rows]}


@router.post("/admin/refresh-violations")
async def refresh_violations(request: Request):
    """Admin: refresh FDA violation data for all parent companies."""
    from mealrunner.violations import refresh_fda_data

    real_user_id = getattr(request.state, 'real_user_id', request.state.user_id)
    conn = _conn()
    if not _is_admin(conn, real_user_id):
        return {"ok": False, "error": "Not authorized"}
    result = refresh_fda_data(conn)
    return {"ok": True, **result}


@router.post("/admin/e2e-cleanup")
async def e2e_cleanup(body: dict):
    """Playwright test cleanup. Deletes all e2e-*@mealrunner-test.invalid users
    and their data. Only active when PLAYWRIGHT_TEST_SECRET is set.

    Each DELETE runs in its own savepoint so a missing table or schema
    mismatch can't poison the whole transaction. Errors are returned in the
    response body (this is a test-only endpoint; info disclosure is fine).
    """
    from mealrunner.web.auth import e2e_enabled, verify_e2e_secret, E2E_EMAIL_DOMAIN

    if not e2e_enabled():
        return JSONResponse({"error": "Not found"}, status_code=404)
    if not verify_e2e_secret(body.get("secret", "")):
        return JSONResponse({"error": "Invalid secret"}, status_code=401)

    conn = _conn()
    pattern = f"e2e-%{E2E_EMAIL_DOMAIN}"
    errors: list[str] = []

    def safe_exec(sql: str, params: dict, label: str) -> None:
        """Run a statement inside a savepoint so a failure doesn't abort the
        surrounding transaction. Records the error for the response body.

        Uses conn.raw.begin_nested() because DictConnection is a thin wrapper
        that only forwards execute/commit/begin/close.
        """
        sp = None
        try:
            sp = conn.raw.begin_nested()
            conn.execute(text(sql), params)
            sp.commit()
        except Exception as e:
            if sp is not None:
                try:
                    sp.rollback()
                except Exception:
                    pass
            errors.append(f"{label}: {type(e).__name__}: {e}")

    try:
        rows = conn.execute(
            text("SELECT id, email FROM users WHERE email LIKE :pattern"),
            {"pattern": pattern},
        ).fetchall()
    except Exception as e:
        return JSONResponse({"error": f"lookup: {e}"}, status_code=500)

    user_ids = [r["id"] for r in rows]
    emails = [r["email"] for r in rows]
    if not user_ids:
        return {"ok": True, "deleted": 0}

    user_scoped = [
        "magic_links", "sessions", "recipes", "meals",
        "product_preferences", "product_ratings",
        "grocery_state", "grocery_items", "receipt_extra_items",
        "rate_limits", "learning_dismissed",
        "meal_item_overrides", "household_members", "user_feedback",
        "user_item_groups", "user_kroger_tokens", "community_data",
        "stores", "nearby_stores", "settings", "product_prices",
    ]

    for uid in user_ids:
        safe_exec(
            """DELETE FROM meal_sides
               WHERE meal_id IN (SELECT id FROM meals WHERE user_id = :uid)""",
            {"uid": uid}, "meal_sides",
        )
        safe_exec(
            """DELETE FROM recipe_ingredients
               WHERE recipe_id IN (SELECT id FROM recipes WHERE user_id = :uid)""",
            {"uid": uid}, "recipe_ingredients",
        )
        for tbl in user_scoped:
            safe_exec(f"DELETE FROM {tbl} WHERE user_id = :uid", {"uid": uid}, tbl)

    for email in emails:
        safe_exec(
            "DELETE FROM household_invites WHERE LOWER(email) = :email",
            {"email": email.lower()}, "household_invites",
        )

    for uid in user_ids:
        safe_exec("DELETE FROM users WHERE id = :uid", {"uid": uid}, "users")

    try:
        conn.commit()
    except Exception as e:
        try:
            conn.raw.rollback()
        except Exception:
            pass
        return JSONResponse(
            {"error": f"commit: {e}", "errors": errors, "attempted": len(user_ids)},
            status_code=500,
        )

    # Verify actual deletion by re-counting.
    try:
        remaining = conn.execute(
            text("SELECT COUNT(*) AS n FROM users WHERE email LIKE :pattern"),
            {"pattern": pattern},
        ).fetchone()["n"]
    except Exception:
        remaining = None
    return {
        "ok": True,
        "attempted": len(user_ids),
        "deleted": (len(user_ids) - remaining) if remaining is not None else None,
        "remaining": remaining,
        "errors": errors,
    }


@router.post("/admin/e2e-stage-grocery-row")
async def e2e_stage_grocery_row(body: dict):
    """Playwright test scaffold: directly set receipt_status / meal_ids on a
    grocery_items row so tests can simulate states that are otherwise hard to
    reach via the UI flow (e.g. a stale receipt-matched row from a prior meal
    occurrence). Only active when PLAYWRIGHT_TEST_SECRET is set.
    """
    from mealrunner.web.auth import e2e_enabled, verify_e2e_secret

    if not e2e_enabled():
        return JSONResponse({"error": "Not found"}, status_code=404)
    if not verify_e2e_secret(body.get("secret", "")):
        return JSONResponse({"error": "Invalid secret"}, status_code=401)

    try:
        row_id = int(body["id"])
    except (KeyError, TypeError, ValueError):
        return JSONResponse({"error": "id required (int)"}, status_code=400)

    conn = _conn()
    conn.execute(
        text("""UPDATE grocery_items SET
                  receipt_status = :receipt_status,
                  meal_ids = :meal_ids
                WHERE id = :id"""),
        {
            "id": row_id,
            "receipt_status": body.get("receipt_status", ""),
            "meal_ids": body.get("meal_ids", ""),
        },
    )
    # Re-derive status from the legacy flags this scaffold just set so the
    # row reads correctly under the new active-list filter. Same formula as
    # the cold-start backfill in db.py.
    conn.execute(
        text("""UPDATE grocery_items SET status = CASE
                  WHEN removed = 1 THEN 'removed'
                  WHEN have_it = 1 THEN 'have_it'
                  WHEN receipt_status = 'dismissed' THEN 'dismissed'
                  WHEN receipt_status IN ('matched','substituted') AND receipt_acknowledged = 1 THEN 'settled'
                  WHEN receipt_status IN ('matched','substituted') AND receipt_acknowledged = 0 THEN 'bought'
                  WHEN receipt_status = 'not_fulfilled' AND receipt_acknowledged = 1 THEN 'active'
                  WHEN receipt_status = 'not_fulfilled' AND receipt_acknowledged = 0 THEN 'ordered'
                  WHEN checked = 1 AND checked_at IS NOT NULL AND checked_at >= NOW() - INTERVAL '3 days' THEN 'bought'
                  WHEN checked = 1 THEN 'settled'
                  WHEN ordered = 1 AND submitted_at IS NOT NULL THEN 'ordered'
                  ELSE 'active'
                END
                WHERE id = :id"""),
        {"id": row_id},
    )
    conn.commit()
    return {"ok": True}


@router.post("/admin/e2e-create-grocery-row")
async def e2e_create_grocery_row(body: dict, request: Request):
    """Playwright test scaffold: INSERT a fully-formed grocery_items row for
    the authenticated test user. Lets longitudinal tests stage what looks
    like accumulated history (e.g. dozens of stale receipt-matched rows
    from prior cycles) without driving them through the real flows. Only
    active when PLAYWRIGHT_TEST_SECRET is set.
    """
    from mealrunner.web.auth import e2e_enabled, verify_e2e_secret

    if not e2e_enabled():
        return JSONResponse({"error": "Not found"}, status_code=404)
    if not verify_e2e_secret(body.get("secret", "")):
        return JSONResponse({"error": "Invalid secret"}, status_code=401)

    user_id = request.state.user_id
    if not body.get("name"):
        return JSONResponse({"error": "name required"}, status_code=400)

    conn = _conn()
    row = conn.execute(
        text("""INSERT INTO grocery_items
                (user_id, name, shopping_group, source, for_meals, meal_ids,
                 meal_count, checked, have_it, removed, ordered, receipt_status,
                 receipt_acknowledged)
                VALUES (:user_id, :name, :shopping_group, :source, :for_meals,
                        :meal_ids, :meal_count, :checked, :have_it, :removed,
                        :ordered, :receipt_status, :receipt_acknowledged)
                RETURNING id"""),
        {
            "user_id": user_id,
            "name": body["name"],
            "shopping_group": body.get("shopping_group", "other"),
            "source": body.get("source", "extra"),
            "for_meals": body.get("for_meals", ""),
            "meal_ids": body.get("meal_ids", ""),
            "meal_count": int(body.get("meal_count", 0)),
            "checked": int(bool(body.get("checked", False))),
            "have_it": int(bool(body.get("have_it", False))),
            "removed": int(bool(body.get("removed", False))),
            "ordered": int(bool(body.get("ordered", False))),
            "receipt_status": body.get("receipt_status", ""),
            "receipt_acknowledged": int(bool(body.get("receipt_acknowledged", False))),
        },
    ).fetchone()
    # Re-derive status from the legacy flags this scaffold just inserted so
    # the row reads correctly under the new active-list filter. Same formula
    # as the cold-start backfill in db.py.
    conn.execute(
        text("""UPDATE grocery_items SET status = CASE
                  WHEN removed = 1 THEN 'removed'
                  WHEN have_it = 1 THEN 'have_it'
                  WHEN receipt_status = 'dismissed' THEN 'dismissed'
                  WHEN receipt_status IN ('matched','substituted') AND receipt_acknowledged = 1 THEN 'settled'
                  WHEN receipt_status IN ('matched','substituted') AND receipt_acknowledged = 0 THEN 'bought'
                  WHEN receipt_status = 'not_fulfilled' AND receipt_acknowledged = 1 THEN 'active'
                  WHEN receipt_status = 'not_fulfilled' AND receipt_acknowledged = 0 THEN 'ordered'
                  WHEN checked = 1 AND checked_at IS NOT NULL AND checked_at >= NOW() - INTERVAL '3 days' THEN 'bought'
                  WHEN checked = 1 THEN 'settled'
                  WHEN ordered = 1 AND submitted_at IS NOT NULL THEN 'ordered'
                  ELSE 'active'
                END
                WHERE id = :id"""),
        {"id": row["id"]},
    )
    conn.commit()
    return {"ok": True, "id": row["id"]}


@router.post("/admin/e2e-magic-link-token")
async def e2e_magic_link_token(body: dict):
    """Playwright: return the most recent unconsumed magic-link token for an
    e2e test email. Lets the auth flow be exercised end-to-end without an
    inbox. Only active when PLAYWRIGHT_TEST_SECRET is set; restricted to
    e2e-*@mealrunner-test.invalid emails so a misuse can't extract real
    users' tokens."""
    from mealrunner.web.auth import e2e_enabled, verify_e2e_secret, E2E_EMAIL_DOMAIN

    if not e2e_enabled():
        return JSONResponse({"error": "Not found"}, status_code=404)
    if not verify_e2e_secret(body.get("secret", "")):
        return JSONResponse({"error": "Invalid secret"}, status_code=401)

    email = body.get("email", "").strip().lower()
    if not email or not email.endswith(E2E_EMAIL_DOMAIN):
        return JSONResponse({"error": "e2e test email required"}, status_code=400)

    conn = _conn()
    row = conn.execute(
        text("""SELECT m.token FROM magic_links m
                JOIN users u ON u.id = m.user_id
                WHERE LOWER(u.email) = :email AND m.used_at IS NULL
                  AND m.expires_at > CURRENT_TIMESTAMP
                ORDER BY m.created_at DESC LIMIT 1"""),
        {"email": email},
    ).fetchone()
    return {"token": row["token"] if row else None}


@router.post("/feedback/{feedback_id}/respond")
async def respond_to_feedback(feedback_id: int, body: dict, request: Request):
    """Admin: respond to a feedback item."""
    real_user_id = getattr(request.state, 'real_user_id', request.state.user_id)
    conn = _conn()
    if not _is_admin(conn, real_user_id):
        return {"ok": False, "error": "Not authorized"}
    response_text = body.get("response", "").strip()
    if not response_text:
        return {"ok": False, "error": "Response required"}
    conn.execute(
        text("""UPDATE user_feedback
           SET status = 'responded', response = :response, responded_at = CURRENT_TIMESTAMP
           WHERE id = :id"""),
        {"id": feedback_id, "response": response_text},
    )
    conn.commit()
    return {"ok": True}


# ── Tip jar ──────────────────────────────────────────────


# Stripe Price floor is $0.50; we use $1 because below that fees ($0.30 + 2.9%)
# eat most of the tip. $1000 cap is sanity, not policy.
_TIP_MIN_CENTS = 100
_TIP_MAX_CENTS = 100000


def _monthly_price_id(amount_cents: int) -> str | None:
    """Map a monthly preset amount to its Stripe Price id from env vars.
    Pre-launch (no Stripe account yet), falls through to a deterministic fake
    Price id when stripe_client is in fake mode so the e2e flow keeps working.
    """
    import os as _os
    mapping = {
        500: _os.environ.get("STRIPE_PRICE_TIP_MONTHLY_5", ""),
        1000: _os.environ.get("STRIPE_PRICE_TIP_MONTHLY_10", ""),
    }
    pid = mapping.get(amount_cents, "")
    if pid:
        return pid
    from mealrunner.stripe_client import _is_fake_mode
    if _is_fake_mode():
        return f"price_test_monthly_{amount_cents}"
    return None


def _tip_return_url(request: Request) -> str:
    """Where Embedded Checkout sends the user after completion. The
    {CHECKOUT_SESSION_ID} placeholder is filled in by Stripe so the success
    page can fetch the session and confirm the result."""
    import os as _os
    base = _os.environ.get("APP_BASE_URL", "")
    if base:
        return f"{base}/app/tip-thanks?session_id={{CHECKOUT_SESSION_ID}}"
    return f"{request.url.scheme}://{request.url.netloc}/app/tip-thanks?session_id={{CHECKOUT_SESSION_ID}}"


@router.post("/tip/checkout-session")
async def create_tip_checkout_session(body: dict, request: Request):
    """Create an Embedded Checkout Session for a tip. Records a pending row
    in `tips` keyed on the Stripe session id; the webhook flips status to
    succeeded/failed. Returns the Embedded Checkout client_secret.
    """
    from mealrunner.stripe_client import (
        is_configured,
        create_one_time_checkout_session,
        create_monthly_checkout_session,
    )

    if not is_configured():
        return JSONResponse(
            {"ok": False, "error": "Tipping isn't set up yet"}, status_code=503
        )

    real_user_id = getattr(request.state, "real_user_id", request.state.user_id)
    mode = body.get("mode")
    if mode not in ("one_time", "monthly"):
        return JSONResponse(
            {"ok": False, "error": "mode must be 'one_time' or 'monthly'"}, status_code=400
        )
    try:
        amount_cents = int(body.get("amount_cents", 0))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "amount_cents must be int"}, status_code=400)
    if amount_cents < _TIP_MIN_CENTS or amount_cents > _TIP_MAX_CENTS:
        return JSONResponse(
            {"ok": False, "error": f"amount_cents must be between {_TIP_MIN_CENTS} and {_TIP_MAX_CENTS}"},
            status_code=400,
        )

    conn = _conn()
    user_row = conn.execute(
        text("SELECT email FROM users WHERE id = :uid"), {"uid": real_user_id}
    ).fetchone()
    customer_email = user_row["email"] if user_row else None

    return_url = _tip_return_url(request)

    try:
        if mode == "one_time":
            session = create_one_time_checkout_session(
                user_id=real_user_id,
                amount_cents=amount_cents,
                return_url=return_url,
                customer_email=customer_email,
            )
        else:
            price_id = _monthly_price_id(amount_cents)
            if not price_id:
                return JSONResponse(
                    {"ok": False, "error": "Monthly amount not configured"},
                    status_code=400,
                )
            session = create_monthly_checkout_session(
                user_id=real_user_id,
                price_id=price_id,
                return_url=return_url,
                customer_email=customer_email,
            )
    except Exception:
        logger.exception("Stripe checkout session creation failed")
        return JSONResponse({"ok": False, "error": "Stripe error"}, status_code=502)

    conn.execute(
        text("""INSERT INTO tips
                  (user_id, stripe_session_id, mode, amount_cents, currency, status)
                  VALUES (:uid, :sid, :mode, :amt, 'usd', 'pending')
                  ON CONFLICT (stripe_session_id) DO NOTHING"""),
        {"uid": real_user_id, "sid": session["id"], "mode": mode, "amt": amount_cents},
    )
    conn.commit()
    from mealrunner.stripe_client import _is_fake_mode
    return {
        "ok": True,
        "client_secret": session["client_secret"],
        "session_id": session["id"],
        "fake": _is_fake_mode(),
    }


@router.get("/tip/history")
async def tip_history(request: Request):
    """Return the user's succeeded tips, most recent first, plus the active
    subscription id (NULL = no active monthly tip).
    """
    user_id = getattr(request.state, "real_user_id", request.state.user_id)
    conn = _conn()
    rows = conn.execute(
        text("""SELECT id, mode, amount_cents, currency, status,
                       stripe_subscription_id, created_at
                  FROM tips
                  WHERE user_id = :uid AND status = 'succeeded'
                  ORDER BY created_at DESC
                  LIMIT 50"""),
        {"uid": user_id},
    ).fetchall()
    items = [{
        "id": r["id"],
        "mode": r["mode"],
        "amount_cents": r["amount_cents"],
        "currency": r["currency"],
        "is_recurring": bool(r["stripe_subscription_id"]),
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
    } for r in rows]
    user_row = conn.execute(
        text("SELECT active_tip_subscription_id FROM users WHERE id = :uid"),
        {"uid": user_id},
    ).fetchone()
    return {
        "ok": True,
        "tips": items,
        "active_subscription_id": user_row["active_tip_subscription_id"] if user_row else None,
    }


@router.post("/tip/portal")
async def tip_customer_portal(request: Request):
    """Create a Stripe Customer Portal session for managing the user's monthly
    subscription. Frontend redirects the user to the returned URL.
    """
    from mealrunner.stripe_client import is_configured, retrieve_session, customer_portal_url

    if not is_configured():
        return JSONResponse({"ok": False, "error": "Tipping isn't set up yet"}, status_code=503)

    user_id = getattr(request.state, "real_user_id", request.state.user_id)
    conn = _conn()
    sub_row = conn.execute(
        text("""SELECT stripe_session_id FROM tips
                  WHERE user_id = :uid
                    AND stripe_subscription_id IS NOT NULL
                    AND status = 'succeeded'
                  ORDER BY created_at DESC LIMIT 1"""),
        {"uid": user_id},
    ).fetchone()
    if not sub_row:
        return JSONResponse({"ok": False, "error": "No subscription found"}, status_code=404)

    try:
        session = retrieve_session(sub_row["stripe_session_id"])
        customer_id = session.get("customer")
        if not customer_id:
            return JSONResponse({"ok": False, "error": "Customer not found"}, status_code=500)
        import os as _os
        base = _os.environ.get("APP_BASE_URL", "")
        return_url = f"{base}/app" if base else f"{request.url.scheme}://{request.url.netloc}/app"
        portal_url = customer_portal_url(customer_id, return_url)
    except Exception:
        logger.exception("Stripe customer portal failed")
        return JSONResponse({"ok": False, "error": "Stripe error"}, status_code=502)

    return {"ok": True, "url": portal_url}


@router.get("/tip/stripe-config")
async def tip_stripe_config(request: Request):
    """Return the Stripe publishable key so the frontend can boot the
    Embedded Checkout iframe. Returns 503 when Stripe isn't configured —
    frontend treats that as the cue to show the fake-mode UI instead.
    """
    import os as _os
    from mealrunner.stripe_client import _is_fake_mode

    pk = _os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
    if not pk:
        return JSONResponse(
            {"ok": False, "error": "Stripe publishable key not configured", "fake": _is_fake_mode()},
            status_code=503,
        )
    return {"ok": True, "publishable_key": pk, "fake": False}


@router.post("/tip/dev-complete-session")
async def tip_dev_complete_session(body: dict, request: Request):
    """Fake-mode-only: let the logged-in user simulate a successful Stripe
    completion for one of their pending tip sessions. Lets us click through
    the tip flow on staging before a real Stripe account is configured;
    returns 404 outside fake mode. Defense in depth: only the user who
    created the session can complete it.
    """
    from mealrunner.stripe_client import _is_fake_mode
    if not _is_fake_mode():
        return JSONResponse({"error": "Not found"}, status_code=404)
    real_user_id = getattr(request.state, "real_user_id", request.state.user_id)
    sid = body.get("session_id", "")
    if not sid:
        return JSONResponse({"ok": False, "error": "session_id required"}, status_code=400)
    conn = _conn()
    row = conn.execute(
        text("SELECT user_id, mode FROM tips WHERE stripe_session_id = :sid"),
        {"sid": sid},
    ).fetchone()
    if not row or row["user_id"] != real_user_id:
        return JSONResponse({"ok": False, "error": "session not found"}, status_code=404)
    subscription_id = body.get("subscription_id")
    if row["mode"] == "monthly" and not subscription_id:
        import secrets as _secrets
        subscription_id = f"sub_test_{_secrets.token_hex(6)}"
    fake_event = {
        "type": "checkout.session.completed",
        "data": {"object": {"id": sid, "subscription": subscription_id}},
    }
    return _handle_stripe_event(fake_event)


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    """Receive verified Stripe events. Public endpoint (auth bypassed in
    auth.py PUBLIC_PATHS) — auth is the Stripe-Signature HMAC, not a session.
    """
    from mealrunner.stripe_client import construct_webhook_event

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        event = construct_webhook_event(payload, sig_header)
    except Exception as e:
        logger.warning("Stripe webhook signature failure: %s", e)
        return JSONResponse({"ok": False, "error": "signature"}, status_code=400)
    # Wrap dispatch so a handler bug returns a useful error to Stripe (500)
    # AND surfaces the traceback in Railway logs. Without this, the bare
    # exception would leak as an opaque 500 with no log line.
    try:
        return _handle_stripe_event(event)
    except Exception as e:
        try:
            etype = event.get("type", "")
        except Exception:
            etype = "?"
        logger.exception("Stripe webhook handler failed (event type=%s)", etype)
        return JSONResponse(
            {"ok": False, "error": f"{type(e).__name__}: {e}"},
            status_code=500,
        )


def _handle_stripe_event(event: dict) -> dict:
    """Dispatch a Stripe event (verified webhook OR e2e-simulated) to its
    DB-side handler. Idempotent — every INSERT uses an ON CONFLICT clause
    keyed on the Stripe id, so retries don't duplicate rows.
    """
    etype = event.get("type", "")
    obj = event.get("data", {}).get("object", {})
    conn = _conn()

    if etype == "checkout.session.completed":
        sid = obj.get("id", "")
        subscription_id = obj.get("subscription") or None
        conn.execute(
            text("""UPDATE tips
                      SET status = 'succeeded', stripe_subscription_id = :sub
                      WHERE stripe_session_id = :sid"""),
            {"sid": sid, "sub": subscription_id},
        )
        if subscription_id:
            user_row = conn.execute(
                text("SELECT user_id FROM tips WHERE stripe_session_id = :sid"),
                {"sid": sid},
            ).fetchone()
            if user_row:
                conn.execute(
                    text("UPDATE users SET active_tip_subscription_id = :sub WHERE id = :uid"),
                    {"sub": subscription_id, "uid": user_row["user_id"]},
                )
        conn.commit()

    elif etype == "invoice.paid":
        # The first invoice for a new subscription has billing_reason
        # 'subscription_create'. We already recorded that row via
        # checkout.session.completed; skip to avoid duplicate.
        billing_reason = obj.get("billing_reason", "")
        if billing_reason == "subscription_create":
            return {"ok": True, "ignored": "initial invoice covered by checkout.session.completed"}
        sub_id = obj.get("subscription") or ""
        invoice_id = obj.get("id", "")
        amount_paid = int(obj.get("amount_paid") or 0)
        currency = obj.get("currency", "usd")
        user_row = conn.execute(
            text("""SELECT user_id FROM tips
                      WHERE stripe_subscription_id = :sub
                      ORDER BY created_at DESC LIMIT 1"""),
            {"sub": sub_id},
        ).fetchone()
        if not user_row:
            return {"ok": True, "ignored": "subscription not found in tips"}
        conn.execute(
            text("""INSERT INTO tips
                      (user_id, stripe_session_id, stripe_subscription_id, stripe_invoice_id,
                       mode, amount_cents, currency, status)
                      VALUES (:uid, :sid, :sub, :inv, 'monthly', :amt, :cur, 'succeeded')
                      ON CONFLICT (stripe_invoice_id) DO NOTHING"""),
            {
                "uid": user_row["user_id"],
                "sid": f"renewal_{invoice_id}",
                "sub": sub_id,
                "inv": invoice_id,
                "amt": amount_paid,
                "cur": currency,
            },
        )
        conn.commit()

    elif etype == "invoice.payment_failed":
        sub_id = obj.get("subscription") or ""
        invoice_id = obj.get("id", "")
        amount_due = int(obj.get("amount_due") or 0)
        currency = obj.get("currency", "usd")
        user_row = conn.execute(
            text("""SELECT user_id FROM tips
                      WHERE stripe_subscription_id = :sub
                      ORDER BY created_at DESC LIMIT 1"""),
            {"sub": sub_id},
        ).fetchone()
        if user_row:
            conn.execute(
                text("""INSERT INTO tips
                          (user_id, stripe_session_id, stripe_subscription_id, stripe_invoice_id,
                           mode, amount_cents, currency, status)
                          VALUES (:uid, :sid, :sub, :inv, 'monthly', :amt, :cur, 'failed')
                          ON CONFLICT (stripe_invoice_id) DO NOTHING"""),
                {
                    "uid": user_row["user_id"],
                    "sid": f"failed_{invoice_id}",
                    "sub": sub_id,
                    "inv": invoice_id,
                    "amt": amount_due,
                    "cur": currency,
                },
            )
            conn.commit()
        # We do NOT clear active_tip_subscription_id here. Stripe handles
        # retries via Smart Retries; the subscription is only really gone
        # on customer.subscription.deleted.

    elif etype == "customer.subscription.deleted":
        sub_id = obj.get("id", "")
        conn.execute(
            text("""UPDATE users SET active_tip_subscription_id = NULL
                      WHERE active_tip_subscription_id = :sub"""),
            {"sub": sub_id},
        )
        conn.commit()

    return {"ok": True, "type": etype}


# ── Tip jar e2e simulators ───────────────────────────────


@router.post("/admin/e2e-stripe-tip-completed")
async def e2e_stripe_tip_completed(body: dict):
    """Simulate Stripe's checkout.session.completed event for a session id.
    Body: {secret, session_id, subscription_id?}. subscription_id is set for
    the monthly happy-path test, NULL for one-time.
    """
    from mealrunner.web.auth import e2e_enabled, verify_e2e_secret
    if not e2e_enabled():
        return JSONResponse({"error": "Not found"}, status_code=404)
    if not verify_e2e_secret(body.get("secret", "")):
        return JSONResponse({"error": "Invalid secret"}, status_code=401)
    fake_event = {
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": body.get("session_id", ""),
            "subscription": body.get("subscription_id"),
        }},
    }
    return _handle_stripe_event(fake_event)


@router.post("/admin/e2e-stripe-subscription-renewal")
async def e2e_stripe_subscription_renewal(body: dict):
    """Simulate Stripe's invoice.paid for a subscription renewal (NOT the
    initial invoice — that's covered by checkout.session.completed).
    Body: {secret, subscription_id, amount_cents, invoice_id?, seq?}.
    """
    from mealrunner.web.auth import e2e_enabled, verify_e2e_secret
    if not e2e_enabled():
        return JSONResponse({"error": "Not found"}, status_code=404)
    if not verify_e2e_secret(body.get("secret", "")):
        return JSONResponse({"error": "Invalid secret"}, status_code=401)
    sub_id = body.get("subscription_id", "")
    seq = body.get("seq", 1)
    invoice_id = body.get("invoice_id") or f"in_test_{sub_id}_{seq}"
    fake_event = {
        "type": "invoice.paid",
        "data": {"object": {
            "id": invoice_id,
            "subscription": sub_id,
            "amount_paid": int(body.get("amount_cents", 500)),
            "currency": body.get("currency", "usd"),
            "billing_reason": "subscription_cycle",
        }},
    }
    return _handle_stripe_event(fake_event)


@router.post("/admin/e2e-stripe-subscription-cancel")
async def e2e_stripe_subscription_cancel(body: dict):
    """Simulate customer.subscription.deleted — subscription cancelled."""
    from mealrunner.web.auth import e2e_enabled, verify_e2e_secret
    if not e2e_enabled():
        return JSONResponse({"error": "Not found"}, status_code=404)
    if not verify_e2e_secret(body.get("secret", "")):
        return JSONResponse({"error": "Invalid secret"}, status_code=401)
    fake_event = {
        "type": "customer.subscription.deleted",
        "data": {"object": {"id": body.get("subscription_id", "")}},
    }
    return _handle_stripe_event(fake_event)


@router.post("/admin/e2e-stripe-payment-failed")
async def e2e_stripe_payment_failed(body: dict):
    """Simulate invoice.payment_failed — Stripe couldn't charge the card."""
    from mealrunner.web.auth import e2e_enabled, verify_e2e_secret
    if not e2e_enabled():
        return JSONResponse({"error": "Not found"}, status_code=404)
    if not verify_e2e_secret(body.get("secret", "")):
        return JSONResponse({"error": "Invalid secret"}, status_code=401)
    sub_id = body.get("subscription_id", "")
    invoice_id = body.get("invoice_id") or f"in_test_failed_{sub_id}"
    fake_event = {
        "type": "invoice.payment_failed",
        "data": {"object": {
            "id": invoice_id,
            "subscription": sub_id,
            "amount_due": int(body.get("amount_cents", 500)),
            "currency": body.get("currency", "usd"),
        }},
    }
    return _handle_stripe_event(fake_event)


# ── Helpers ──────────────────────────────────────────────


def _meal_dict(m) -> dict:
    return {
        "id": m.id,
        "slot_date": m.slot_date,
        "recipe_id": m.recipe_id,
        "recipe_name": m.recipe_name,
        "side": m.side,  # backward compat: comma-joined side names
        "side_recipe_id": m.side_recipe_id,  # backward compat: first side's recipe ID
        "sides": [
            {"id": s.id, "side_recipe_id": s.side_recipe_id, "name": s.side_name, "position": s.position}
            for s in m.sides
        ],
        "locked": m.locked,
        "is_followup": m.is_followup,
        "on_grocery": m.on_grocery,
        "day_name": m.day_name,
        "day_short": m.day_short,
        "notes": m.notes,
    }


def _recipe_dict(r) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "cuisine": r.cuisine,
        "effort": r.effort,
        "cleanup": r.cleanup,
        "outdoor": r.outdoor,
        "kid_friendly": r.kid_friendly,
        "premade": r.premade,
        "prep_minutes": r.prep_minutes,
        "cook_minutes": r.cook_minutes,
        "servings": r.servings,
        "recipe_type": r.recipe_type,
    }
