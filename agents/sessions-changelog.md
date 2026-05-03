# Sessions Changelog

Milestone-style log of significant shipped changes by session. Live rules and ongoing invariants live in the topic agents (`grocery-sync.md`, `database.md`, `deploy-workflow.md`); this file is **history**, not active guidance. Read it when you need context on **why** the code is shaped the way it is or **when** a particular pattern landed.

Cross-refs to topic agents are noted as → `<agent>`.

---

## Session 42 — Brand ownership + onboarding + staging
- **Brand ownership categories**: `brand_ownership.category` for brands owned by different parents in different product categories (e.g. Sara Lee: Deli→Tyson, Bakery→Bimbo). `get_parent_company()` accepts optional `category` hint from Kroger API.
- **User profile fields**: `users` table got `first_name`, `last_name`, `tos_accepted_at`, `tos_version`.
- **Onboarding flow** (5 steps): Who Are You (name/zip/household invites/TOS) → Meals+Sides → Staples → Regulars → Store Integration. Tour is a separate overlay on the real app after onboarding completes.
- **Staging workflow** → `deploy-workflow.md`.
- **Tagline**: "From planning to pantry."

## Session 54 — meal_ids + e2e harness + order-page polish
- **`grocery_items.meal_ids`** introduced (was `trip_items.meal_ids` pre-Phase A) → `grocery-sync.md`.
- **`grocery_items.receipt_acknowledged`** separates "user has acted on this receipt match via the receipt page" from `checked` (which can be set by grocery-list pre-checking). Receipt page is the canonical reconciliation step. Receipt match writes `receipt_acknowledged=0`; `/receipt/resolve` (any action) sets it to 1. Receipt page filters matched/substituted sections by `!receipt_acknowledged`.
- **E2E test harness** at `frontend/e2e/` → `deploy-workflow.md`.
- **Order page prior selections — authoritative availability**: `/order/search` does a per-UPC Kroger lookup for any pref UPC not in today's top-12 results. Drops prefs Kroger doesn't acknowledge (foreign-chain UPCs from receipts, discontinued SKUs). Out-of-stock and wrong-fulfillment items dropped instead of shown disabled. Whole enrichment block try/except'd — failures degrade to empty prior selections, never crash.
- **Order page end-state**: product cards / prior selections render guarded by `activeItem &&` so once everything is resolved the end-state panel shows alone.

## Session 58 — schema cleanup + sync narrowing + id-based grocery identity
Big multi-phase cleanup. The commits below are all referenced from `grocery-sync.md`.
- **Phase A — schema cleanup** (commit `1cf4794`): Dropped `grocery_trips` (vestigial). Replaced with `grocery_state` table keyed by owner's user_id (per-household state: order_source / regulars_added / pantry_checked / receipt_data). Renamed `trip_items` → `grocery_items` keyed off user_id directly. Dropped `UNIQUE(trip_id, name)`. Receipt_extra_items also rekeyed to user_id. Migration in `db.py:_migrate_grocery_trips_to_state` is idempotent.
- **Phase B — sync narrowing** (commit `847f02a` + revision `374f7eb`): `_refresh_trip_meal_items` only considers `source='meal'` rows when building existing_map. Three branches by meal_ids history (legacy / new occurrence / same occurrences) → `grocery-sync.md`.
- **Phase C — id-based grocery row identity** (commits `8ad4ae6` + `59cd743`): All grocery row mutations take an integer id instead of a name path/body param. → `grocery-sync.md`.
- **Phase D — receipt reconciliation polish** (commit `87b4449`): `/receipt` exposes id, source, for_meals (array), and notes per item. `/receipt/resolve` and `/receipt/match-extra` take `id` instead of name. New `ReceiptRowMeta` sub-component renders product thumbnail (44px), brand + size, for-meal attribution, notes, and the existing 'from receipt: …' subtext uniformly across matched and substituted sections.
- **`is_pantry_staple` is no longer a grocery filter** (commit `97e5b3f`): `build_grocery_list` no longer skips items based on the global `ingredients.is_pantry_staple` flag. The flag remains a hint for onboarding pre-fill and the "add to pantry?" suggestion. Resolved a long-standing "ingredient is in my recipe but never appears on my list" bug.
- **Recipe swap = new meal_id** (commit `eb54f6d`) → `grocery-sync.md`.
- **Clear-day affordance** (commit `eb54f6d`): "Clear this day" destructive button (trash icon) added to the meal action sheet, calls `api.removeMeal(date)`. Empties the slot vs. "Nothing needed" (sets to "Nothing Planned" freeform).
- **Hard-delete regulars** (commit `0430768`): `DELETE /regulars/{id}` actually deletes the row instead of `UPDATE active=0`. No more "purgatory". `learning_dismissed` already tracks "don't re-suggest" separately.
- **'Ordered' is not 'on list'** (commit `aefa74c`) → `grocery-sync.md`.
- **Stale-order TTL** (commit `15f6abc`): Initial implementation, hard-delete-everything. Revised in session 60 → `grocery-sync.md`.

