# MealRunner — Product & Design Handoff
*Context for Claude Code from strategy/design session · March 2026*

---

## What MealRunner is

A meal planning and grocery logistics app. The key framing: **shopping logistics with meals as the input** — not a recipe app. Users generally know what they're going to cook and how to cook it. MealRunner helps them plan meals, build a grocery list from that plan, order what they need, and reconcile what they actually received.

Parent company: **Aletheia** (transparency/values layer, methodology home). Consumer product: **MealRunner**.

---

## Core functionality

1. **Meal planning** — a simplified, real-life style planner. Not recipe-centric. Meal (not recipe) is the atomic unit. Rolling 10-day window (today + 9 days), not a fixed "week of X." Past 7 days viewable via toggle (read-only, greyed).

2. **Grocery list tied to the plan** — auto-generated from meals marked for the list via per-meal `on_grocery` toggle, plus user-added items (regulars, pantry items, one-offs). No quantities — user knows how much they need.

3. **Ordering** — from unchecked grocery list items, select Kroger products, hand off to Kroger to complete. NOVA scores and brand ownership info surface at point of selection, not preachy, just present.

4. **Receipt reconciliation** — match delivered items against what was ordered. Surface substitutions and out-of-stocks. Decisions at this step feed the learning layer. (Placeholder UI built; full reconciliation flow not yet implemented.)

5. **Learning from history** — what meals are cooked regularly, what items consistently appear, substitution patterns, recency of meals. Learning suggestions surface during the Build My List flow (regulars step), not while shopping. Items bought on 3+ of last 5 trips are suggested as regulars.

---

## Navigation — 4 tabs

**Plan / Grocery / Order / Receipt**

This is a linear flow that also works as a nav. Each tab is a distinct job, in the order you'd actually use them in a given week.

- **Plan** — build the meal plan
- **Grocery** — manage and check off the active list
- **Order** — select products for unchecked items, hand off to Kroger
- **Receipt** — reconcile what was delivered

No floating + button. No extra nav chrome. Everything is reachable through the four tabs. Bottom nav on mobile, top nav on desktop.

**Preferences overlay:** accessible via bent spoon icon in the nav bar. Bottom sheet with accordion sections (Stores, Regulars, Pantry, Transparency, Integrations, About). Not a page — an overlay.

---

## The plan tab

- Rolling 10-day calendar view — each day as a row (today + 9 days)
- Past 7 days viewable via toggle (read-only, greyed out)
- Filled meal rows show the meal name, side dish, and `on_grocery` toggle
- **Tap filled row** → action sheet bottom sheet (Replace / Change Side / Eating Out / Leftovers / Remove)
- **Hold + drag** → reorder meals between days (swap dates). Long-press (400ms) initiates drag on mobile.
- Empty day rows are tappable → opens meal picker bottom sheet
- **Freeform meals** — "Eating Out" and "Leftovers" are first-class meal states with no recipe, auto-excluded from grocery tracking
- **Side picker** — pill-based side selection with search and freeform input, via bottom sheet
- **Smart swap** — replacing a meal cross-references ingredients against all on-list meals before suggesting removal, then offers to add new meal's ingredients
- **"Build My List"** — floating FAB (fixed position, bottom-right), creates a new grocery trip via multi-step flow, then navigates to Grocery tab
- **"Fresh Start"** — secondary ghost button, erases current meals with staggered wipe animation, suggests new plan
- Status bar shows plain-language summary of plan state

---

## Build My List — the list creation trigger

"Build My List" is the moment that creates a discrete list object. Planning itself can happen gradually over days — MealRunner waits for the intentional act of "I'm ready to shop."

When tapped, opens a multi-step bottom sheet flow:
1. **Carryover** — check for unchecked items on the prior active trip. If found, show "Still need these?" with checkboxes pre-selected. User picks what to carry over (or skips).
2. **Regulars** — checklist of recurring items. Learning suggestions appear here: items bought on 3+ of last 5 trips that aren't already regulars get an "Add to regulars?" prompt.
3. **Pantry** — checklist of pantry items that may need restocking.
4. **Build** — new trip created with items from four sources: meal ingredients, carryover, regulars, pantry. Navigates to Grocery tab on completion.

