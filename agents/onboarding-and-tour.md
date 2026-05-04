# Onboarding + Tour

The two one-time-per-user flows. Onboarding runs the first time a user signs in; the tour fires after onboarding completes (or via "Take the tour" in Account settings). For always-on UI infrastructure (sheets, header icons, error/toast/offline, branding), see `agents/ui-patterns.md`.

## Onboarding wizard

5-step flow (Welcome → Meals+Sides → Staples → Regulars → Store). Household members get a shortened flow (Welcome → Store) since the owner has already set up meals/staples/regulars.

- **`OnboardingFlow.jsx`** orchestrates the steps.
- **Clippy** — paperclip chef mascot with speech bubbles. Asset: `clippy-chef.png`. Companion: `mouse-chef.png`.
- **Tile grid** for meal selection (deep-copies recipes from the `__library__` user — see `agents/meals-and-plan.md`).
- **DB-driven staple checklist** — pulled from `seed_ingredient_database.yaml` so newly added staples appear in onboarding without code changes.
- **Categorized regulars** — grouped by aisle.
- Spec: `design/onboarding-spec.md`.

## Tour overlay

`TourOverlay.jsx` — live spotlight tour on the **real app UI** (not a mockup). Fires after onboarding completes or via "Take the tour" in Account settings.

- **Targets real DOM** via `data-tour` attributes (e.g. `data-tour="plan"`, `data-tour="grocery-sidebar"`, `data-tour="tipjar"`). The advantage over a mockup is that the tour always reflects the current UI — but it means every redesign must keep `data-tour` selectors in place.
- **Multi-element highlighting per stop** is supported — each stop can highlight several rects at once via multiple `data-tour` selectors. Cutouts render as multiple clip-path polygons.
- **Desktop vs mobile selectors.** `getStops()` in `TourOverlay.jsx` chooses between `data-tour="plan"` (desktop sidebar/main) and `data-tour="plan-tab"` (mobile bottom nav) based on `window.innerWidth >= 1024`. Tip jar / Kitchen / Account / Feedback stops use the same selector on both (header icons).
- **Plan stop is nav-link-only.** Earlier multi-rect version (highlighting both nav link AND main content) had alignment problems on desktop. Keep this stop simple.
- **Rendering.** Semi-transparent backdrop with `clip-path: polygon(...)` cutouts (see `cutoutPolygon` helper). Spotlights are positioned `<div>`s. Callout floats below the primary rect when there's room, otherwise above.
- **Skip-when-missing.** `advance()` skips stops whose target elements don't exist in the current DOM (e.g. mobile-only or feature-flagged elements).

## Stop list (current)

In order: Plan → Grocery → Order → Receipt → Kitchen → Tip jar → Account → Feedback. Tip jar was inserted between Kitchen and Account in session 66.
