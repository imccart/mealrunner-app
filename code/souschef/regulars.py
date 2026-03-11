"""Regulars — recurring items bought on a regular basis.

Replaces the old essentials + pantry staples split. A "regular" is anything
the user buys repeatedly: coffee, eggs, olive oil, flour. The user checks
what they need each grocery run from a saved list.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class Regular:
    id: int | None
    name: str
    ingredient_id: int | None  # nullable FK → ingredients
    shopping_group: str  # resolved: from ingredient if linked, else own field
    store_pref: str
    active: bool = True


def list_regulars(
    conn: sqlite3.Connection, active_only: bool = True
) -> list[Regular]:
    """List regulars, resolving shopping_group from linked ingredient when available."""
    query = """
        SELECT r.*, COALESCE(i.aisle, r.shopping_group) AS resolved_group
        FROM regulars r
        LEFT JOIN ingredients i ON i.id = r.ingredient_id
    """
    if active_only:
        query += " WHERE r.active = 1"
    query += " ORDER BY resolved_group, r.name"
    rows = conn.execute(query).fetchall()
    return [
        Regular(
            id=r["id"],
            name=r["name"],
            ingredient_id=r["ingredient_id"],
            shopping_group=r["resolved_group"] or "Other",
            store_pref=r["store_pref"],
            active=bool(r["active"]),
        )
        for r in rows
    ]


def add_regular(
    conn: sqlite3.Connection,
    name: str,
    shopping_group: str = "",
    store_pref: str = "either",
) -> Regular:
    """Add a regular item. Silently links to an ingredient if a match exists."""
    ingredient_id = _match_ingredient(conn, name)

    # If we matched an ingredient and no group was given, inherit it
    if ingredient_id and not shopping_group:
        row = conn.execute(
            "SELECT aisle FROM ingredients WHERE id = ?", (ingredient_id,)
        ).fetchone()
        if row and row["aisle"]:
            shopping_group = row["aisle"]

    if not shopping_group:
        shopping_group = _infer_group(name)

    conn.execute(
        """INSERT INTO regulars (name, ingredient_id, shopping_group, store_pref)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(name) DO UPDATE SET
               active = 1,
               ingredient_id = COALESCE(excluded.ingredient_id, regulars.ingredient_id),
               shopping_group = CASE WHEN excluded.shopping_group != '' THEN excluded.shopping_group ELSE regulars.shopping_group END,
               store_pref = excluded.store_pref""",
        (name, ingredient_id, shopping_group, store_pref),
    )
    conn.commit()

    row = conn.execute("SELECT * FROM regulars WHERE name = ?", (name,)).fetchone()
    return Regular(
        id=row["id"],
        name=row["name"],
        ingredient_id=row["ingredient_id"],
        shopping_group=row["shopping_group"] or shopping_group,
        store_pref=row["store_pref"],
        active=bool(row["active"]),
    )


def remove_regular(conn: sqlite3.Connection, name: str) -> bool:
    """Soft-delete a regular (sets active=0)."""
    cursor = conn.execute(
        "UPDATE regulars SET active = 0 WHERE name = ? AND active = 1", (name,)
    )
    conn.commit()
    return cursor.rowcount > 0


def toggle_regular(conn: sqlite3.Connection, regular_id: int) -> Regular | None:
    """Toggle a regular's active state."""
    row = conn.execute("SELECT * FROM regulars WHERE id = ?", (regular_id,)).fetchone()
    if not row:
        return None
    new_active = 0 if row["active"] else 1
    conn.execute("UPDATE regulars SET active = ? WHERE id = ?", (new_active, regular_id))
    conn.commit()
    return Regular(
        id=row["id"],
        name=row["name"],
        ingredient_id=row["ingredient_id"],
        shopping_group=row["shopping_group"] or "Other",
        store_pref=row["store_pref"],
        active=bool(new_active),
    )


def get_regulars_by_group(
    conn: sqlite3.Connection, active_only: bool = True
) -> dict[str, list[Regular]]:
    """Return regulars grouped by shopping_group."""
    items = list_regulars(conn, active_only=active_only)
    groups: dict[str, list[Regular]] = {}
    for item in items:
        groups.setdefault(item.shopping_group, []).append(item)
    return groups


# ── Silent matching ──────────────────────────────────────


def _match_ingredient(conn: sqlite3.Connection, name: str) -> int | None:
    """Try to match a regular name to an existing ingredient. Returns ingredient_id or None."""
    # Exact match first
    row = conn.execute(
        "SELECT id FROM ingredients WHERE LOWER(name) = LOWER(?)", (name,)
    ).fetchone()
    if row:
        return row["id"]

    # Fuzzy: check if the name contains an ingredient name or vice versa
    rows = conn.execute("SELECT id, name FROM ingredients").fetchall()
    name_lower = name.lower()
    for r in rows:
        ing_name = r["name"].lower()
        if ing_name in name_lower or name_lower in ing_name:
            return r["id"]

    return None


_GROUP_KEYWORDS: dict[str, list[str]] = {
    "Produce": ["apple", "banana", "lettuce", "tomato", "onion", "potato", "fruit",
                 "veggie", "vegetable", "pepper", "carrot", "celery", "garlic", "avocado",
                 "lemon", "lime", "cilantro", "parsley", "basil", "spinach", "broccoli"],
    "Meat": ["chicken", "beef", "pork", "turkey", "sausage", "bacon", "steak", "ground",
             "ham", "meatball"],
    "Dairy & Eggs": ["milk", "cream", "cheese", "yogurt", "butter", "egg", "sour cream"],
    "Bread & Bakery": ["bread", "bun", "roll", "tortilla", "pita", "cornbread", "bagel"],
    "Pasta & Grains": ["pasta", "noodle", "rice", "quinoa", "couscous", "oat"],
    "Spices & Baking": ["cumin", "chili powder", "paprika", "oregano", "cinnamon",
                         "pepper", "seasoning", "spice", "sugar", "flour", "baking",
                         "vanilla", "cocoa", "salt", "thyme", "garlic powder",
                         "onion powder", "cayenne", "nutmeg"],
    "Condiments & Sauces": ["sauce", "ketchup", "mustard", "mayo", "dressing", "vinegar",
                             "oil", "soy sauce", "worcestershire", "hot sauce", "salsa",
                             "tomato paste", "honey", "syrup", "ranch"],
    "Canned Goods": ["canned", "soup", "broth", "stock", "beans", "tomato sauce",
                      "diced tomato", "paste", "tuna"],
    "Frozen": ["frozen", "ice cream"],
    "Breakfast & Beverages": ["cereal", "granola", "oatmeal", "juice", "tea",
                               "la croix", "soda", "water", "pancake"],
    "Snacks": ["chips", "crackers", "cookies", "snack", "popcorn", "nuts", "pretzel"],
}


def _infer_group(name: str) -> str:
    """Infer shopping group from item name using keyword matching."""
    name_lower = name.lower()
    for group, keywords in _GROUP_KEYWORDS.items():
        for kw in keywords:
            if kw in name_lower:
                return group
    # Check for multi-word matches that need special handling
    # "pepper" alone → Produce, but "black pepper" or "chili powder" → Spices
    spice_terms = ["black pepper", "chili powder", "garlic powder", "onion powder"]
    for term in spice_terms:
        if term in name_lower:
            return "Spices & Baking"
    return "Other"