The plan and the list are decoupled in time — you can plan Monday through Wednesday and build on Wednesday night, or plan the whole week Sunday. The app doesn't care.

---

## The grocery tab

- Shows the active grocery trip
- Items grouped by category (Produce, Meat, Dairy & Eggs, Bread & Bakery, Pasta & Grains, etc.)
- Each item shows: check circle, item name, multi-meal badge ("x2"/"x3") if ingredient appears in 2+ meals
- **Item states:**
  - Unchecked — needs to be bought (empty circle)
  - Checked (green checkmark) — bought in store
  - Ordered (accent up-arrow) — selected in Order tab, awaiting delivery. Not toggleable.
- Footer: "Add item..." input with autocomplete from full item history + "+" button
- All item names normalized to lowercase at the API layer
- No "Select Products" button here — that's the Order tab's job

**Mid-week additions** are just items added directly to the active list via the add input. No new trip, no new mode. It's the same list.

---

## The order tab

- Pulls from **unchecked items on the active grocery list** — this is the source of truth
- As items get checked off in person in Grocery, they disappear from Order automatically
- Queue strip shows pending items and already-selected items
- Auto-search triggers when an item is selected from the queue
- Product preferences from prior orders surface first (recency-weighted, last 3 orders)

**Product cards show:**
- Product image
- Product name, brand, size, price
- NOVA score badge (1-4, color coded green to red)
- Nutri-Score grade (A-E)
- Brand ownership (e.g., "Owned by Hormel Foods") — planned, not yet implemented

Product selections stored on `trip_items` table (product_upc, name, brand, size, price, image columns). Batch submit to Kroger cart via API.

---

## The receipt tab

- Populated after a Kroger order is delivered
- Three sections: **Matched / Substituted / Not fulfilled**
- Each substitution gets a resolution prompt: "That's fine" or "Note for next time"
- Out-of-stock items are automatically returned to the grocery list
- Resolving the receipt feeds the learning layer — what you accepted, what you flagged
- "Close Receipt" finalizes the reconciliation

**Status:** placeholder UI exists. Full reconciliation flow not yet implemented.

---

## The grocery trip data model

One active grocery list at a time. A list has items from four sources:

1. **Meal ingredients** — auto-added when "Build My List" runs for on-list meals (source: `meal`)
2. **Carryover** — unchecked items from previous trip, selected during build flow (source: `carryover`)
3. **Regulars** — recurring items selected during build flow (source: `regular`)
4. **Pantry** — pantry items selected during build flow (source: `pantry`)
5. **Extras** — freeform additions via the grocery tab add-item field (source: `extra`)

All stored in one table with a `trip_id`. The list doesn't care where items came from — everything shows up grouped by category.

**Trip lifecycle:**
- Active until user starts a new trip (via "Build My List")
- Old trip archived as-is, checked and unchecked items preserved
- Unchecked items surface in carryover prompt at next Build My List

**Trip tables:**
```
grocery_trips: id, created_at, completed_at, status
trip_items: trip_id, name, shopping_group, source (meal/carryover/regular/pantry/extra),
            checked, checked_at, ordered, ordered_at,
            product_upc, product_name, product_brand, product_size, product_price,
            product_image, selected_at
```

**Learning queries from trip history:**
- Frequency of items → suggest as regulars (surfaced in Build My List flow)
- Recency of meals → surface in meal picker ("haven't had this in a while")
- Substitution patterns → smarter product defaults
- Unchecked items → what didn't get bought and why

---

## Onboarding

4-step full-screen flow for first-time users:
1. **Store** (required) — select shopping store
2. **Meals** (skippable) — seed initial meal plan
3. **Regulars** — checklist of common recurring items
4. **Pantry** — checklist of pantry items

