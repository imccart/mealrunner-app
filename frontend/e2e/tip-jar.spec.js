import { test, expect } from "./fixtures.js";
import {
  simulateTipCompleted,
  simulateSubscriptionRenewal,
  simulateSubscriptionCancel,
  simulatePaymentFailed,
} from "./helpers.js";

// All tip jar tests run against staging in fake-mode (PLAYWRIGHT_TEST_SECRET set,
// STRIPE_SECRET_KEY unset/sentinel). The Stripe iframe is never rendered;
// instead, the simulator endpoints fire fake webhook events that exercise the
// real DB-side handler.

async function createSession(page, mode, amountCents) {
  const resp = await page.request.post("/api/tip/checkout-session", {
    data: { mode, amount_cents: amountCents },
  });
  if (!resp.ok()) {
    throw new Error(`POST /api/tip/checkout-session ${resp.status()}: ${await resp.text()}`);
  }
  return await resp.json();
}

async function fetchHistory(page) {
  const resp = await page.request.get("/api/tip/history");
  if (!resp.ok()) throw new Error(`GET /api/tip/history ${resp.status()}`);
  return await resp.json();
}

test.describe("Tip jar", () => {
  test("one-time happy path: $5 preset → simulated completion → history shows it", async ({
    authedPage,
  }) => {
    const session = await createSession(authedPage, "one_time", 500);
    expect(session.ok).toBe(true);
    expect(session.client_secret).toBeTruthy();
    expect(session.fake).toBe(true);

    await simulateTipCompleted(authedPage, { sessionId: session.session_id });

    const hist = await fetchHistory(authedPage);
    expect(hist.tips.length).toBe(1);
    expect(hist.tips[0].amount_cents).toBe(500);
    expect(hist.tips[0].mode).toBe("one_time");
    expect(hist.tips[0].is_recurring).toBe(false);
    expect(hist.active_subscription_id).toBeFalsy();
  });

  test("one-time custom amount: $7 records as $7", async ({ authedPage }) => {
    const session = await createSession(authedPage, "one_time", 700);
    await simulateTipCompleted(authedPage, { sessionId: session.session_id });

    const hist = await fetchHistory(authedPage);
    expect(hist.tips.length).toBe(1);
    expect(hist.tips[0].amount_cents).toBe(700);
  });

  test("custom amount minimum $1 enforced server-side", async ({ authedPage }) => {
    // Below floor — backend should reject with 400.
    const resp = await authedPage.request.post("/api/tip/checkout-session", {
      data: { mode: "one_time", amount_cents: 50 },
    });
    expect(resp.status()).toBe(400);
  });

  test("custom amount above ceiling rejected", async ({ authedPage }) => {
    const resp = await authedPage.request.post("/api/tip/checkout-session", {
      data: { mode: "one_time", amount_cents: 200000 },
    });
    expect(resp.status()).toBe(400);
  });

  test("monthly happy path: $5/mo preset → simulated completion → active subscription set", async ({
    authedPage,
  }) => {
    const session = await createSession(authedPage, "monthly", 500);
    const fakeSubId = `sub_test_${Date.now()}`;
    await simulateTipCompleted(authedPage, {
      sessionId: session.session_id,
      subscriptionId: fakeSubId,
    });

    const hist = await fetchHistory(authedPage);
    expect(hist.active_subscription_id).toBe(fakeSubId);
    expect(hist.tips.length).toBe(1);
    expect(hist.tips[0].is_recurring).toBe(true);
    expect(hist.tips[0].amount_cents).toBe(500);
  });

  test("monthly renewal: invoice.paid adds a second tip row keyed on the same subscription", async ({
    authedPage,
  }) => {
    const session = await createSession(authedPage, "monthly", 500);
    const subId = `sub_test_${Date.now()}_renew`;
    await simulateTipCompleted(authedPage, {
      sessionId: session.session_id,
      subscriptionId: subId,
    });

    // Simulate a subscription_cycle invoice.paid.
    await simulateSubscriptionRenewal(authedPage, {
      subscriptionId: subId,
      amountCents: 500,
      seq: 2,
    });

    const hist = await fetchHistory(authedPage);
    expect(hist.tips.length).toBe(2);
    // Both rows should be tied to the same subscription.
    const recurringRows = hist.tips.filter((t) => t.is_recurring);
    expect(recurringRows.length).toBe(2);
    expect(hist.active_subscription_id).toBe(subId);
  });

  test("monthly cancellation: customer.subscription.deleted clears active state", async ({
    authedPage,
  }) => {
    const session = await createSession(authedPage, "monthly", 500);
    const subId = `sub_test_${Date.now()}_cancel`;
    await simulateTipCompleted(authedPage, {
      sessionId: session.session_id,
      subscriptionId: subId,
    });

    let hist = await fetchHistory(authedPage);
    expect(hist.active_subscription_id).toBe(subId);

    await simulateSubscriptionCancel(authedPage, { subscriptionId: subId });

    hist = await fetchHistory(authedPage);
    expect(hist.active_subscription_id).toBeFalsy();
    // The previous succeeded tip stays in history — Stripe charged it.
    expect(hist.tips.length).toBe(1);
  });

  test("failed renewal: invoice.payment_failed does NOT clear active subscription", async ({
    authedPage,
  }) => {
    // Stripe handles dunning via Smart Retries; we only clear active state
    // on customer.subscription.deleted, not on a transient payment failure.
    const session = await createSession(authedPage, "monthly", 500);
    const subId = `sub_test_${Date.now()}_fail`;
    await simulateTipCompleted(authedPage, {
      sessionId: session.session_id,
      subscriptionId: subId,
    });

    await simulatePaymentFailed(authedPage, {
      subscriptionId: subId,
      amountCents: 500,
    });

    const hist = await fetchHistory(authedPage);
    expect(hist.active_subscription_id).toBe(subId);
    // tips returned (status='succeeded' filter) doesn't include the failed row.
    // Active subscription preserved is the load-bearing assertion.
    expect(hist.tips.length).toBe(1);
  });

  test("UI: open sheet, presets render correctly per tab, custom hidden on monthly", async ({
    authedPage,
  }) => {
    await authedPage.goto("/app");
    // Wait for app to load.
    await authedPage.waitForSelector('[data-tour="tipjar"]', { timeout: 10_000 });
    await authedPage.click('[data-tour="tipjar"]');

    // One-time tab is the default.
    await expect(authedPage.locator('[data-testid="tip-mode-one_time"]')).toBeVisible();
    await expect(authedPage.locator('[data-testid="tip-preset-500"]')).toBeVisible();
    await expect(authedPage.locator('[data-testid="tip-preset-1000"]')).toBeVisible();
    await expect(authedPage.locator('[data-testid="tip-preset-custom"]')).toBeVisible();

    // Click custom — input appears.
    await authedPage.click('[data-testid="tip-preset-custom"]');
    await expect(authedPage.locator('[data-testid="tip-custom-input"]')).toBeVisible();

    // Switch to monthly — custom preset and input both gone.
    await authedPage.click('[data-testid="tip-mode-monthly"]');
    await expect(authedPage.locator('[data-testid="tip-preset-custom"]')).toHaveCount(0);
    await expect(authedPage.locator('[data-testid="tip-custom-input"]')).toHaveCount(0);

    // Monthly defaults to $5 selected.
    const monthlyFive = authedPage.locator('[data-testid="tip-preset-500"]');
    await expect(monthlyFive).toBeVisible();
  });

  test("UI: end-to-end click-through via Simulate completion button", async ({
    authedPage,
  }) => {
    await authedPage.goto("/app");
    await authedPage.waitForSelector('[data-tour="tipjar"]', { timeout: 10_000 });
    await authedPage.click('[data-tour="tipjar"]');

    // Default selection is $5 one-time. Click the Submit.
    await authedPage.click('[data-testid="tip-submit"]');

    // Fake-mode panel appears with the simulate button.
    const simulateBtn = authedPage.locator('[data-testid="tip-fake-complete"]');
    await expect(simulateBtn).toBeVisible({ timeout: 5_000 });
    await simulateBtn.click();

    // Thanks state.
    await expect(authedPage.locator("text=Thank you")).toBeVisible({ timeout: 5_000 });
  });
});