## Session 59 — performance audit + move-a-meal redesign
- **Connection-release pattern for external HTTP** (commit `9fe941a`) → `database.md`.
- **Bulk-fetch in `build_grocery_list` and `swap_meal_smart` preview** (commit `981fa37`): One SELECT...IN for ingredients + one bulk pantry SELECT instead of `get_pantry_quantity` per ingredient. 7-meal × 2-side plan dropped from ~50 queries to 2. Same pattern in `swap_meal_smart` preview. New indexes: `recipe_ingredients(recipe_id)` and `pantry(user_id, ingredient_id)` (Postgres doesn't auto-index FK source columns).
- **Move-a-meal redesign** (commit `a4243e7`): Touch-only waffle drag handle on PlanPage rows removed. Sheet structure now has 3 views: **main** (Change meal · Ingredients · Cooking notes), **change** submenu (Different meal · Different sides · Move to a different day · ─── · Nothing planned · Clear day), **move** day picker. New global CSS classes `.sheet-back` and `.sheet-divider`. Cooking notes and Ingredients hidden when the day's meal is freeform (no `recipe_id`) — commit `0fec4c1`.

## Session 60 — stale-order split + compare_key dedup invariant
- **Stale-order TTL — soft-delete meal rows, hard-delete extras** (commit `752138c`) → `grocery-sync.md`.
- **Canonical-name dedup via `compare_key`** (commits `ff1197e`, `01e9f3a`, `9670125`, `4ef77c2`) → `grocery-sync.md`.
- **`resolve_user_canonical(conn, user_id, raw_name)`** (commit `5d03ba6` + `4ef77c2`) → `grocery-sync.md`.
- **Seed pluralization** (commits `5d03ba6` + migration `8f4c3d5`): 40 entries in `seed_ingredient_database.yaml` renamed to plural form where the user typically buys multiples (apples / bananas / oranges / lemons / limes / peaches / pears / plums / onions / potatoes / carrots / peppers / tomatoes, plus ribeye / sirloin steaks, salmon fillets, lobster tails). Mass nouns and single-purchase items stay singular. New seed entry `mini cucumbers`. `_seed_recipes` now builds a compare_key index of the ingredients table to tolerate singular/plural drift between recipe yamls and canonical seed. One-shot prod migration repointed FKs and deleted old singular ingredient rows.
- **Per-item quantity stepper** (commit `6a8857c`): Removed auto-derived meal_count `xN` badge. New `grocery_items.quantity` field surfaced via the action waffle (− N + / ✓). New `POST /grocery/quantity` clamps to [1, 99]. Display `apples x 4` next to the name on grocery list, Walk the Aisles, order page. Optimistic local update on tap (commit `4ef77c2`).
- **Dead Jinja templates removed** (commit `9670125`): `code/mealrunner/web/templates/` deleted (~1000 lines, leftover from before the React port).

## Session 61 — normalization fixes + write/read gotchas
- **Normalization no longer strips qualifiers** (commit `f8f8f59`) → `grocery-sync.md`.
- **`_parse_ts` accepts datetime, not just strings** (commit `e7a136d`) → `database.md`.
- **Optimistic-update rollback refetches from server** (commit `512b103`) → `grocery-sync.md`.
- **`CURRENT_TIMESTAMP::text` write to timestamptz column** (commit `db9ec74`) → `database.md`.
- **Dead `_prompt_state` removed** (commit `d29dfea`): Same broken `.replace("Z", ...)` pattern as the original `_parse_ts`, never called. Deleted to prevent copy-paste reuse.
- **Grocery swipe sensitivity tightened** (commit `1226b5b`): SWIPE_THRESHOLD bumped 50 → 80px; LOCK_THRESHOLD 8 → 12px; new HORIZONTAL_LOCK_RATIO=1.8 requires `|dx| > 1.8 * |dy|` to lock horizontal. Plus a final-motion check at touch-end. Catches the "started slightly right then went up/down" case.

## Session 62 — phantom-row bug class + skip-flag pattern
- **Phantom-row bug class** (commits `b5adfd1`, `bcb0204`, `d44a63a`) → `grocery-sync.md`. The receipt-tagged + ordered rows invisible-to-meal-sync class.
- **Skip-flag pattern** (commit `4842b26`): Five write endpoints (`/grocery/add-regulars`, `/grocery/add-pantry`, `/grocery/build`, `/order/select`, `/order/deselect`) called `_ensure_active_trip` themselves and then returned via `await get_grocery/order(request)` — the helper called it again. The second pass saw post-UPDATE state and was the source of the `/order/select` phantom-insert. Now `request.state._skip_ensure_active = True` set before the trailing `await get_*(request)`; helpers honor the flag. Stale-order TTL and prune cleanups still fire on the first pass — they're idempotent.
- **Swap-meal confirm DELETE is id-scoped + source='meal'** (commit `f8c77a1`): The confirm action's DELETE used `LOWER(name) = LOWER(:name)`. Post-Phase-A this catches every row with the canonical name — including manually-added `tomatoes` (source='extra') AND, worst case, an in-flight Kroger order row. Fix: preview SELECT tightened with `AND source='meal'`. `removable` response shape changed from `[name]` to `[{id, name}]`. Confirm DELETE scopes by `id AND user_id AND source='meal'`.
- **`/order/search` cache-read no longer overwrites live in-stock** (commit `8ed49c2`): Cache-read at `api.py:1839` was clobbering the live search response's mode-correct `in_stock` with a potentially-stale cached value. Fix: don't read in_stock/curbside from cache. Cache continues to supply price/promo/scores. Eliminates fulfillment-mode mismatch and 24-hour same-day inventory drift.
- **Quantity stepper collapsed into a tappable "x N" pill** (commit `4c14f55`): Action waffle was visually heavy. New `qtyEditing` state holds the item id being edited; pill expands into the existing − N + stepper plus a ✓ Done button. Action bar order: pill, Bought, Have it, Aisle, Note, ×.
- **Recipe-ingredient edits propagate lazily** → `grocery-sync.md`.

## Session 64 — multi-user prep + e2e expansion + legacy uniques
- **Legacy single-user UNIQUE-on-name constraints dropped** (commit `d517af5`) → `database.md`.
- **`/api/meals` shape mismatch fix in e2e helpers** (commit `4258d02`).
- **Grocery-longitudinal e2e suite** (commit `944f38a`) + `e2e-create-grocery-row` admin endpoint. Four tests covering bug classes that the per-meal-id refactor should keep passing across realistic accumulated state. (1) lifecycle bought + have-it + active-still on three rows of one meal, then receipt-tag the bought row and re-sync. (2) multi-occurrence: same recipe on 3 dates → meal_ids='A,B,C'; staging row to receipt='matched' meal_ids='A' should leave B,C uncovered. (3) regulars overlap. (4) long-tail accumulation: 20 stale receipt-matched rows don't pollute fresh meal sync.
- **Per-meal-id covered tracking** (commit `b498098`) → `grocery-sync.md`.

## Session 65 — `_refresh_trip_meal_items` refactor
- **6-phase pipeline of named helpers** (commit `39ea35c`) → `grocery-sync.md`. Same code, partitioned along bug-class boundaries. Verified against 11 scenarios; 17/17 e2e pass on staging and master.