Marker file `~/.mealrunner/onboarding_complete` gates the flow. Existing users auto-skip.

**Future:** Claude API conversation replaces forms — user describes meals naturally, Claude extracts structured data.

---

## Design system — Warm Editorial

```css
--cream: #FAF7F2
--warm-white: #F5F0E8
--tan: #E8DDD0
--brown: #8B6F5E
--dark: #2C2420
--accent: #D4623A        /* terracotta */
--accent-light: #F0A882
--accent-bg: #FFF5F0
--green: #4A7C59
--green-light: #E8F0EB
--text-muted: #9B8B80
--text-light: #C4B8B0
```

**Fonts:** Playfair Display (headings, logo) + DM Sans (body, UI)

**Typography:** 16px base, 44px minimum touch targets. Playfair Display for headers and brand elements.

**NOVA badge colors:**
```
NOVA 1: bg #E8F0EB, text #4A7C59  (green — minimally processed)
NOVA 2: bg #F0F4DC, text #7A8C3A  (yellow-green)
NOVA 3: bg #FDF0DC, text #C47F2A  (amber)
NOVA 4: bg #FDECEA, text #C43A2A  (red — ultra-processed)
```

**Layout:**
- Mobile: single column, bottom tab nav
- Desktop (1024px+): two-column layout — plan left (flex:3), grocery sidebar right (flex:2, sticky). Top tab nav.
- Bottom sheets: `.sheet-overlay` + `.sheet` CSS + `useSwipeDismiss` hook for swipe-down dismiss. Desktop sheets max-width 700px.

---

## Key design decisions

**On the + button:** Removed entirely. Every action it was covering is reachable through the main tabs. Adding a meal → tap empty day row. Adding to list → Grocery tab add-item field. Building a new list → "Build My List" FAB. The + button was solving a navigation problem that doesn't exist.

**On list creation:** "Build My List" is the intentional trigger — not automatic. You can plan gradually over days and build when ready. This creates a discrete trip object with a clear start time. The FAB floats above bottom nav on mobile; bottom-right on desktop.

