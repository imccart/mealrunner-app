# Grocery Sync

How the grocery list stays in sync with the meal plan, plus the dedup, state, and identity invariants that hold the whole subsystem together. This is the densest correctness boundary in MealRunner — every grocery bug from sessions 58 through 65 routed through here.

If you're touching `_refresh_trip_meal_items`, `grocery_items`, `/api/grocery`, `/grocery/add*`, or any optimistic-update grocery handler in the frontend, read the relevant section first.

## Quick reference

| Topic | Where the rule lives |
|---|---|
| `_refresh_trip_meal_items` pipeline | `code/mealrunner/web/api.py` — 6 named helpers (post session 65 refactor) |
| Per-meal-id coverage | `_load_covered_meal_ids` + `_effective_need_for` |
| `compare_key` (canonical dedup) | `mealrunner.normalize.compare_key` (Python) + `frontend/src/utils/compareKey.js` |
| Active-row filter | `checked=0 AND have_it=0 AND removed=0 AND skipped=0 AND submitted_at IS NULL` (and usually `buy_elsewhere=0`, `ordered=0`) |
| Stale-order TTL | `_ensure_active_trip` — meal rows soft-delete, non-meal rows hard-delete |
| `grocery_items` row identity | integer `id` only — no `(user_id, name)` unique exists |

---

## `_refresh_trip_meal_items` — 6-phase pipeline

Lives in `code/mealrunner/web/api.py`. Refactored from a 250-line monolith in session 65 (commit `39ea35c`) into named helpers, each mapping to a bug-class boundary the function had absorbed:

1. `_build_fresh_meal_items(conn, user_id, mw, resolve)` — derives `fresh_meal_items: dict[compare_key, {name, shopping_group, for_meals, meal_ids, meal_count}]` and `meal_id_to_name: dict[int, str]` from the active plan.
2. `_load_meal_sync_existing(conn, user_id)` — the existing_map SELECT. Excludes `ordered=1`, `submitted_at IS NOT NULL`, non-empty `receipt_status`. Includes have_it/checked/removed rows. ORDER BY `(have_it+checked+removed) ASC, id DESC` so first-write-wins puts active rows first when a canonical name has multiple states.
3. `_load_covered_meal_ids(conn, user_id, all_active_meal_ids)` — the per-meal-id coverage SELECT. SELECTs rows that are owned by another flow (ordered+unreconciled OR non-empty receipt_status). For each, intersects its `meal_ids` with `all_active_meal_ids`. Returns `dict[compare_key, set[int]]`.
4. `_effective_need_for(info, covered_for_key, meal_id_to_name)` — pure. `eff_ids = info["meal_ids"] - covered_for_key`. Returns `_EffectiveNeed(meal_ids, for_meals, meal_ids_str, count)`.
5. `_delete_phantom_meal_rows(...)` — two-pass collect-then-delete; mutates existing_map.
6. `_drop_orphaned_meal_rows(...)` — deletes meal-source rows whose meal left the plan; clears meal fields on stale extras.
7. `_apply_meal_sync(...)` — INSERT/UPDATE with the 3-branch logic (see below).

The orchestrator is a 30-line pipeline that mirrors its docstring. **Don't re-inline** — each helper exists to make a specific bug class reviewable in isolation.

### The two filters are intentionally distinct

`existing_map` answers **"can meal-sync mutate this row?"** — must NOT include rows owned by other flows.

`covered_keys` (`covered_meal_ids_by_key`) answers **"is the canonical name already covered for this meal?"** — MUST include any row that actively serves the canonical name for a meal currently on the plan.

A row can be in `covered_keys` without being safe to mutate. Don't merge them.

### Why this pattern exists

Half a dozen "items reappearing on the grocery list" + the inverse "items not appearing" reports route through this. Concrete history:

- `/order/select` flips a row to `ordered=1`. existing_map excludes it. Without covered_keys, meal-sync inserts a phantom on the trailing `get_order` pass.
- Receipt processor tags a row `receipt_status='matched'`/`'not_fulfilled'`/etc. existing_map excludes it. Without covered_keys, meal-sync inserts a phantom on the next refresh.
- 2026-05-03 (feedback id=108): User added "Frozen Pizza Night"; March-29 receipt-matched rows for "frozen pizza" + "edamame" with `meal_ids=''` (legacy) were in covered_keys, so meal-sync skipped insert → grocery list stayed empty for these ingredients. Fix: covered_keys requires `meal_ids` intersection with active plan.

