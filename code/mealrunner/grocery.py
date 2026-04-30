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

    # Skip ingredients the user has explicitly told us they handle elsewhere:
    # active regulars (the "every trip" checklist) and pantry items they've
    # said they have on hand. The ingredient-level is_pantry_staple flag is
    # NOT used for filtering — that's a hint for onboarding / "add to pantry?"
    # suggestions, not a silent gate on what reaches the grocery list.
    from mealrunner.regulars import list_regulars
    regular_names = {r.name.lower() for r in list_regulars(conn, user_id)}

    # Bulk-fetch pantry quantities for every aggregated ingredient in one
    # query (was one query per ingredient via get_pantry_quantity).
    pantry_qtys: dict[int, float] = {}
    if agg:
        agg_ids = list(agg.keys())
        ph = ", ".join(f":i{i}" for i in range(len(agg_ids)))
        ps = {f"i{i}": iid for i, iid in enumerate(agg_ids)}
        ps["uid"] = user_id
        for row in conn.execute(
            text(f"""SELECT ingredient_id, quantity FROM pantry
                  WHERE user_id = :uid AND ingredient_id IN ({ph})"""),
            ps,
        ).fetchall():
            pantry_qtys[row["ingredient_id"]] = row["quantity"]

    items: list[GroceryListItem] = []
    for iid, info in sorted(agg.items(), key=lambda x: (x[1]["store"], x[1]["aisle"], x[1]["name"])):
        if info["name"].lower() in regular_names:
            continue

        pantry_qty = pantry_qtys.get(iid, 0.0)
        needed = info["quantity"] - pantry_qty
        if needed <= 0:
            continue

        items.append(GroceryListItem(
            id=None,
            list_id=0,
            ingredient_id=iid,
            total_quantity=round(needed, 2),
            unit=info["unit"],
            store=info["store"],
            aisle=info["aisle"],
            from_pantry=pantry_qty,
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
