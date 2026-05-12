# Grocery UX

User-facing behavior of the grocery list — distinct from `agents/grocery-sync.md`, which covers the sync engine. Read this for **what the user sees and does**; read grocery-sync for **how meal items get there and stay there**.

## List shape

- **Continuous list, no trips.** One list per user, accumulates with timestamps on `grocery_items` (keyed by `user_id`). Old checked extra/regular items auto-pruned after 3 days. Meal-sourced items are managed by `_refresh_trip_meal_items` (not pruned).
- **Per-household state** (order_source, regulars_added, pantry_checked, receipt_data) lives on a tiny `grocery_state` table keyed by the **owner's** user_id.
- **Always-live.** No Build My List flow. Meal ingredients flow on automatically. "Add my regulars" / "Check my staples" buttons sit at the top, always visible.
- **Synced on plan changes.** `GroceryPage` remounts via `groceryVersion` key when `PlanPage` data changes.

## Item states

- `active` (default), `bought`, `have-it`, `removed`, `buy-elsewhere`. Last two are columns on `grocery_items`.
- Checked/removed items disappear with a 24-hour "Recently checked" undo section; auto-pruned after 3 days.
- **Unified undo:** `POST /grocery/undo/{item_name}` resets any state back to active (clears all flags + product selection in one call).

## Tap-to-expand

Items show name + meals + ordered badge. Tap ☰ icon to expand action bar: **[Bought] [Have it] [Note] [Move] [×]**. Ordered items are fully interactive (just have a badge).

## Swipe + remove semantics

- **Swipe right = bought.** Remove is via the ☰ action bar only.
- **Remove behavior diverges by source:** extra/regular items are deleted from the trip; meal-sourced items get `removed=1` (prevents re-add by the next refresh). Same lifecycle as bought/have-it (24-hr undo, 3-day prune for extras/regulars only). No learning contribution either way.

## Walk the Aisles mode

Was "Shopping Now". Dark full-screen checklist for in-store use. `shoppingMode` toggle within `GroceryPage`.

- Tap or swipe right to check off.
- 36px text, sticky aisle headers, running count.
- Meal attribution + notes shown below each item.
- Wake lock keeps the screen on. Exit via "Done".

## Notes

Notes on meals (Plan action sheet) and grocery items (pencil icon / tap to edit). `notes` column on `meals` and `grocery_items`.

## Empty states

Groups with no active items are hidden. The all-done state shows the bent-spoon icon + "Nothing left to grab."

## Categories

Food aisles **plus** Personal Care, Household, Cleaning, Pets for non-food items.

## Staples lifecycle

- **One unified `staples` table.** From the user's view there's one list of "staples" — items they keep around or buy regularly. The legacy split into `regulars` / `pantry` tables was retired in commit `<staples-consolidation>` (2026-05-11). The `staples` table has a `mode` column with values `'every_trip'` or `'keep_on_hand'` that controls default-add behavior. Mutual exclusion (an item is in exactly one mode at a time) is enforced by table identity: one row per (user_id, ingredient_id).
- **Add via /staples POST.** Body: `{name, mode, shopping_group?, store_pref?}`. If the user already has a staple for the same ingredient (or same free-form name), `add_staple` updates the mode in place rather than creating a duplicate — toggling between modes is a property change, not a delete+add.
- **Delete by ID.** `DELETE /staples/{id}` uses the integer ID. The "don't re-suggest" signal goes to `learning_dismissed`.
- **Mode flip.** `PATCH /staples/{id}` with `{mode}` flips between `'every_trip'` and `'keep_on_hand'` on the same row. Replaces the old "Move to pantry" / "Move to regulars" remove+add dance — no churn on the row id, no race window where the staple briefly exists in neither place.
- **Both modes filter meal-driven items by presence.** `build_grocery_list` excludes ingredients in the user's `staples` table by both ingredient_id and compare_key on name — meal ingredients never auto-flow a staple onto the list. User adds them when they want: "Add my regulars" (mode='every_trip') / "Check my staples" (mode='keep_on_hand'). The legacy `pantry` table had `quantity` / `unit` columns from an inventory-tracking design that never got a UI; don't reintroduce unit-blind quantity subtraction.
- **Staple recategorize.** Hamburger icon on each staple opens a category picker; submits `PATCH /staples/{id}` with `{shopping_group}`.
- **Passive staple suggestion.** After "Have it" tapped 3+ times on the same item, suggest adding it as a staple (defaults to `keep_on_hand` since that's the "I have it" semantic).
- **Smart suggestions.** `last_bought_at` on `staples`. Learning endpoint suggests removing every_trip staples not bought in 4+ weeks (only if 4+ weeks old) and restocking keep_on_hand staples not bought in 6+ weeks (only after first purchase tracked). No skip tracking — unselected every_trip staples are simply not added to a trip.
- **Legacy `regulars` and `pantry` tables remain in the schema** for now (idempotent NOT EXISTS gates in the consolidation migration), but nothing reads from them. They can be dropped in a follow-up once we're confident no callers remain.

## Buy elsewhere

Order page button marks an item as buying at another store. Item stays on grocery list but exits the ordering flow.

- Desktop sidebar: section headers (Active / Ordered / Buying elsewhere) — comparison toggle and send button live there too.
- Mobile: tappable header counts surface the same sections.

## Canonical normalization boundary

`normalize.py` normalizes user-typed names at every input boundary (grocery add, regulars, pantry, recipe ingredients, Kroger search). `compare_key` is the dedup boundary across all paths — see `agents/grocery-sync.md` for the full identity / dedup rules.