---

## Per-meal-id coverage (NOT per-name)

The data structure is `covered_meal_ids_by_key: dict[str, set[int]]` — canonical name → set of meal_ids that are already covered, intersected with the active plan.

```
effective_need = fresh_meal_items[key].meal_ids - covered_meal_ids_by_key.get(key, set())
```

INSERT only when `effective` is non-empty. Use effective for the new row's `meal_ids` and `for_meals`.

**Without per-meal-id tracking,** "I bought X for meal A" silently blocks "I added meal B that also wants X" — the user-facing reason `meal_ids` was added in session 54 in the first place.

### Active-plan intersect is load-bearing

A covering row only counts when its `meal_ids` set intersects with the union of all currently-active plan meal_ids. **Don't remove this gate.**

Without it, receipt-tagged rows from prior meal occurrences silently block the same meal name from re-populating its ingredients when the meal is re-added later. Pre-Phase-B legacy rows (`meal_ids=''`) likewise have empty intersection with anything → never cover.

`all_active_meal_ids = set(meal_id_to_name.keys())` derived in the orchestrator and passed into `_load_covered_meal_ids`.

### Branch 2 detection uses effective, not full

In `_apply_meal_sync`'s 3-branch UPDATE logic:

- Branch 1: legacy migration (old `meal_ids` empty) — no reset.
- Branch 2: new uncovered occurrence (`eff.meal_ids - old_meal_ids` non-empty) — reset per-buy state.
- Branch 3: same occurrences (`eff ⊆ old`) — preserve all state.

Branches 1 and 3 collapse to the same UPDATE; only Branch 2 injects the reset clause. Detection runs on **effective**, not full fresh_need. Otherwise rows just inserted with the uncovered subset get reset on the next sync because full fresh_need always contains the covered ids too.

### Phantom-cleanup tightens to "effective is empty"

A row is phantom only when ALL of its fresh_need is covered. Pre-Phase-B logic (`name in covered_rows`) was over-aggressive.

### Source != 'meal' attach branch keeps FULL fresh_need

User-added rows aren't auto-stripped of meal context just because something else also covers the meal. Only the `source='meal'` UPDATE/INSERT branches use effective.

### When you add a new "row owned by another flow" state

If you add a new `receipt_status` value or new state column that means "this row should not be re-added by meal-sync," it must be added to BOTH:

