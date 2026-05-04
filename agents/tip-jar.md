# Tip jar

Stripe Embedded Checkout for one-time tips and monthly subscriptions. Subtitle copy: "Be sure to tip your app maker."

## UI

- **`TipJarSheet.jsx`** — opened from a header icon (mason jar with `$`) between bent spoon and apron. Two tabs:
  - **One-time** — $5 / $10 / custom. Default $5.
  - **Monthly** — $5 / $10 only. Stripe needs Price objects for recurring; custom is impossible without dynamically creating Price objects per subscriber (clutters the dashboard). One-time uses inline `price_data` so any custom amount works.
- **`TipJarIcon.jsx`** — DALL-E mason jar PNG painted through CSS mask so the line color tracks `var(--accent)`. Wobble-on-tap animation (Web Animations API on a wrapper span). See `feedback_dalle_icon_pipeline.md` in user memory for the full mask recipe.
- Stripe **Embedded Checkout** iframe via `@stripe/react-stripe-js` (`<EmbeddedCheckoutProvider>` + `<EmbeddedCheckout>`). `ui_mode='embedded_page'`. Module-level `_stripePromise` cache, lazy-loaded Stripe.js.
- 9 e2e tests in `frontend/e2e/tip-jar.spec.js` covering happy paths, custom amount, $1 minimum, $1000 ceiling, monthly, renewal, cancellation, failed renewal, UI tab toggle.

## Backend endpoints (`code/mealrunner/web/api.py`)

- `POST /api/tip/checkout-session` — create a Stripe Checkout Session, return `{id, client_secret, fake?}`.
- `GET /api/tip/history` — list past tips for the current user.
- `POST /api/tip/portal` — open Stripe Customer Portal (manage subscription, update card, cancel).
- `POST /api/stripe/webhook` — receive Stripe events. Public path (in `PUBLIC_PATHS` in `auth.py`).
- `POST /api/tip/dev-complete-session` — fake-mode-only simulator that fires a fake `checkout.session.completed` through `_handle_stripe_event`.
- 4 e2e simulator endpoints (`/api/admin/e2e-stripe-*`) for the test suite.

## Schema

- **`tips` table** — one row per Stripe charge. Initial purchase + each renewal share a `stripe_subscription_id`; each row has a distinct `stripe_invoice_id` (which is also UNIQUE so re-delivery is idempotent).
- **`users.active_tip_subscription_id`** — denormalized "is this user currently a monthly tipper?" lookup.

## Stripe client wrapper

`code/mealrunner/stripe_client.py` wraps the SDK with a fake-mode branch. Functions:

- `_is_fake_mode()`
- `is_configured()`
- `create_one_time_checkout_session()`
- `create_monthly_checkout_session()`
- `retrieve_session()`
- `construct_webhook_event()` — verifies the Stripe signature, then returns the **plain JSON dict** parsed from the raw payload (NOT the SDK's `Event` object — see gotcha below).
- `cancel_subscription()`
- `customer_portal_url()`

## Fake-mode pattern

When `STRIPE_SECRET_KEY` is unset (or the sentinel `sk_test_e2e_fake`) AND `PLAYWRIGHT_TEST_SECRET` is set, the wrapper returns canned responses without hitting Stripe. Lets staging exercise the entire flow before a real account exists. See `feedback_stripe_gotchas.md` for why this matters.

## Webhook gotchas

- **Parse payload as plain dict after signature verification.** Stripe SDK's `Event` object doesn't behave like a plain dict at every nesting level — `.get()` on `event['data']` raises `AttributeError` in some SDK versions. Verify the signature, discard the typed Event, parse the raw JSON downstream.
- **Wrap `_handle_stripe_event` in try/except.** Without it, Starlette returns a generic "Internal Server Error" string with no body, AND Railway log viewer doesn't always surface the traceback. Wrap to log via `logger.exception` and return `{"ok": false, "error": "{ExceptionType}: {msg}"}` so the error is visible in both Railway logs AND Stripe's webhook delivery response body.
- **`ui_mode='embedded'` deprecated** → use `'embedded_page'`. The React components are driven by `client_secret`, not by ui_mode parameter.

## Required Stripe events

`checkout.session.completed`, `invoice.paid`, `invoice.payment_failed`, `customer.subscription.deleted`.

## Env vars (5)

- `STRIPE_PUBLISHABLE_KEY` (frontend, served via `/api/tip/stripe-config`)
- `STRIPE_SECRET_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `STRIPE_PRICE_TIP_MONTHLY_5`
- `STRIPE_PRICE_TIP_MONTHLY_10`

Staging values are test-mode (`pk_test_`, `sk_test_`); production gets a separate set of `pk_live_` / `sk_live_` once Aletheia LLC is activated for live mode.

## Webhooks are environment-specific

Staging → `https://staging.getmealrunner.app/api/stripe/webhook`. Production needs its OWN destination at `https://getmealrunner.app/api/stripe/webhook` with its own signing secret. They're independent registrations in Stripe — no auto-clone from test to live.
