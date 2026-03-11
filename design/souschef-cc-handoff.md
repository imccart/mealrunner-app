# Souschef — Product & Design Handoff
*Context for Claude Code from strategy/design session · March 2026*

---

## What Souschef is

A meal planning and grocery logistics app. The key framing: **shopping logistics with meals as the input** — not a recipe app. Users generally know what they're going to cook and how to cook it. Souschef helps them plan meals, build a grocery list from that plan, order what they need, and reconcile what they actually received.

Parent company: **Aletheia** (transparency/values layer, methodology home). Consumer product: **Souschef**.

---

## Core functionality

1. **Meal planning** — a simplified, real-life style planner. Not recipe-centric. Meal (not recipe) is the atomic unit. Rolling 7-day window, not a fixed "week of X."

2. **Grocery list tied to the plan** — auto-generated from meals marked for the list, plus user-added items (regulars, one-offs). No quantities — user knows how much they need.

3. **Ordering** — from unchecked grocery list items, select Kroger products, hand off to Kroger to complete. NOVA scores and brand ownership info surface at point of selection, not preachy, just present.

4. **Receipt reconciliation** — match delivered items against what was ordered. Surface substitutions and out-of-stocks. Decisions at this step feed the learning layer.

5. **Learning from history** — what meals are cooked regularly, what items consistently appear, substitution patterns, recency of meals. This comes from the archived trip data over time, not from any special tracking mechanism.

---

## Navigation — 4 tabs

**Plan / Grocery / Order / Receipt**

This is a linear flow that also works as a nav. Each tab is a distinct job, in the order you'd actually use them in a given week.

- **Plan** — build the meal plan
- **Grocery** — manage and check off the active list
- **Order** — select products for unchecked items, hand off to Kroger
- **Receipt** — reconcile what was delivered

No floating + button. No extra nav chrome. Everything is reachable through the four tabs.

---

## The plan tab

- Rolling 7-day calendar view — each day as a row
- Filled meal rows show the meal name, an optional note (sides etc.), a swap button (⇄), and a toggle to mark it for the grocery list (🛒 → ✓)
- Empty day rows are tappable/clickable → opens a meal picker sheet with recent meals + "Eating Out" option
- **"Eating Out"** is a first-class meal state — always available in the picker
- **"Build My List →"** is the single primary action at the bottom — this is the trigger that creates a new grocery trip object
- **"Start a new plan"** is a quiet secondary link below it (destructive, muted)

---

## Build My List — the list creation trigger

"Build My List" is the moment that creates a discrete list object. Planning itself can happen gradually over days — Souschef waits for the intentional act of "I'm ready to shop."

When tapped:
1. Check for unchecked items on the prior active trip
2. If found → show carryover sheet: "Still need these?" with checkboxes pre-selected
3. User picks what to carry over (or skips)
4. New trip created: carryover items + all meal ingredients for on-list meals

The plan and the list are decoupled in time — you can plan Monday through Wednesday and build on Wednesday night, or plan the whole week Sunday. The app doesn't care.

---

## The grocery tab

- Shows the active grocery trip
- Items grouped by category (Meat, Bread & Pasta, Dairy, Other, etc.)
- Each item shows: check circle, item name, source meal (if from a meal)
- **Item states:**
  - Unchecked — needs to be bought
  - Checked (✓ green) — bought in store
  - Ordered (↑ accent) — selected in Order tab, awaiting delivery
- Sticky footer: "Add item..." input with autocomplete + "+" button
- Autocomplete pulls from full item history (every prior list) — this is how regulars and staples get added. No separate "regulars" button needed. User types "mil..." and milk surfaces.
- No "Select Products" button here — that's the Order tab's job

**Mid-week additions** are just items added directly to the active list via the add input. No new trip, no new mode. It's the same list.

---

## The order tab

- Pulls from **unchecked items on the active grocery list** — this is the source of truth
- As items get checked off in person in Grocery, they disappear from Order automatically
- Left panel: item queue — pending items and already-selected items
- Center panel: Kroger product options for the currently active item
- Right panel (desktop) / footer (mobile): order summary + "Finalize on Kroger →"