1. The `_load_meal_sync_existing` SELECT filter (so meal-sync doesn't try to mutate it).
2. The `_load_covered_meal_ids` SELECT (so meal-sync's insert branch skips when the canonical name is covered).

Today's covered_keys filter is `(ordered=1 AND submitted_at IS NULL AND COALESCE(receipt_status,'')='') OR COALESCE(receipt_status,'') != ''` — new receipt_status values are covered for free, but verify.

**Symptoms of forgetting:**
- Skip #1 (no covered_keys gate): items reappear on grocery list (phantom siblings).
- Skip the active-plan intersect: items silently fail to appear (stale rows block fresh inserts).

The phantom-cleanup sweep self-heals once `covered_keys` is updated.

---

## `compare_key` — the canonical dedup boundary

Any place that asks "are these two grocery item names the same?" should use `compare_key(name)`, not `name.lower()`.

The dedup invariant: **at most one active row per canonical item name across all sources.**

`compare_key` collapses singular/plural variants but **NOT qualifiers** ("soy milk" ≠ "milk", "cauliflower rice" ≠ "cauliflower").

### Python and JS in sync

- Backend: `from mealrunner.normalize import compare_key`
- Frontend: `import { compareKey } from '../utils/compareKey'`
- 15/15 test parity. If you change `_depluralize` rules or `compare_key` shape on one side, mirror to the other.
- Both have `_VES_TO_F` allowlist (loaves/leaves/halves/calves/shelves/thieves/knives/wives/lives) and the same `-es` rule (only strip `-es` when stem ends in `sh/ch/x/z/ss/o`).

### Where it's called

**Python:**
- `/grocery/add`, `/grocery/add-staples` (mode=every_trip|keep_on_hand) — active-row existence checks
- `_refresh_trip_meal_items` — keys both `existing_map` and `fresh_meal_items`
- `build_grocery_list` — filters regulars
- `feedback.py`, `/learning-suggestions` — filters regulars exclusion
- `_seed_recipes` — plural-aware lookup index of the ingredients table

**JS:**
- `onListSet` — staples-picker badge, autocomplete exclude, duplicate-add warning
- `fuzzyMatch.fuzzyFilter` — `exclude.has(compareKey(candidate))` works directly with a Set of compareKeys

### `resolve_user_canonical(conn, user_id, raw_name)`

When `/grocery/add` gets a freeform name with no canonical seed match, this scans the user's full grocery_items history for a same-compare_key row and uses that row's display name. So "mini cucumber" typed today resolves to "mini cucumbers" if that's what the user used last week, even if that older row is checked off. ORDER BY `id DESC` for determinism.

Only called when `_normalize_name` returned no canonical seed match (`ingredient_id is None`); seed-canonical names short-circuit.

### Don't undo

- Don't go back to exact `LOWER(name) = LOWER(:name)` matching — plural blindness silently creates duplicates.
- Don't strip qualifiers in `compare_key` — they're different products at the store.
- Don't auto-pluralize at display time. The seed dictates display form; `compare_key` handles user-typed variants.

### Pre-session-61 trap (do not re-introduce)

`normalize_item_name` step 5 (bidirectional substring containment) silently mapped "cauliflower rice" → "cauliflower" because "cauliflower" was a 0.69-ratio substring. Same path caused "almond milk" → "milk", "chocolate chips" → "chocolate", "pepper" → "pepperoni". Step 5 was removed entirely in commit `f8f8f59`. Steps 1–4 (exact, plural variants, compact, fuzzy word-overlap with 0.7 threshold) cover principled cases.

---

## `meal_id` stability — move via slot_date, not attribute swap

When the user reorders / swaps / moves meals on the plan, prefer:

```sql
UPDATE meals SET slot_date = :new_date WHERE id = :id
```

Don't swap `recipe_id` / `recipe_name` / sides between two existing meal rows.

**Why:** `_refresh_trip_meal_items` keys occurrence detection off the `meal_ids` set on each grocery row. If meal_id 42 had Chicken and you swap recipe attributes with meal_id 43 (Tacos), Chicken now lives on meal_id 43 — its diff is `{43} - {42}` = non-empty, so Branch 2 fires and clears `checked` / `have_it` / `removed` / `receipt_*` / `product_*` / `selected_at` / `ordered_at`. The user's bought ingredients re-surface as un-bought.

Date-swap keeps each meal_id glued to its recipe, so `meal_ids_by_name` is identical pre/post and state is preserved.

Fired in feedback id=106 (session 56).

**Apply to:** drag handlers, undo, restore-from-history, batch reschedule. Sides are FK'd to `meal_id` via `meal_sides` so they follow automatically.

**Recipe swap is the exception:** session 58 commit `eb54f6d` made `planner.set_meal` / `set_freeform_meal` DELETE-and-INSERT (CASCADE drops `meal_sides`) when the recipe at a date changes. The fresh `meal.id` makes grocery sync see a new occurrence and reset state from scratch — the prior recipe's bought claims correctly don't leak. Same recipe re-saved (sides change) still UPDATEs in place.

---

## `grocery_items` has NO `(user_id, name)` unique constraint

Dropped in Phase A (session 58, commit `1cf4794`). Multiple rows for the same user can share a name as long as their states differ — typically one active + N completed (have_it / checked / removed / ordered).

### Endpoints take row id, not name

```
/grocery/toggle/{id}             /grocery/have-it/{id}
/grocery/buy-elsewhere/{id}      /grocery/undo/{id}
/grocery/item/{id}  DELETE       /grocery/note  body {id, notes}
/grocery/recategorize  body {id, shopping_group}
/receipt/resolve  body {id, status}
/receipt/match-extra  body {extra_name, grocery_id, ...}
```

Look up by `id AND user_id` for ownership.

### Active-row filter

Anywhere computing "what's on the user's current list":

```sql
WHERE checked = 0
  AND have_it = 0
  AND removed = 0
  AND skipped = 0
  AND submitted_at IS NULL
  -- and usually:
  AND buy_elsewhere = 0
  AND ordered = 0   -- depending on whether you mean "active" or "in-flight"
```

Common partial filters that have leaked rows in the past:
- `WHERE product_upc != '' AND product_price IS NOT NULL AND submitted_at IS NULL AND removed=0 AND buy_elsewhere=0` — **missing checked/have_it/skipped**
- `WHERE product_upc != '' AND ordered=1 AND submitted_at IS NULL` — relies on `ordered=1` not being cleared, which it usually is by sync state-reset, but stale rows where `ordered=1` lingers can still leak

Bug fixed in session 57 commit `f0381cb`: `/order/price-comparison` reported "comparing 40 of 53 items" when the user had ~25 in their actual order — the count query was missing `checked/have_it/skipped`.

### Backend filters items_by_group to active rows; frontend MUST trust it

`get_grocery` only includes rows with `have_it=0 AND checked=0 AND removed=0` in `items_by_group`. The `checked` / `have_it` / `removed` name lists in the same response are for the recently-checked section and "ordered" badges, NEVER for filtering active rows on the frontend.

Regression in session 58 — name-filtering on the frontend hid a fresh active row when a stale completed row shared the name. Fix: render `items_by_group` directly.

### `/grocery/add` partial-unique semantics

If there's already an active row whose `compare_key` matches (`have_it=0 AND checked=0 AND removed=0 AND ordered=0 AND submitted_at IS NULL`), no-op. Otherwise insert a new `source='extra'` row.

`compare_key` collapses plural/singular: "apple" doesn't add a second row when "apples" is already on the list.

### Don't add `UNIQUE(user_id, name)` back

If you need partial uniqueness (one active per name), use a partial unique index, not a global one.

---

## "Ordered" rows are in-flight, not on-list

A row is "on the active grocery list" only when:
```
checked=0 AND have_it=0 AND removed=0 AND ordered=0 AND submitted_at IS NULL
```

`ordered=1` (user picked a Kroger product) and `submitted_at IS NOT NULL` (sent to Kroger) mean the row is in the **online order pipeline** awaiting receipt reconciliation. From the user's perspective, that's separate from the active grocery list — they don't need to buy it again, it's coming via Kroger.

### State distinctions

- `ordered=1, submitted_at=NULL` — user picked product on Order page, hasn't sent yet
- `ordered=1, submitted_at=set` — sent to Kroger, awaiting receipt
- `checked=1` — bought (in-store or post-receipt match)
- `have_it=1` — user has on hand, doesn't need to buy
- `removed=1` — user explicitly doesn't want this
- `receipt_status` ∈ `{matched, substituted, not_fulfilled, dismissed}` set when reconciliation happens

### Frontend `onListSet` excludes orderedSet

Drives the regulars/staples prompt, default-checked state, grocery-add duplicate warning, autocomplete exclude. Ordered items appear as autocomplete suggestions and default-checked in regulars prompt — ready to re-add.

### Backend `/grocery/add*` excludes ordered/submitted

Active-existence checks include `AND ordered=0 AND submitted_at IS NULL`. Re-adding while an order is pending creates a fresh sibling row instead of no-op-ing — same multi-row-per-name model as Phase C's multi-product feature.

### Don't auto-revert ordered → active

The user explicitly pushed back on autonomous reappearance — an unreconciled stale order should NOT come back onto the active list. Hard-delete the row instead. User can re-add manually if they actually still need it.

---

## Stale-order TTL splits by source

In `_ensure_active_trip`, the 3-day stale-order TTL:

- `source='meal'` → soft-delete via `checked=1, checked_at=submitted_at` (old timestamp keeps it out of the 24hr Recently-checked panel), clear ordered/submitted/product fields. **The row stays in the table.**
- `source != 'meal'` → hard-delete (the original session-58 behavior).

Both gated on `COALESCE(receipt_status, '') = ''` so receipt-tagged rows are preserved.

**Why the split:** original session-58 implementation hard-deleted everything. For meal-source rows, that broke `_refresh_trip_meal_items` — the missing row looked like a fresh need and got re-INSERTed on the next sync, causing already-bought meal ingredients to reappear. Non-meal rows aren't touched by sync, so hard-delete is safe.

**`resolve_receipt_item` recover/not_fulfilled branches must clear `checked=0, checked_at=NULL`** because the soft-delete may have set them. Otherwise a late receipt for a soft-deleted meal row leaves it stuck in checked state. Late receipts still match because `receipt_status=''` is matchable regardless of `checked` — don't add a `WHERE checked=0` filter to receipt-matching code.

---

## `/api/grocery` payload — `items_by_group` includes ordered rows

`/api/grocery`'s `items_by_group` buckets contain `ordered=1` rows. The active-vs-not filter only excludes `checked / have_it / removed / receipt_status in (matched, substituted, dismissed)`. It does NOT exclude `ordered=1`. Those rows appear in `items_by_group` AND get their name appended to the separate `ordered` array. The frontend filters at render (`GroceryPage.jsx` — `if (isOrdered) return null`).

**Tests / scripts asserting "is this row hidden from the user's grocery view?"** need: `(name in items_by_group) === (name in ordered)`. If both, it's an ordered row, frontend filters. If only `items_by_group`, it's truly active.

**Phantom-row bug class assertion:** count of `items_by_group` rows for that name = 1, name is in `ordered`.

If you ever change the API to filter ordered server-side, the frontend's `if (isOrdered) return null` becomes dead code, and so does the `ordered` array consumer downstream — sweep both before flipping the contract.

---

## Optimistic-update rollback refetches from server

Any frontend handler that does `const prev = state; setState(optimistic); try { await api.X() } catch { setState(prev) }` is wrong under concurrency. Use:

```js
const rollback = async (prev) => {
  try { setGrocery(await api.getGrocery()) }
  catch { setGrocery(prev) }
}
```

**Why:** When the user fires action A, then action B before A returns:
1. Handler A: `prev_A = current state`, optimistic remove A, call API_A.
2. Handler B (closure-captured AFTER A's setState): `prev_B = state with A already removed`. Optimistic remove B, call API_B.
3. API_A fails → `setState(prev_A)` restores both A and B as active (B's optimistic update was lost — momentary visual glitch).
4. API_B fails → `setState(prev_B)` restores state where A is *missing*. A vanishes from active list. Server never marked it as bought. Item is gone.

Refetch sidesteps the problem because the server is the single source of truth.

**Apply to:** any new optimistic-update path in the frontend. The 5 spots in `GroceryPage.jsx` that hold to this convention (as of session 61): `handleItemAction` bought/have_it/remove, `handleQtyChange`, `handleShopUncheck`, `handleUndoRecent`.

The error toast still fires on the underlying API failure — the rollback fix doesn't suppress it.

**Don't:** wrap the rollback in another try-around-`setState(prev)`. `setState` doesn't throw. The failure mode is the refetch's `await api.getGrocery()` rejecting (offline, 5xx).

---

## Recipe-ingredient edits propagate lazily

`POST /recipes/{id}/ingredients` and `DELETE /recipes/{id}/ingredients/{ri_id}` deliberately do NOT call `_refresh_trip_meal_items`. The next `GET /grocery` (any tab switch, any other meal-mutation endpoint) triggers the refresh naturally via `_ensure_active_trip → _refresh_trip_meal_items`.

Considered in session 62 and accepted. Lazy refresh keeps the recipe endpoints cheap and avoids dragging meal-week loading into a per-ingredient mutation.

Meal-set / meal-swap / meal-clear endpoints DO refresh proactively because they often happen from the Plan tab where the user is about to look at grocery; recipe-ingredient edits happen from MealIngredientsSheet / MyKitchenSheet detail views and the user typically navigates back through Plan or Grocery anyway.

**Side effect, accepted:** when an ingredient is removed from a recipe, the next refresh DELETEs the existing meal-source row regardless of state — including `bought` / `have_it`. The user's prior buy/have claim silently vanishes. Defensible: removing an ingredient is a statement that it's no longer part of the meal, so a prior decision about buying/having it for this meal is no longer relevant. Not worth a confirmation prompt.

If a future complaint surfaces "I edited spaghetti and the grocery list didn't update," the fix is the user navigating to /grocery — not adding a refresh call. Verify the frontend is re-fetching on tab switch before changing backend behavior.
