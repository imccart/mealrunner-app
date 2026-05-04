"""Stripe SDK wrapper with a fake-mode branch for e2e testing.

The wrapper exists so e2e tests can run without a Stripe account. When
`STRIPE_SECRET_KEY` is unset (or set to the sentinel `sk_test_e2e_fake`)
AND `PLAYWRIGHT_TEST_SECRET` is set, every function returns a deterministic
fake response and never hits the real Stripe API. The DB-side webhook logic
in api.py is exercised separately via the `/api/admin/e2e-stripe-*` admin
endpoints, which simulate Stripe webhooks without requiring real signatures.

Production sets `STRIPE_SECRET_KEY` to a real `sk_live_*` (or `sk_test_*`
with a real Stripe account during pre-launch) and the real `stripe` SDK
takes over.
"""

from __future__ import annotations

import os
import secrets
from typing import Any

# `stripe` is in the [web] extras; only imported when not in fake mode.
_stripe = None


_FAKE_SENTINEL = "sk_test_e2e_fake"


def _is_fake_mode() -> bool:
    """Fake mode requires PLAYWRIGHT_TEST_SECRET set (so prod can't accidentally
    fall into it) AND STRIPE_SECRET_KEY either unset or the sentinel value.
    """
    if not os.environ.get("PLAYWRIGHT_TEST_SECRET"):
        return False
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    return key == "" or key == _FAKE_SENTINEL


def _real_stripe():
    global _stripe
    if _stripe is None:
        import stripe as _s
        _s.api_key = os.environ["STRIPE_SECRET_KEY"]
        _stripe = _s
    return _stripe


def is_configured() -> bool:
    """Returns True when Stripe is usable (real or fake). False = no key, no test secret."""
    if _is_fake_mode():
        return True
    return bool(os.environ.get("STRIPE_SECRET_KEY"))


# ── Checkout Sessions ────────────────────────────────────


def create_one_time_checkout_session(
    *, user_id: str, amount_cents: int, return_url: str, customer_email: str | None = None,
) -> dict[str, Any]:
    """Create an Embedded Checkout Session for a one-time tip.

    Returns dict with `id` and `client_secret`. The client_secret is what the
    frontend's <EmbeddedCheckout> needs to render the payment form.
    """
    if _is_fake_mode():
        return {
            "id": f"cs_test_{secrets.token_hex(8)}",
            "client_secret": f"cs_test_{secrets.token_hex(16)}_secret_{secrets.token_hex(8)}",
        }
    stripe = _real_stripe()
    session = stripe.checkout.Session.create(
        ui_mode="embedded_page",
        mode="payment",
        return_url=return_url,
        line_items=[{
            "price_data": {
                "currency": "usd",
                "product_data": {"name": "MealRunner tip"},
                "unit_amount": amount_cents,
            },
            "quantity": 1,
        }],
        customer_email=customer_email,
        metadata={"user_id": user_id, "mode": "one_time"},
    )
    return {"id": session.id, "client_secret": session.client_secret}


def create_monthly_checkout_session(
    *, user_id: str, price_id: str, return_url: str, customer_email: str | None = None,
) -> dict[str, Any]:
    """Create an Embedded Checkout Session for a monthly subscription.

    `price_id` references a Price object in Stripe (set up via dashboard for
    each preset $5/mo, $10/mo, ...). Stripe maps the Price → Product → recurring billing.
    """
    if _is_fake_mode():
        return {
            "id": f"cs_test_{secrets.token_hex(8)}",
            "client_secret": f"cs_test_{secrets.token_hex(16)}_secret_{secrets.token_hex(8)}",
        }
    stripe = _real_stripe()
    session = stripe.checkout.Session.create(
        ui_mode="embedded_page",
        mode="subscription",
        return_url=return_url,
        line_items=[{"price": price_id, "quantity": 1}],
        customer_email=customer_email,
        metadata={"user_id": user_id, "mode": "monthly"},
    )
    return {"id": session.id, "client_secret": session.client_secret}


def retrieve_session(session_id: str) -> dict[str, Any]:
    """Fetch a Checkout Session — used after the embedded form completes to
    pull the payment status, subscription id, customer details before redirect.
    """
    if _is_fake_mode():
        return {
            "id": session_id,
            "payment_status": "paid",
            "status": "complete",
            "subscription": None,
            "customer": f"cus_test_{secrets.token_hex(6)}",
            "amount_total": 500,
            "currency": "usd",
        }
    stripe = _real_stripe()
    session = stripe.checkout.Session.retrieve(session_id)
    return {
        "id": session.id,
        "payment_status": session.payment_status,
        "status": session.status,
        "subscription": session.subscription,
        "customer": session.customer,
        "amount_total": session.amount_total,
        "currency": session.currency,
    }


# ── Webhook signature verification ───────────────────────


def construct_webhook_event(payload: bytes, sig_header: str) -> dict[str, Any]:
    """Verify the Stripe-Signature header against `STRIPE_WEBHOOK_SECRET` and
    return the event as a plain dict. Raises on invalid signature.

    Returns a regular dict (parsed from the raw JSON payload) instead of the
    Stripe SDK's `Event` object. Stripe SDK's typed Event objects don't behave
    like plain dicts at every nesting level — `.get()` on `event['data']`
    raises AttributeError in some SDK versions. Plain-dict downstream removes
    that footgun and matches the e2e simulator endpoints, which already
    construct plain-dict events.

    In fake mode we never receive real Stripe webhooks (the e2e simulator
    endpoints bypass this entirely), so this should never be called. Raise
    loudly if it is — a misconfigured prod could otherwise quietly accept
    forged events.
    """
    import json

    if _is_fake_mode():
        raise RuntimeError(
            "construct_webhook_event called in fake mode — use the e2e simulator endpoints instead"
        )
    stripe = _real_stripe()
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET not configured")
    # Verify the signature; this raises on tampered or expired webhooks.
    # We discard the typed Event return value and parse the raw payload as
    # a plain dict for downstream processing.
    stripe.Webhook.construct_event(payload, sig_header, secret)
    return json.loads(payload.decode("utf-8"))


# ── Subscriptions ────────────────────────────────────────


def cancel_subscription(subscription_id: str) -> None:
    """Cancel a subscription immediately. Used when the user hits Cancel in
    our UI (rare — they'd usually go through the Stripe Customer Portal).
    """
    if _is_fake_mode():
        return
    stripe = _real_stripe()
    stripe.Subscription.delete(subscription_id)


def customer_portal_url(customer_id: str, return_url: str) -> str:
    """Create a Stripe Customer Portal session and return the redirect URL.
    The hosted Portal lets users update card, view invoices, cancel subscription.
    """
    if _is_fake_mode():
        # Roundtrip the user back to the return_url so the e2e flow can assert
        # they get redirected somewhere reasonable. Real users would be on
        # billing.stripe.com.
        return return_url
    stripe = _real_stripe()
    session = stripe.billing_portal.Session.create(customer=customer_id, return_url=return_url)
    return session.url
