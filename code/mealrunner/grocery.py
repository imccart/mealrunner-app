"""Grocery list builder: dedup, pantry subtraction, store split."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import text

from mealrunner.database import DictConnection
from mealrunner.models import GroceryList, GroceryListItem, Meal


def build_grocery_list(
    conn: DictConnection,
    meals: list[Meal],
    start_date: str = "",
    end_date: str = "",
    user_id: str = "default",
) -> GroceryList:
    """Build a grocery list from a list of meals."""
    # Bulk-fetch ingredients for every recipe involved (meals + sides) in one
    # query, group in Python. Avoids an N+1 over recipe_ingredients (was one
    # query per meal + one per side; for a typical 7-day plan with 2 sides
    # each that's ~21 round trips, now 1).
    recipe_ids: set[int] = set()
    for meal in meals:
        if meal.recipe_id is not None:
            recipe_ids.add(meal.recipe_id)
        for side in meal.sides:
            if side.side_recipe_id:
                recipe_ids.add(side.side_recipe_id)

    rows_by_recipe: dict[int, list[dict]] = {}
    if recipe_ids:
        placeholders = ", ".join(f":r{i}" for i in range(len(recipe_ids)))
        params = {f"r{i}": rid for i, rid in enumerate(recipe_ids)}
        rows = conn.execute(
            text(f"""SELECT ri.recipe_id, ri.ingredient_id, ri.quantity, ri.unit, ri.component,
                       i.name, i.store_pref, i.aisle, i.is_pantry_staple, i.category
                FROM recipe_ingredients ri
                JOIN ingredients i ON i.id = ri.ingredient_id
                WHERE ri.recipe_id IN ({placeholders})"""),
            params,
        ).fetchall()
        for r in rows:
            rows_by_recipe.setdefault(r["recipe_id"], []).append(dict(r))

    # Aggregate ingredients across all meals
    agg: dict[int, dict] = {}

    for meal in meals:
        # Side dish ingredients
        for side in meal.sides:
            if not side.side_recipe_id:
                continue
            side_label = meal.recipe_name
            for sr in rows_by_recipe.get(side.side_recipe_id, []):
                iid = sr["ingredient_id"]
                if iid in agg:
                    agg[iid]["quantity"] += sr["quantity"]
                    agg[iid]["meals"].add(side_label)
                else:
                    agg[iid] = {
                        "quantity": sr["quantity"],
                        "unit": sr["unit"],
                        "store": sr["store_pref"],
                        "aisle": sr["aisle"],
                        "name": sr["name"],
                        "category": sr["category"],
                        "meals": {side_label},
                    }

        if meal.recipe_id is None:
            continue

        for r in rows_by_recipe.get(meal.recipe_id, []):
            iid = r["ingredient_id"]
            # Skip the protein in follow-up meals — it's covered by the big cook
            if meal.is_followup and r["component"] == "protein":
                continue
            if iid in agg:
                agg[iid]["quantity"] += r["quantity"]
                agg[iid]["meals"].add(meal.recipe_name)
            else:
                agg[iid] = {
                    "quantity": r["quantity"],
                    "unit": r["unit"],
                    "store": r["store_pref"],
                    "aisle": r["aisle"],
                    "name": r["name"],
                    "category": r["category"],
                    "meals": {meal.recipe_name},
                }

    # Skip ingredients the user has explicitly told us they handle elsewhere
    # — anything in their staples list. Presence-only filter on both
    # ingredient_id (when the staple is linked to a canonical ingredient)
    # and compare_key on name (covers free-form staples and plural/singular
    # variants). The user adds these to the trip explicitly via
    # /grocery/add-staples; they should never auto-flow from meals.
    from mealrunner.staples import list_staples
    from mealrunner.normalize import compare_key
    staples = list_staples(conn, user_id)
    staple_name_keys = {compare_key(s.name) for s in staples}
    staple_ingredient_ids = {s.ingredient_id for s in staples if s.ingredient_id is not None}

    items: list[GroceryListItem] = []
    for iid, info in sorted(agg.items(), key=lambda x: (x[1]["store"], x[1]["aisle"], x[1]["name"])):
        if iid in staple_ingredient_ids:
            continue
        if compare_key(info["name"]) in staple_name_keys:
            continue

        items.append(GroceryListItem(
            id=None,
            list_id=0,
            ingredient_id=iid,
            total_quantity=round(info["quantity"], 2),
            unit=info["unit"],
            store=info["store"],
            aisle=info["aisle"],
            ingredient_name=info["name"],
            category=info["category"],
            meals=sorted(info["meals"]),
        ))

    return GroceryList(id=None, start_date=start_date, end_date=end_date, items=items)



def split_by_store(gl: GroceryList) -> dict[str, list[GroceryListItem]]:
    stores: dict[str, list[GroceryListItem]] = defaultdict(list)
    for item in gl.items:
        stores[item.store].append(item)
    return dict(stores)
