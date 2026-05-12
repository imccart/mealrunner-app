"""Staples — items the user buys regularly or keeps on hand.

Replaces the legacy regulars + pantry tables. A "staple" is anything the
user wants the app to know about as a recurring item; the `mode` column
distinguishes "Every trip" (default-suggested on each grocery list) from
"Keep on hand" (user has it; only added when explicitly chosen).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import text

from mealrunner.database import DictConnection


EVERY_TRIP = "every_trip"
KEEP_ON_HAND = "keep_on_hand"
VALID_MODES = (EVERY_TRIP, KEEP_ON_HAND)


@dataclass
class Staple:
    id: int | None
    name: str
    ingredient_id: int | None
    shopping_group: str
    store_pref: str
    mode: str


def _row_to_staple(r) -> Staple:
    return Staple(
        id=r["id"],
        name=r["name"],
        ingredient_id=r["ingredient_id"],
        shopping_group=r["shopping_group"] or "",
        store_pref=r["store_pref"],
        mode=r["mode"],
    )


def list_staples(
    conn: DictConnection, user_id: str, mode: str | None = None
) -> list[Staple]:
    """Return all staples for a user, optionally filtered by mode."""
    if mode is not None:
        rows = conn.execute(
            text(
                "SELECT * FROM staples WHERE user_id = :user_id AND mode = :mode"
                " ORDER BY name"
            ),
            {"user_id": user_id, "mode": mode},
        ).fetchall()
    else:
        rows = conn.execute(
            text("SELECT * FROM staples WHERE user_id = :user_id ORDER BY name"),
            {"user_id": user_id},
        ).fetchall()
    return [_row_to_staple(r) for r in rows]


def add_staple(
    conn: DictConnection,
    user_id: str,
    name: str,
    mode: str,
    shopping_group: str = "",
    store_pref: str = "either",
) -> Staple:
    """Add or update a staple.

    Silently links to a canonical ingredient if a match exists. If the
    user already has a staple for the same canonical ingredient (or the
    same name when there's no ingredient match), this updates the mode
    on the existing row rather than creating a duplicate — that's the
    mutual-exclusion invariant ("every trip" vs "keep on hand" is a
    per-item attribute, not a separate concept).
    """
    if mode not in VALID_MODES:
        raise ValueError(f"invalid staple mode: {mode!r}")

    from mealrunner.normalize import normalize_item_name

    canonical, ingredient_id = normalize_item_name(conn, name)
    name = canonical

    if ingredient_id and not shopping_group:
        row = conn.execute(
            text("SELECT aisle FROM ingredients WHERE id = :id"),
            {"id": ingredient_id},
        ).fetchone()
        if row and row["aisle"]:
            shopping_group = row["aisle"]
    if not shopping_group:
        shopping_group = _infer_group(name)

    if ingredient_id is not None:
        existing = conn.execute(
            text(
                "SELECT id FROM staples WHERE user_id = :user_id"
                " AND ingredient_id = :iid"
            ),
            {"user_id": user_id, "iid": ingredient_id},
        ).fetchone()
    else:
        existing = conn.execute(
            text(
                "SELECT id FROM staples WHERE user_id = :user_id"
                " AND ingredient_id IS NULL AND LOWER(name) = LOWER(:name)"
            ),
            {"user_id": user_id, "name": name},
        ).fetchone()

    if existing:
        conn.execute(
            text(
                """UPDATE staples SET
                       mode = :mode,
                       ingredient_id = COALESCE(:iid, ingredient_id),
                       shopping_group = CASE WHEN :group != '' THEN :group ELSE shopping_group END,
                       store_pref = :store_pref,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = :id"""
            ),
            {
                "mode": mode, "iid": ingredient_id, "group": shopping_group,
                "store_pref": store_pref, "id": existing["id"],
            },
        )
        staple_id = existing["id"]
    else:
        result = conn.execute(
            text(
                """INSERT INTO staples
                       (user_id, name, ingredient_id, shopping_group, store_pref, mode)
                   VALUES (:user_id, :name, :iid, :group, :store_pref, :mode)
                   RETURNING id"""
            ),
            {
                "user_id": user_id, "name": name, "iid": ingredient_id,
                "group": shopping_group, "store_pref": store_pref, "mode": mode,
            },
        )
        staple_id = result.fetchone()["id"]
    conn.commit()

    row = conn.execute(
        text("SELECT * FROM staples WHERE id = :id"), {"id": staple_id}
    ).fetchone()
    return _row_to_staple(row)


def update_staple(
    conn: DictConnection,
    user_id: str,
    staple_id: int,
    mode: str | None = None,
    shopping_group: str | None = None,
) -> Staple | None:
    """Update the mode and/or shopping group of an existing staple."""
    if mode is not None and mode not in VALID_MODES:
        raise ValueError(f"invalid staple mode: {mode!r}")

    sets = ["updated_at = CURRENT_TIMESTAMP"]
    params: dict = {"id": staple_id, "user_id": user_id}
    if mode is not None:
        sets.append("mode = :mode")
        params["mode"] = mode
    if shopping_group is not None:
        sets.append("shopping_group = :group")
        params["group"] = shopping_group

    conn.execute(
        text(f"UPDATE staples SET {', '.join(sets)} WHERE id = :id AND user_id = :user_id"),
        params,
    )
    conn.commit()

    row = conn.execute(
        text("SELECT * FROM staples WHERE id = :id AND user_id = :user_id"),
        {"id": staple_id, "user_id": user_id},
    ).fetchone()
    return _row_to_staple(row) if row else None


def remove_staple(conn: DictConnection, user_id: str, staple_id: int) -> bool:
    cursor = conn.execute(
        text("DELETE FROM staples WHERE id = :id AND user_id = :user_id"),
        {"id": staple_id, "user_id": user_id},
    )
    conn.commit()
    return cursor.rowcount > 0


def mark_bought(
    conn: DictConnection, user_id: str, ingredient_ids: Iterable[int]
) -> None:
    """Update last_bought_at for the given ingredient ids if they're staples
    for this user. Used by receipt reconciliation for smart suggestions."""
    ids = list(ingredient_ids)
    if not ids:
        return
    ph = ", ".join(f":i{i}" for i in range(len(ids)))
    params = {f"i{i}": iid for i, iid in enumerate(ids)}
    params["user_id"] = user_id
    conn.execute(
        text(
            f"""UPDATE staples SET last_bought_at = CURRENT_TIMESTAMP
                WHERE user_id = :user_id AND ingredient_id IN ({ph})"""
        ),
        params,
    )
    conn.commit()


_GROUP_KEYWORDS: dict[str, list[str]] = {
    "Produce": ["apple", "banana", "lettuce", "tomato", "onion", "potato", "fruit",
                 "veggie", "vegetable", "pepper", "carrot", "celery", "garlic", "avocado",
                 "lemon", "lime", "cilantro", "parsley", "basil", "spinach", "broccoli"],
    "Meat": ["chicken", "beef", "pork", "turkey", "sausage", "bacon", "steak", "ground",
             "ham", "meatball"],
    "Dairy & Eggs": ["milk", "cream", "cheese", "yogurt", "butter", "egg", "sour cream"],
    "Bread & Bakery": ["bread", "bun", "roll", "tortilla", "pita", "cornbread", "bagel"],
    "Pasta & Grains": ["pasta", "noodle", "rice", "quinoa", "couscous", "oat"],
    "Spices & Baking": ["black pepper", "chili powder", "garlic powder", "onion powder",
                         "cumin", "paprika", "oregano", "cinnamon",
                         "pepper", "seasoning", "spice", "sugar", "flour", "baking",
                         "vanilla", "cocoa", "salt", "thyme", "cayenne", "nutmeg"],
    "Condiments & Sauces": ["sauce", "ketchup", "mustard", "mayo", "dressing", "vinegar",
                             "oil", "soy sauce", "worcestershire", "hot sauce", "salsa",
                             "tomato paste", "honey", "syrup", "ranch"],
    "Canned Goods": ["canned", "soup", "broth", "stock", "beans", "tomato sauce",
                      "diced tomato", "paste", "tuna"],
    "Frozen": ["frozen", "ice cream"],
    "Breakfast & Beverages": ["cereal", "granola", "oatmeal", "juice", "tea",
                               "la croix", "soda", "water", "pancake"],
    "Snacks": ["tortilla chips", "chips", "crackers", "cookies", "snack", "popcorn", "nuts", "pretzel"],
    "Personal Care": ["shampoo", "conditioner", "soap", "toothpaste", "toothbrush", "deodorant",
                       "lotion", "razor", "floss", "mouthwash", "sunscreen", "body wash",
                       "tissue", "tissues", "chapstick", "contact", "cotton"],
    "Household": ["battery", "batteries", "light bulb", "trash bag", "garbage bag", "aluminum foil",
                   "plastic wrap", "ziplock", "ziploc", "paper towel", "napkin", "candle",
                   "toilet paper", "paper plate", "cup", "straw"],
    "Cleaning": ["cleaner", "wipes", "sponge", "dish soap", "detergent", "bleach", "lysol",
                  "disinfectant", "broom", "mop", "duster", "dryer sheet", "fabric softener"],
    "Pets": ["cat food", "dog food", "cat litter", "kitty litter", "pet food", "treats",
             "flea", "heartworm", "pet"],
}


def _infer_group(name: str) -> str:
    """Infer shopping group from item name. Longest keyword wins so
    'tortilla chips' lands in Snacks (via 'chips') rather than Bread &
    Bakery (via 'tortilla')."""
    name_lower = name.lower()
    pairs = []
    for group, keywords in _GROUP_KEYWORDS.items():
        for kw in keywords:
            pairs.append((kw, group))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    for kw, group in pairs:
        if kw in name_lower:
            return group
    return "Other"
