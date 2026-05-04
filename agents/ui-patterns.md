# UI patterns

Always-on UI infrastructure: header icons, sheets, error/toast/offline behavior, branding. For the one-time onboarding wizard and the spotlight tour, see `agents/onboarding-and-tour.md`.

## Header icons

Three icons live in `Nav.jsx` between the brand and right-side controls:

- **Bent spoon** → opens `MyKitchenSheet` (meals / sides / staples / ratings). `BentSpoonIcon.jsx`. Tilt animation when active.
- **Tip jar** (mason jar with `$`) → opens `TipJarSheet`. `TipJarIcon.jsx`. Wobble-on-tap animation. See `agents/tip-jar.md`.
- **Apron** → opens `PreferencesSheet` (Account). `ApronIcon.jsx`. Sway-on-tap animation (top pivot, fabric-on-hook feel).

All three have tap-feedback animations via Web Animations API on a wrapper `<span>` (predictable transform-origin — SVG + CSS `transform-origin` are flaky together in some browsers).

## Sheets

- **`MyKitchenSheet.jsx`** — 4-tab segmented control (Meals | Sides | Staples | Ratings). Tap a meal/side → detail view with ingredients. Staples tab unifies regulars + pantry with "Every trip" / "On hand" toggle per item. Ratings tab shows purchase history with thumbs up/down.
  - **Add-first layout:** Meals/Sides/Staples tabs show add input at top. Flat scrollable lists (no "Show all" toggle needed).
- **`PreferencesSheet.jsx`** (Account sheet — renamed from "Preferences"). Sections in order:
  1. You and Your Household
  2. Online Store Integrations
  3. Price Tracking
  4. Behind the Label
  5. Invite a Friend
  6. Sign Out
  7. Terms / Privacy links
- **Bottom-sheet swipe.** `useSwipeDismiss` hook handles swipe-to-close.

## Cross-cutting UI

- **Error boundary.** `ErrorBoundary.jsx` — reusable component with dropped grocery bag graphic. Test via `/app#oops`.
- **Toast notifications.** `Toast.jsx` — random restaurant-themed messages on failed API calls ("Lost the ticket", "Stove misfired", etc.). Suppressed when fully offline. Event-based via `mealrunner-toast` `CustomEvent` from `client.js`.
- **Offline support.** Service worker (`sw.js`) caches app shell + grocery/meals/auth API responses. `OfflineBanner.jsx` shows "Offline — showing your last saved list" (near-black bar). SW precaches app shell on activate, clears static cache on update for deploy freshness.
- **Mobile swipe nav.** `useSwipeNav` hook on `<main>` — drag-to-slide with rubber band at edges between Plan / Grocery / Order / Receipt tabs.
- **Terms / Privacy.** Static HTML pages at `/app/terms` and `/app/privacy`. Linked from Account sheet. Contact: `support@aletheia-apps.com`.

## Feedback loop

- `FeedbackFab.jsx` on all page states (including loading/error/empty).
- Admin respond endpoint + user notification banner on next app load.
- Admin = first registered user, or `ADMIN_USER_ID` env var.

## Branding

- **Runner-R mark** — DALL-E-generated running figure forming the letter R. Transparent PNG at `assets/runner-r.png`. Used inline in the wordmark (`meal[R]unner`) on `LoginPage`, `Nav`, `OnboardingFlow`, and `PreferencesSheet`.
- **Welcome animation.** Runner-R slides in from left → shrinks to inline size as "meal" expands left and "unner" expands right → brand group slides up → tagline fades in → button appears.
- **Favicons + PWA icons.** Runner-R composited onto a cream `#f4ede4` tile via Pillow for tab visibility (raw runner-R blends into dark-mode tab strips). Multi-resolution `favicon.ico` (16/32/48/64), `icon-192.png`, `icon-512.png`, `apple-touch-icon.png`. Tile padding tighter at favicon scale (10% pad) than PWA (15% pad).
- **DALL-E icon → CSS-mask pipeline.** For line-art header icons that need to track `var(--accent)` despite being raster: generate via DALL-E, Pillow-clean (transparent BG + ICC strip), paint through `mask-image` so the alpha drives where `background-color: var(--accent)` shows. See `feedback_dalle_icon_pipeline.md` in user memory for the full recipe. Used for the tip jar mason jar.
