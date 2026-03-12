"""Service layer for the souschef workflow.

Orchestrates plan → grocery → order → reconcile flow.
Both CLI and web frontends call these functions.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import text

from souschef.database import DictConnection
from souschef.models import GrocerySelections, Meal, MealWeek, WorkflowStatus
from souschef.planner import (
    DAY_NAMES,
    load_current_week,
    load_meal_week,
    load_meals,
    load_rolling_week,
    rolling_range,
    week_range,
)

_CONFIG_DIR = Path.home() / ".souschef"
_SAVED_LIST_FILE = _CONFIG_DIR / "current_list.json"
_SAVED_SHEET_FILE = _CONFIG_DIR / "sheet_id.txt"
_RECONCILE_FILE = _CONFIG_DIR / "reconcile_result.json"


# ── Meal Operations ───────────────────────────────────


def list_weeks(conn: DictConnection, user_id: str) -> list[dict]:
    """List all weeks that have meals. Returns [{start_date, end_date, meal_count, accepted}, ...]."""
    rows = conn.execute(
        text("SELECT slot_date FROM meals WHERE user_id = :user_id ORDER BY slot_date"),
        {"user_id": user_id},
    ).fetchall()
    if not rows:
        return []

    from datetime import date, timedelta

    # Group by week (Monday-Sunday)
    weeks: dict[str, list[str]] = {}
    statuses: dict[str, list[str]] = {}
    for r in rows:
        d = date.fromisoformat(r["slot_date"])
        monday = (d - timedelta(days=d.weekday())).isoformat()
        weeks.setdefault(monday, []).append(r["slot_date"])

    # Get status for each meal
    all_meals = conn.execute(
        text("SELECT slot_date, status FROM meals WHERE user_id = :user_id"),
        {"user_id": user_id},
    ).fetchall()
    status_map = {r["slot_date"]: r["status"] for r in all_meals}

    result = []
    for monday in sorted(weeks.keys(), reverse=True):
        dates = weeks[monday]
        sunday = (date.fromisoformat(monday) + timedelta(days=6)).isoformat()
        accepted = all(status_map.get(d) == "accepted" for d in dates)
        result.append({
            "start_date": monday,
            "end_date": sunday,
            "week_of": monday,  # backward compat for templates
            "meal_count": len(dates),
            "accepted": accepted,
        })
    return result


def get_meals_for_week(
    conn: DictConnection, user_id: str, week: str | None = None
) -> MealWeek:
    """Load meals for a week. week is any date in that week (defaults to current)."""
    return load_meal_week(conn, user_id, week)


def get_rolling_meals(conn: DictConnection, user_id: str) -> MealWeek:
    """Load meals for the rolling 7-day window starting today."""
    return load_rolling_week(conn, user_id)


def get_meals(
    conn: DictConnection, user_id: str, start_date: str, end_date: str
) -> list[Meal]:
    """Load meals in a date range."""
    return load_meals(conn, user_id, start_date, end_date)


def parse_day(day_str: str) -> int | None:
    """Parse a day string (name or 0-6) to day index."""
    if day_str.isdigit():
        d = int(day_str)
        return d if 0 <= d <= 6 else None
    for i, name in enumerate(DAY_NAMES):
        if name.lower().startswith(day_str.lower()):
            return i
    return None


# ── Legacy compatibility ──────────────────────────────


def list_plans(conn: DictConnection, user_id: str) -> list[dict]:
    """Legacy: list plans. Now returns weeks."""
    weeks = list_weeks(conn, user_id)
    # Add synthetic 'id' for templates that still reference it
    for i, w in enumerate(weeks):
        w["id"] = i + 1
    return weeks


def get_plan(conn: DictConnection, plan_id: int | None = None):
    """Legacy: load a plan by ID or latest. Returns MealPlan for backward compat."""
    from souschef.planner import load_latest_plan, load_plan
    if plan_id:
        return load_plan(conn, plan_id)
    return load_latest_plan(conn)


# ── Grocery Selections State ────────────────────────────


def _date_key(start_date: str, end_date: str) -> str:
    """Build a date key for grocery selections."""
    return f"{start_date}/{end_date}"


def save_grocery_selections(
    plan_id: int = 0,
    regulars: list[str] | None = None,
    extras: list[str] | None = None,
    meal_items: list[str] | None = None,
    store_assignments: dict[str, str] | None = None,
    date_key: str = "",
) -> None:
    """Save grocery selections keyed by date range (or legacy plan_id)."""
    data = {
        "plan_id": plan_id,
        "date_key": date_key,
        "regulars": regulars or [],
        "extras": extras or [],
        "meal_items": meal_items or [],
        "stores": store_assignments or {},
    }
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(_SAVED_LIST_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_grocery_selections(
    plan_id: int = 0, date_key: str = ""
) -> GrocerySelections | None:
    """Load saved grocery selections matching date_key or legacy plan_id."""
    if not _SAVED_LIST_FILE.exists():
        return None
    with open(_SAVED_LIST_FILE) as f:
        data = json.load(f)

    # Match by date_key first, then fall back to plan_id, then load whatever is there
    saved_dk = data.get("date_key", "")
    saved_pid = data.get("plan_id")

    if date_key and saved_dk == date_key:
        pass  # exact match
    elif plan_id and saved_pid == plan_id:
        pass  # legacy match
    else:
        pass  # load whatever is there — rolling window may shift daily

    # Backward compat: old files have "essentials" + "staples", new files have "regulars"
    regulars = data.get("regulars", [])
    if not regulars:
        regulars = data.get("essentials", []) + data.get("staples", [])

    return GrocerySelections(
        plan_id=data.get("plan_id", 0),
        date_key=saved_dk,
        regulars=regulars,
        extras=data.get("extras", []),
        meal_items=data.get("meal_items", []),
        stores=data.get("stores", {}),
    )


def remove_grocery_item(name: str) -> None:
    """Remove an item from the saved grocery selections (all categories)."""
    if not _SAVED_LIST_FILE.exists():
        return
    with open(_SAVED_LIST_FILE) as f:
        data = json.load(f)
    lowered = name.lower()
    for key in ("regulars", "meal_items", "extras", "essentials", "staples"):
        data[key] = [n for n in data.get(key, []) if n.lower() != lowered]
    with open(_SAVED_LIST_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ── Grocery List Reconstruction ─────────────────────────


def reconstruct_grocery_list(
    conn: DictConnection,
    user_id: str,
    meals: list[Meal],
    start_date: str = "",
    end_date: str = "",
    plan_id: int = 0,
) -> dict | None:
    """Rebuild the full grocery list from saved selections.

    Returns dict with keys: grocery_list, regulars, extras, selections
    or None if no saved selections.
    """
    from souschef.grocery import build_grocery_list
    from souschef.regulars import list_regulars

    dk = _date_key(start_date, end_date)
    sel = load_grocery_selections(plan_id=plan_id, date_key=dk)
    if sel is None:
        return None

    gl = build_grocery_list(conn, meals, start_date, end_date, user_id=user_id)
    # Filter to only items the user kept
    saved_meal_set = {n.lower() for n in sel.meal_items}
    gl.items = [item for item in gl.items if item.ingredient_name.lower() in saved_meal_set]

    all_regulars = list_regulars(conn, user_id)
    regular_map = {r.name.lower(): r for r in all_regulars}
    selected_regulars = [regular_map[n.lower()] for n in sel.regulars if n.lower() in regular_map]

    return {
        "grocery_list": gl,
        "regulars": selected_regulars,
        "extras": sel.extras,
        "selections": sel,
    }


def get_search_list(
    conn: DictConnection,
    start_date: str = "",
    end_date: str = "",
    plan_id: int = 0,
) -> dict:
    """Get items split by store type. Returns {"api_items": [...], "in_person": {"Store": [...]}}.

    Requires saved grocery selections.
    """
    from souschef.stores import list_stores

    dk = _date_key(start_date, end_date)
    sel = load_grocery_selections(plan_id=plan_id, date_key=dk)
    if sel is None:
        return {"api_items": [], "in_person": {}}

    store_assignments = sel.stores
    configured = {s["key"]: s for s in list_stores()}
    api_keys = {k for k, s in configured.items() if s["api"] != "none"}

    seen: set[str] = set()
    api_items: list[str] = []
    in_person: dict[str, list[str]] = {}

    for name in sel.all_names:
        if name.lower() not in seen:
            seen.add(name.lower())
            store_key = store_assignments.get(name, "")
            if store_key in api_keys:
                api_items.append(name)
            elif store_key and store_key in configured:
                store_name = configured[store_key]["name"]
                in_person.setdefault(store_name, []).append(name)

    return {"api_items": api_items, "in_person": in_person}


# ── Sheet State ─────────────────────────────────────────


def save_sheet_id(sheet_id: str) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _SAVED_SHEET_FILE.write_text(sheet_id)


def load_sheet_id() -> str | None:
    if _SAVED_SHEET_FILE.exists():
        sid = _SAVED_SHEET_FILE.read_text().strip()
        if sid:
            return sid
    return None


# ── Reconcile State ─────────────────────────────────────


def save_reconcile_result(matched_items: list[str]) -> None:
    """Accumulate matched item names across multiple reconciliations."""
    existing = set()
    if _RECONCILE_FILE.exists():
        with open(_RECONCILE_FILE) as f:
            existing = set(json.load(f).get("matched", []))
    existing.update(n for n in matched_items if n)
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(_RECONCILE_FILE, "w") as f:
        json.dump({"matched": sorted(existing)}, f)


def load_reconcile_result() -> list[str] | None:
    if not _RECONCILE_FILE.exists():
        return None
    with open(_RECONCILE_FILE) as f:
        return json.load(f).get("matched", [])


def set_reconcile_result(matched_items: list[str]) -> None:
    """Replace the reconcile state entirely (for toggle operations)."""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(_RECONCILE_FILE, "w") as f:
        json.dump({"matched": sorted(n for n in matched_items if n)}, f)


def clear_reconcile_result() -> None:
    if _RECONCILE_FILE.exists():
        _RECONCILE_FILE.unlink()


# ── Reconcile Orchestration ─────────────────────────────


def reconcile_receipt(
    conn: DictConnection,
    user_id: str,
    receipt_items: list[dict],
    start_date: str = "",
    end_date: str = "",
    plan_id: int | None = None,
) -> dict:
    """Match receipt items against the grocery list, save preferences, update reconcile state."""
    from souschef.reconcile import diff_grocery_list

    # Get grocery selections
    dk = _date_key(start_date, end_date) if start_date else ""
    sel = load_grocery_selections(plan_id=plan_id or 0, date_key=dk)
    if sel is None:
        return {"matched": [], "unmatched": receipt_items, "preferences_saved": 0}

    result = diff_grocery_list(sel.all_names, receipt_items)

    from souschef.kroger import KrogerProduct, save_preference
    prefs_saved = 0
    for m in result["matched"]:
        rec = m["receipt"]
        upc = rec.get("upc", "")
        if upc:
            p = KrogerProduct(product_id="", upc=upc, description=rec["item"], brand="", size="")
            save_preference(conn, user_id, m["grocery_name"], p, source="receipt")
            prefs_saved += 1

    save_reconcile_result([m["grocery_name"] for m in result["matched"]])

    return {
        "matched": result["matched"],
        "unmatched": result["unmatched"],
        "preferences_saved": prefs_saved,
    }


def parse_receipt(source_path: str) -> list[dict]:
    """Parse a receipt file (PDF, image, email, or text)."""
    from pathlib import Path

    from souschef.reconcile import (
        parse_receipt_email,
        parse_receipt_image,
        parse_receipt_pdf,
        parse_receipt_text,
    )

    p = Path(source_path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {source_path}")

    suffix = p.suffix.lower()
    if suffix == ".pdf":
        return parse_receipt_pdf(source_path)
    elif suffix == ".eml":
        return parse_receipt_email(source_path)
    elif suffix in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        return parse_receipt_image(source_path)
    else:
        with open(p) as f:
            return parse_receipt_text(f.read())


# ── Export Orchestration ────────────────────────────────


def export_to_sheets(
    conn: DictConnection,
    user_id: str,
    start_date: str = "",
    end_date: str = "",
    plan_id: int | None = None,
    sheet_id: str | None = None,
    force_new: bool = False,
) -> str | None:
    """Export grocery list to Google Sheets. Returns URL or None on failure."""
    from souschef.sheets import export_grocery_list

    # Load meals for the date range
    if start_date and end_date:
        meals = load_meals(conn, user_id, start_date, end_date)
    else:
        # Legacy: fall back to plan_id
        plan = get_plan(conn, plan_id)
        if plan is None:
            return None
        s, e = week_range(plan.week_of)
        start_date, end_date = s, e
        meals = load_meals(conn, user_id, s, e)

    rebuilt = reconstruct_grocery_list(
        conn, user_id, meals, start_date, end_date, plan_id=plan_id or 0
    )
    if rebuilt is None:
        return None

    reconciled = load_reconcile_result()
    strikethrough_names = {n.lower() for n in reconciled} if reconciled else set()

    if not force_new and not sheet_id:
        sheet_id = load_sheet_id()

    # Build a lightweight plan-like object for sheets export
    mw = MealWeek(start_date=start_date, end_date=end_date, meals=meals)

    url = export_grocery_list(
        conn, mw,
        spreadsheet_id=sheet_id,
        regulars_list=rebuilt["regulars"],
        extra_items=rebuilt["extras"],
        strikethrough_names=strikethrough_names,
        grocery_list=rebuilt["grocery_list"],
    )

    actual_id = url.split("/d/")[1].split("/")[0] if "/d/" in url else sheet_id
    if actual_id:
        save_sheet_id(actual_id)

    return url


# ── Workflow Status ─────────────────────────────────────


def get_workflow_status(conn: DictConnection, user_id: str) -> WorkflowStatus:
    """Get the current state of the workflow for the rolling window."""
    from souschef.kroger import load_order

    mw = load_rolling_week(conn, user_id)
    if not mw.meals:
        return WorkflowStatus()

    sel = load_grocery_selections(date_key=_date_key(mw.start_date, mw.end_date))
    grocery_built = sel is not None

    order = load_order()
    order_placed = bool(order)

    reconciled = load_reconcile_result()
    reconcile_count = len(reconciled) if reconciled else 0

    # Freeform meals (Eating Out, Leftovers) have no ingredients — always "on list"
    real_meals = [m for m in mw.meals if m.recipe_id is not None]
    freeform_count = len(mw.meals) - len(real_meals)
    meals_on_grocery = sum(1 for m in real_meals if m.on_grocery) + freeform_count

    return WorkflowStatus(
        start_date=mw.start_date,
        end_date=mw.end_date,
        has_meals=True,
        meals_on_grocery=meals_on_grocery,
        total_meals=len(mw.meals),
        grocery_built=grocery_built,
        order_placed=order_placed,
        reconcile_count=reconcile_count,
    )