**On the grocery/order relationship:** Grocery is the source of truth. Order derives from unchecked items. Checking something off in Grocery removes it from Order. Ordering something in Order marks it as "ordered" in Grocery (not checked — it hasn't arrived yet). These are distinct states.

**On Receipt vs History:** Receipt is an active step in the weekly workflow, not a passive archive. History lives inside the data layer, surfacing in the meal picker and learning features — it's not a top-level tab.

**On mid-week shopping:** Not a separate mode. Just add items directly to the active list in the Grocery tab. Same list, same trip, no new concept required.

**On regulars/staples:** Merged into a single `regulars` system. Regulars are recurring items the user checks each grocery run, managed via the Preferences overlay. They never appear as meal ingredients — filtered out in grocery list building. The add-item autocomplete also pulls from full purchase history for quick additions.

**On per-meal grocery control:** Each meal has an `on_grocery` boolean toggle. Grocery list only builds from toggled meals. No bulk "accept plan" step — granular control per meal.

**On preferences:** Not a page. A bottom sheet overlay toggled by the bent spoon icon in the nav. Accordion sections keep it organized without adding navigation weight.

---

## Tech stack

- **Backend:** Python 3.10+, FastAPI, SQLAlchemy Core (PostgreSQL-compatible)
- **Frontend:** React + Vite, served at `/app` by FastAPI in production
- **Database:** SQLite locally (`~/.mealrunner/mealrunner.db`), PostgreSQL in production. `DATABASE_URL` env var for connection.
- **APIs:** Kroger (product search, pricing, cart), Open Food Facts (NOVA, Nutri-Score), Google Sheets (grocery export)
- **CLI:** Click + Rich (kept for debug/admin, web-first development)
- **Deployment:** Railway-ready (Procfile, railway.toml, health check endpoint)
- **Dev setup:** Vite dev server at `:5173` proxies `/api` to FastAPI at `:8000`.

---

## What's been built

- **React + Vite frontend** — complete, production build served at `/app`
- **4-tab navigation** — Plan / Grocery / Order / Receipt, bottom nav mobile, top nav desktop
- **Plan tab** — rolling 10-day window, meal rows with tap actions, hold+drag reorder, per-meal grocery toggle, status bar
- **Meal picker bottom sheet** — search + suggestions for empty days and replacements
- **Side picker sheet** — pill-based side selection with search and freeform
- **Smart swap** — ingredient cross-reference on meal replacement (remove old ingredients, add new)
- **Freeform meals** — Eating Out / Leftovers with no recipe, excluded from grocery
- **Fresh Start** — erase animation with staggered row dissolve, suggests new plan
- **Touch drag-and-drop** — long-press (400ms) to drag meals between days on mobile
- **`grocery_trips` and `trip_items` tables** — full trip lifecycle with product selection columns
- **"Build My List" multi-step flow** — floating FAB, bottom sheet (carryover → regulars → pantry → build), navigates to Grocery tab
- **Grocery tab** — active trip with grouped checklist, three item states, multi-meal badges, add-item with autocomplete
- **Three item states** — unchecked (empty circle), checked (green checkmark), ordered (accent up-arrow)
- **Order tab** — full Kroger product picker with queue strip, auto-search, product images, NOVA badges, Nutri-Score, preference learning, batch cart submission
- **Receipt tab** — placeholder UI (reconciliation flow not yet implemented)
- **Carryover prompt** — surfaces unchecked items from prior trip during Build My List
- **Learning suggestions** — items bought on 3+ of last 5 trips suggested as regulars during Build My List
- **Regulars system** — merged essentials + pantry staples into single `regulars` table
- **Onboarding flow** — 4-step full-screen (Store/Meals/Regulars/Pantry), marker file gates repeat
- **Preferences overlay** — bottom sheet with accordion sections, bent spoon icon toggle with tilt animation
- **Desktop two-column layout** — plan left, grocery sidebar right on 1024px+
- **Typography pass** — 16px base, 44px touch targets, Playfair Display headers, DM Sans body
- **Bottom sheet pattern** — all sheets use swipe-down dismiss via `useSwipeDismiss` hook
- **Lowercase grocery normalization** — all item names saved lowercase at API layer
- **Kroger API integration** — product search, price cache, preference learning, cart submission, OAuth
- **Open Food Facts integration** — NOVA scores + Nutri-Score grades at product selection
- **Google Sheets export** — meal plan + grocery list, excludes online-ordered items
- **SQLAlchemy Core migration** — all raw sqlite3 queries converted to SQLAlchemy Core with PostgreSQL compatibility
- **Production deployment prep** — `DATABASE_URL` env var, health check endpoint, Railway config (Procfile, railway.toml), DEPLOY.md
- **DB moved to `~/.mealrunner/`** — prevents cloud sync corruption of SQLite

## What needs building next (priority order)

1. **Receipt page** — full reconciliation flow (matched / substituted / not fulfilled), resolution prompts, learning feedback
2. **Canonical item dictionary** — typo/spelling normalization for grocery items
3. **Brand ownership display** — surface parent company info at product selection in Order tab
4. **Claude-powered onboarding** — replace form-based placeholder with conversational flow
5. **Shopping feedback loop** — checked = confirmed, skipped = wrong, added = missing; feeds learning layer
6. **Aggregate ethical/sustainability flags** — B-Corp, EWG, labor practices at product selection
7. **Value reporting** — weekly/monthly summary: spending trends, nutrition profile, waste/completion rate

---

## Mockup files (for reference)

- `mealrunner-mobile-final.html` — 6 screens: Plan, Add a Meal sheet, Build My List carryover, Grocery, Order, Receipt
- `mealrunner-desktop-final.html` — 3 browser views: Plan+Grocery, Order (3-col), Receipt

---

*Built with Claude · Aletheia / MealRunner · March 2026*
