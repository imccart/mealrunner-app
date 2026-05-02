"""One-time migration to consolidate pluralized seed renames in the ingredients table.

After commit 5d03ba6 renamed 40 entries in the seed YAML (apple → apples, etc.),
the seed-loader's ON CONFLICT DO NOTHING leaves both rows in prod: the old
'apple' (with all the FK references) sitting alongside the freshly-inserted
'apples'. This script repoints all FK references from the old row to the new
one and deletes the old row.

Side effect: regulars/pantry/recipe_ingredients rows that pointed to old
ingredient_ids now point to the renamed ingredient. Their own `name` columns
are left alone — the user's typed regular stays as typed; only the FK moves.

Run once per environment: `DATABASE_URL='...' python migrate_pluralize_seed.py`.
Idempotent — running twice is a no-op since the old rows will already be gone.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "code"))

from sqlalchemy import text  # noqa: E402

from mealrunner.database import get_connection  # noqa: E402

RENAMES = {
    "apple": "apples", "banana": "bananas", "orange": "oranges",
    "lemon": "lemons", "lime": "limes", "peach": "peaches", "pear": "pears",
    "plum": "plums", "nectarine": "nectarines",
    "yellow onion": "yellow onions", "red onion": "red onions",
    "white onion": "white onions",
    "potato": "potatoes", "sweet potato": "sweet potatoes",
    "russet potato": "russet potatoes", "red potato": "red potatoes",
    "carrot": "carrots", "bell pepper": "bell peppers",
    "jalapeno": "jalapenos", "serrano pepper": "serrano peppers",
    "poblano pepper": "poblano peppers",
    "tomato": "tomatoes", "roma tomato": "roma tomatoes",
    "turnip": "turnips",
    "ribeye steak": "ribeye steaks", "sirloin steak": "sirloin steaks",
    "salmon fillet": "salmon fillets",
    "green pepper": "green peppers", "red pepper": "red peppers",
    "orange pepper": "orange peppers", "habanero pepper": "habanero peppers",
    "parsnip": "parsnips", "plantain": "plantains",
    "green apple": "green apples", "fuji apple": "fuji apples",
    "tangerine": "tangerines", "apricot": "apricots", "fig": "figs",
    "persimmon": "persimmons", "lobster tail": "lobster tails",
}


def main() -> None:
    conn = get_connection()
    try:
        merged = 0
        renamed = 0
        skipped = 0
        for old, new in RENAMES.items():
            old_row = conn.execute(
                text("SELECT id FROM ingredients WHERE name = :name"),
                {"name": old},
            ).fetchone()
            new_row = conn.execute(
                text("SELECT id FROM ingredients WHERE name = :name"),
                {"name": new},
            ).fetchone()

            if old_row and new_row:
                old_id = old_row["id"]
                new_id = new_row["id"]
                conn.execute(
                    text("UPDATE recipe_ingredients SET ingredient_id = :new WHERE ingredient_id = :old"),
                    {"new": new_id, "old": old_id},
                )
                conn.execute(
                    text("UPDATE pantry SET ingredient_id = :new WHERE ingredient_id = :old"),
                    {"new": new_id, "old": old_id},
                )
                conn.execute(
                    text("UPDATE regulars SET ingredient_id = :new WHERE ingredient_id = :old"),
                    {"new": new_id, "old": old_id},
                )
                conn.execute(
                    text("DELETE FROM ingredients WHERE id = :id"),
                    {"id": old_id},
                )
                print(f"  merged: '{old}' (id={old_id}) -> '{new}' (id={new_id})")
                merged += 1
            elif old_row and not new_row:
                conn.execute(
                    text("UPDATE ingredients SET name = :new WHERE id = :id"),
                    {"new": new, "id": old_row["id"]},
                )
                print(f"  renamed: '{old}' (id={old_row['id']}) -> '{new}'")
                renamed += 1
            else:
                skipped += 1

        conn.commit()
        print(f"\nDone. merged={merged}, renamed={renamed}, skipped={skipped}.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
