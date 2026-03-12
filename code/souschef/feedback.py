"""Shopping feedback loop: detect patterns from completed grocery trips.

Two patterns:
- Skipped meal items: ingredients listed for a recipe but never bought
- Extra-meal links: manually added items that correlate with specific meals
"""

from __future__ import annotations

from sqlalchemy import text

from souschef.database import DictConnection


def detect_skipped_items(conn: DictConnection, user_id: str, min_trips: int = 3) -> list[dict]:
    """Find meal ingredients the user consistently does not buy.

    Returns list of {"item", "meal", "times_listed"} dicts.
    """
    rows = conn.execute(text("""
        SELECT ti.name, ti.for_meals, ti.checked
        FROM trip_items ti
        JOIN grocery_trips gt ON gt.id = ti.trip_id
        WHERE gt.user_id = :user_id AND gt.active = 0 AND gt.completed_at IS NOT NULL
          AND ti.source = 'meal' AND ti.for_meals != ''
    """), {"user_id": user_id}).fetchall()

    # Accumulate (item, meal) -> {listed, bought}
    pairs: dict[tuple[str, str], dict] = {}
    for r in rows:
        for meal in r["for_meals"].split(","):
            meal = meal.strip()
            if not meal:
                continue
            key = (r["name"], meal)
            if key not in pairs:
                pairs[key] = {"listed": 0, "bought": 0}
            pairs[key]["listed"] += 1
            pairs[key]["bought"] += r["checked"]

    # Filter: listed enough times and never bought
    dismissed = _get_dismissed(conn, user_id, "skip")
    results = []
    for (item, meal), d in pairs.items():
        if d["listed"] >= min_trips and d["bought"] == 0:
            if f"{item}::{meal.lower()}" not in dismissed:
                results.append({
                    "item": item,
                    "meal": meal,
                    "times_listed": d["listed"],
                })
    return results


def detect_extra_meal_links(conn: DictConnection, user_id: str, min_trips: int = 3) -> list[dict]:
    """Find extra items that always appear when a specific meal is planned.

    Returns list of {"item", "meal", "times_together", "meal_trips"} dicts.
    """
    from souschef.regulars import list_regulars

    trips = conn.execute(text("""
        SELECT id FROM grocery_trips
        WHERE user_id = :user_id AND active = 0 AND completed_at IS NOT NULL
        ORDER BY id DESC LIMIT 20
    """), {"user_id": user_id}).fetchall()

    if not trips:
        return []

    # Exclude items already in regulars (those correlate with everything)
    regular_names = {r.name.lower() for r in list_regulars(conn, user_id, active_only=False)}

    trip_data = []
    for t in trips:
        tid = t["id"]
        meals_rows = conn.execute(text("""
            SELECT DISTINCT for_meals FROM trip_items
            WHERE trip_id = :tid AND source = 'meal' AND for_meals != ''
        """), {"tid": tid}).fetchall()

        meals_on_trip: set[str] = set()
        for r in meals_rows:
            for m in r["for_meals"].split(","):
                m = m.strip()
                if m:
                    meals_on_trip.add(m)

        extras = conn.execute(text("""
            SELECT name FROM trip_items
            WHERE trip_id = :tid AND source = 'extra' AND checked = 1
        """), {"tid": tid}).fetchall()

        extras_on_trip = {r["name"] for r in extras if r["name"] not in regular_names}
        trip_data.append({"meals": meals_on_trip, "extras": extras_on_trip})

    # Count co-occurrences
    meal_count: dict[str, int] = {}
    pair_count: dict[tuple[str, str], int] = {}
    for td in trip_data:
        for meal in td["meals"]:
            meal_count[meal] = meal_count.get(meal, 0) + 1
            for extra in td["extras"]:
                key = (extra, meal)
                pair_count[key] = pair_count.get(key, 0) + 1

    dismissed = _get_dismissed(conn, user_id, "extra_link")
    results = []
    for (extra, meal), count in pair_count.items():
        total = meal_count[meal]
        if count >= min_trips and count / total >= 0.75:
            if f"{extra}::{meal.lower()}" not in dismissed:
                results.append({
                    "item": extra,
                    "meal": meal,
                    "times_together": count,
                    "meal_trips": total,
                })
    return results


def get_overrides(conn: DictConnection, user_id: str) -> list[dict]:
    """Get all active meal item overrides."""
    rows = conn.execute(text(
        "SELECT recipe_name, item_name, action FROM meal_item_overrides WHERE user_id = :user_id ORDER BY recipe_name, item_name"
    ), {"user_id": user_id}).fetchall()
    return [{"recipe_name": r["recipe_name"], "item_name": r["item_name"], "action": r["action"]} for r in rows]


def get_skips_for_meal(conn: DictConnection, user_id: str, meal_name: str) -> set[str]:
    """Get item names to skip for a specific meal."""
    rows = conn.execute(text(
        "SELECT item_name FROM meal_item_overrides WHERE user_id = :user_id AND LOWER(recipe_name) = LOWER(:meal) AND action = 'skip'"
    ), {"user_id": user_id, "meal": meal_name}).fetchall()
    return {r["item_name"] for r in rows}


def get_adds_for_meal(conn: DictConnection, user_id: str, meal_name: str) -> list[dict]:
    """Get items to auto-add for a specific meal."""
    rows = conn.execute(text(
        "SELECT item_name FROM meal_item_overrides WHERE user_id = :user_id AND LOWER(recipe_name) = LOWER(:meal) AND action = 'add'"
    ), {"user_id": user_id, "meal": meal_name}).fetchall()
    return [{"item_name": r["item_name"]} for r in rows]


def _get_dismissed(conn: DictConnection, user_id: str, kind: str) -> set[str]:
    """Get dismissed feedback suggestion keys for a given kind."""
    rows = conn.execute(text(
        "SELECT name FROM learning_dismissed WHERE user_id = :user_id AND kind = :kind"
    ), {"user_id": user_id, "kind": kind}).fetchall()
    return {r["name"] for r in rows}
