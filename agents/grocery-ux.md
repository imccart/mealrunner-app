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

## Regulars + staples lifecycle

- **Regulars delete by ID.** `DELETE /regulars/{id}` uses integer ID, not name-in-URL (name-based path was unreliable).
- **Staple type prompt.** Adding a staple prompts "Every trip" vs "Keep on hand" before saving.
- **Both filter meal-driven items by presence.** "Every trip" regulars and "Keep on hand" pantry both get excluded from `build_grocery_list` output by canonical-name / ingredient-id match — so meal ingredients never auto-flow a staple onto the list. User adds them when they want: "Add my regulars" / "Check my staples." The `pantry` table has `quantity` / `unit` columns from an earlier inventory-tracking design that never got a UI; they're written as defaults (1.0 / count) and ignored on read. Don't reintroduce unit-blind quantity subtraction (recipes ask in tbsp/cups, pantry says 1.0 count → leaked items in pre-2026-05-11 builds).
- **Staple recategorize.** Hamburger icon on each staple opens a category picker.
- **Passive staple suggestion.** After "Have it" tapped 3+ times on the same item, suggest adding it as a staple.
- **Smart suggestions.** `last_bought_at` on `regulars` and `pantry`. Learning endpoint suggests removing regulars not bought in 4+ weeks (only if the regular itself is 4+ weeks old) and restocking staples not bought in 6+ weeks (only after first purchase tracked). No skip tracking — unselected regulars are simply not added to a trip.

## Buy elsewhere

Order page button marks an item as buying at another store. Item stays on grocery list but exits the ordering flow.

- Desktop sidebar: section headers (Active / Ordered / Buying elsewhere) — comparison toggle and send button live there too.
- Mobile: tappable header counts surface the same sections.

## Canonical normalization boundary

`normalize.py` normalizes user-typed names at every input boundary (grocery add, regulars, pantry, recipe ingredients, Kroger search). `compare_key` is the dedup boundary across all paths — see `agents/grocery-sync.md` for the full identity / dedup rules.
