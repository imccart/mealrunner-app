"""One-time dedup of duplicate active grocery_items rows.

Background: before the canonical-name fix, two rows could end up active for
the same item — e.g. "apple" added as a regular plus "apples" pulled in by a
meal recipe. The fix prevents new dups, but existing rows need a one-shot
merge.

Run once per environment: `railway run python scratch/dedup_grocery_items.py`
(or `python scratch/dedup_grocery_items.py` locally with DATABASE_URL set).

For each (user_id, compare_key) group of >1 active rows, keeps the oldest row
(lowest id), merges `for_meals` / `meal_ids` from the others, deletes the
others. Non-active rows (have_it / checked / removed / ordered / submitted)
are left alone — they're history, not on the active list.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

from sqlalchemy import text  # noqa: E402

from mealrunner.database import get_connection  # noqa: E402
from mealrunner.normalize import compare_key  # noqa: E402


def main() -> None:
    conn = get_connection()
    try:
        rows = conn.execute(
            text("""SELECT id, user_id, name, source, shopping_group,
                           for_meals, meal_ids, meal_count, notes, added_at
                    FROM grocery_items
                    WHERE have_it = 0 AND checked = 0 AND removed = 0
                      AND ordered = 0 AND submitted_at IS NULL
                    ORDER BY user_id, id""")
        ).fetchall()

        groups: dict[tuple[str, str], list[dict]] = {}
        for r in rows:
            key = (r["user_id"], compare_key(r["name"]))
            groups.setdefault(key, []).append(dict(r))

        merged_count = 0
        deleted_count = 0

        for (user_id, ckey), group in groups.items():
            if len(group) <= 1:
                continue

            keep = group[0]  # oldest by id (rows ordered by id ASC)
            losers = group[1:]

            keep_for_meals = {m for m in (keep["for_meals"] or "").split(",") if m}
            keep_meal_ids = {x for x in (keep["meal_ids"] or "").split(",") if x.strip().isdigit()}
            keep_notes = (keep["notes"] or "").strip()

            for loser in losers:
                for m in (loser["for_meals"] or "").split(","):
                    if m:
                        keep_for_meals.add(m)
                for x in (loser["meal_ids"] or "").split(","):
                    if x.strip().isdigit():
                        keep_meal_ids.add(x)
                ln = (loser["notes"] or "").strip()
                if ln and ln not in keep_notes:
                    keep_notes = (keep_notes + " | " + ln).strip(" |") if keep_notes else ln

            new_for_meals = ",".join(sorted(keep_for_meals))
            new_meal_ids = ",".join(sorted(keep_meal_ids, key=lambda x: int(x)))
            new_meal_count = len([m for m in keep_for_meals if m])

            conn.execute(
                text("""UPDATE grocery_items SET
                          for_meals = :for_meals, meal_ids = :meal_ids,
                          meal_count = :meal_count, notes = :notes
                       WHERE id = :id"""),
                {"for_meals": new_for_meals, "meal_ids": new_meal_ids,
                 "meal_count": new_meal_count, "notes": keep_notes,
                 "id": keep["id"]},
            )
            merged_count += 1

            for loser in losers:
                conn.execute(
                    text("DELETE FROM grocery_items WHERE id = :id"),
                    {"id": loser["id"]},
                )
                deleted_count += 1

            print(
                f"  user={user_id[:8]}.. key={ckey!r} kept id={keep['id']} "
                f"(name={keep['name']!r}) merged {len(losers)} dup(s): "
                + ", ".join(f"id={l['id']} (name={l['name']!r})" for l in losers)
            )

        conn.commit()
        print(f"\nDone. Merged {merged_count} group(s), deleted {deleted_count} duplicate row(s).")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
