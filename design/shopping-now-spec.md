# MealRunner — "Shopping Now" Mode Spec

## Concept

A focused, full-screen shopping mode optimized for in-store use. One-handed, large text, high contrast, minimal distraction. Activated from the Grocery tab when the user is ready to shop in person.

---

## Entry Point

- **"Shopping Now"** button on the Grocery tab — persistent, bottom of screen, same visual weight as a primary action button
- Terracotta (`--accent`) filled button, full width
- Label: **"Shopping Now"**
- Tapping enters shopping mode immediately — no confirmation needed

---

## The Shopping Mode View

### Layout
- Full screen takeover — no nav tabs, no header icons, no add bar, no regulars/staples buttons, no preferences
- Status bar hidden or minimal
- Everything stripped away except the list and the exit button

### Color Scheme — High Contrast Dark Mode
Flip the normal palette for in-store readability under fluorescent lights:
- Background: `#2C2420` (dark brown — `--dark`)
- Item text: `#FAF7F2` (cream — `--cream`)
- Aisle headers: `#D4623A` (terracotta — `--accent`)
- Checked/dismissed items: `#FAF7F2` at 30% opacity, strikethrough
- Section dividers: `#8B6F5E` (brown — `--brown`) at 40% opacity

### Typography — Aggressive Size
- Item names: **28px**, DM Sans, regular weight
- Aisle headers: **20px**, Playfair Display, bold, terracotta
- Running count: **16px**, DM Sans, muted, top center
- Everything larger than you think you need — arm's length, one hand, cart in the other

### Header — Minimal
- Running count centered: **"12 of 18"** — updates in real time as items are checked off
- **"Done"** button top right — exits shopping mode
- No other controls

### List Structure
- Items grouped by aisle (same as normal Grocery view)
- Aisle headers sticky — stay visible as you scroll through the section
- Active items at top of each aisle group
- Dismissed items at bottom of each aisle group, faded

---

## Interactions

### Swipe Left — Check Off Item
- Swipe left on any active item → item is marked as bought
- Smooth animation: item slides left, fades to 30% opacity, moves to bottom of its aisle group with strikethrough
- Satisfying — should feel like physically crossing something off a list
- No confirmation needed

### Tap Checked Item — Undo
- Tap any dismissed (faded/strikethrough) item at bottom of group → restores it to active
- Moves back up to active section of its aisle group
- Handles fat finger mistakes gracefully
- No confirmation needed

### Swipe Right — Reserved
- No action for now
- Reserve for future use (flag item, note substitution, etc.)

---

## Battery & Performance Optimization

Shopping mode should be as battery-efficient as possible — users may be in the store for 30-60 minutes with the screen on.

### Screen Wake Lock
- Request `WakeLock` API on entry to shopping mode — keeps screen on
- Release wake lock on exit
- Handle wake lock denial gracefully (some browsers/devices don't support it) — fail silently, don't block entry

### Dark Mode Power Savings
- Dark background is intentional for OLED power efficiency — dark pixels use significantly less power
- Do not override with any light elements

### Pause Background Activity
- On entry to shopping mode, pause all background polling:
  - Price check background jobs
  - Any periodic sync or refresh calls
  - Learning/suggestion computations
- Resume all background activity on exit
- The list data is already loaded — no need to refresh mid-shop

### No Animations in Background
- Disable any non-essential CSS animations while in shopping mode
- Only item check/uncheck animations should run

---

## Exit

- **"Done" button** top right — exits shopping mode, returns to normal Grocery view
- Checked items remain checked in the normal view — state is shared, not separate
- Background activity resumes immediately on exit
- Wake lock released on exit

---

## State Management

- Shopping mode is a **render mode toggle within GroceryPage** — not a separate component
- Same underlying trip data, same checked state
- No sync issues — one source of truth
- `shoppingMode: boolean` state variable controls which view renders

---

## Technical Notes

- Wake Lock API: `navigator.wakeLock.request('screen')` — wrap in try/catch, not universally supported
- Swipe detection: use touch events (`touchstart`, `touchend`) with a minimum swipe distance threshold (~50px) to avoid accidental triggers
- Item reordering (active vs dismissed within aisle group): handle in component state, not backend — no API calls needed for reorder within session
- Checked state syncs to backend same as normal grocery checking — no change to existing API calls

---

## What This Is Not

- Not a separate page or route
- Not a new trip or list — same active trip
- Not a replacement for the Order tab (online ordering)
- Not a persistent mode — always returns to normal Grocery view on exit

---

## Future Considerations (Not Now)

- Swipe right to flag a substitution or note
- Voice readout of next item ("Next: chicken breasts, Meat section")
- Estimated time remaining based on aisle progress
- Share shopping mode with household member in real time ("Ian is on aisle 7")
