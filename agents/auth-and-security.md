# Auth, household sharing, security

## Auth

- **Magic link email** OR **Google Sign-In** → session cookie (30 days).
- **Magic link grace window:** 10-minute grace period after first use to handle email client link prefetching.
- **Google auth via GIS** (Google Identity Services) — JWT verified server-side, no client secret needed. `GOOGLE_CLIENT_ID` env var.

## Household sharing

- Middleware resolves a member user → household owner's data for all data-bearing endpoints.
- **Per-household state** for grocery (order_source, regulars_added, pantry_checked, receipt_data) lives on `grocery_state` keyed by the **owner's** user_id.
- **Store sharing.** `allow_household` column on `user_kroger_tokens`. Toggle in Account sheet. Server-side check on order/submit. Endpoint name is generic: `/api/store/allow-household`.

## Rate limiting

DB-backed (`rate_limits` table), persists across deploys. Per-user limits:

| Action | Limit |
|---|---|
| search | 20 / min |
| receipt upload | 10 / min |
| magic link request | 3 / 15 min |
| invites | 5 / hr |

## Kroger token encryption

Fernet symmetric encryption via `ENCRYPTION_KEY` env var.

- Encrypt on write (OAuth callback, token refresh).
- Decrypt on read.
- Graceful fallback to plaintext if key not set.

## Admin

Admin user = first registered user **or** `ADMIN_USER_ID` env var. Admin endpoints: feedback respond, unknown brands review, e2e simulators (gated behind `PLAYWRIGHT_TEST_SECRET`).

## Public webhook paths

`/api/stripe/webhook` is in `PUBLIC_PATHS` in `code/mealrunner/web/auth.py` so Stripe can hit it without a session cookie. See `agents/tip-jar.md` for signature verification.