**Product cards show:**
- Product name, brand, size, price
- NOVA score badge (1–4, color coded green → red)
- Brand ownership (e.g., "Owned by Hormel Foods")

These are informational, not judgmental. Present at the moment of decision, that's all.

---

## The receipt tab

- Populated after a Kroger order is delivered
- Three sections: **Matched / Substituted / Not fulfilled**
- Each substitution gets a resolution prompt: "That's fine" or "Note for next time"
- Out-of-stock items are automatically returned to the grocery list
- Resolving the receipt feeds the learning layer — what you accepted, what you flagged
- "Close Receipt ✓" finalizes the reconciliation

---

## The grocery trip data model

One active grocery list at a time. A list has items from three sources:

1. **Meal ingredients** — auto-added when a meal is toggled on-list
2. **Regulars / staples** — added via autocomplete add-item field
3. **One-offs** — same field, freeform additions for anything else

All stored in one table with a `trip_id`. The list doesn't care where items came from — everything shows up grouped by category.

**Trip lifecycle:**
- Active until user starts a new trip (via "Build My List")
- Old trip archived as-is, checked and unchecked items preserved
- Unchecked items surface in carryover prompt at next Build My List

**Trip table sketch:**
```
grocery_trips: id, created_at, completed_at, status
trip_items: trip_id, name, shopping_group, source (meal/regular/extra), checked, checked_at, ordered, ordered_at
```

**Learning queries from trip history:**
- Frequency of items → suggest as regulars
- Recency of meals → surface in meal picker ("haven't had this in a while")
- Substitution patterns → smarter product defaults
- Unchecked items → what didn't get bought and why

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

**NOVA badge colors:**
```
NOVA 1: bg #E8F0EB, text #4A7C59  (green — minimally processed)
NOVA 2: bg #F0F4DC, text #7A8C3A  (yellow-green)
NOVA 3: bg #FDF0DC, text #C47F2A  (amber)
NOVA 4: bg #FDECEA, text #C43A2A  (red — ultra-processed)
```

---

## Key design decisions from this session

**On the + button:** Removed entirely. Every action it was covering is reachable through the main tabs. Adding a meal → tap empty day row. Adding to list → Grocery tab add-item field. Building a new list → "Build My List" button. The + button was solving a navigation problem that doesn't exist.

**On list creation:** "Build My List" is the intentional trigger — not automatic. You can plan gradually over days and build when ready. This creates a discrete trip object with a clear start time.

**On the grocery/order relationship:** Grocery is the source of truth. Order derives from unchecked items. Checking something off in Grocery removes it from Order. Ordering something in Order marks it as "ordered" in Grocery (not checked — it hasn't arrived yet). These are distinct states.

**On Receipt vs History:** Receipt is an active step in the weekly workflow, not a passive archive. History lives inside the data layer, surfacing in the meal picker and learning features — it's not a top-level tab.

**On mid-week shopping:** Not a separate mode. Just add items directly to the active list in the Grocery tab. Same list, same trip, no new concept required.

**On regulars/staples:** No separate UI concept. The autocomplete add-item field pulls from full purchase history. Typing "mil..." surfaces milk. Frequency of manual additions over time is exactly the signal needed to build regulars-style suggestions — it falls out of the data naturally.

---

## What's been built (existing backend)

- Python/FastAPI backend
- React frontend (in progress)
- Kroger API integration — first real order has been placed
- Running locally, accessible via Tailscale
- Meal plan data model exists
- Grocery list functionality exists

## What needs building next (priority order)

1. `grocery_trips` and `trip_items` tables + migration
2. "Build My List" endpoint — creates trip, attaches meal ingredients, handles carryover
3. Grocery tab renders active trip
4. Item state management: unchecked / checked / ordered
5. Order tab pulls from unchecked trip items
6. Receipt tab — reconciliation flow
7. Autocomplete on add-item field from trip history
8. Carryover prompt UI on Build My List

---

## Mockup files (for reference)

- `souschef-mobile-final.html` — 6 screens: Plan, Add a Meal sheet, Build My List carryover, Grocery, Order, Receipt
- `souschef-desktop-final.html` — 3 browser views: Plan+Grocery, Order (3-col), Receipt

---

*Built with Claude · Aletheia / Souschef · March 2026*
