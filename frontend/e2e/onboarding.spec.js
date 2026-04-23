import { test, expect } from "./fixtures.js";
import { makeTestEmail } from "./fixtures.js";

test.describe("Onboarding", () => {
  test("fresh user walks through all steps and lands on Plan", async ({
    browser,
  }) => {
    // This test needs onboarding to NOT be skipped, so we call the bypass
    // directly rather than using the authedPage fixture.
    const context = await browser.newContext();
    const page = await context.newPage();
    const email = makeTestEmail();
    const resp = await page.request.post("/api/auth/e2e-login", {
      data: {
        email,
        secret: process.env.PLAYWRIGHT_TEST_SECRET || "",
        skip_onboarding: false,
      },
    });
    expect(resp.ok()).toBeTruthy();

    try {
      await page.goto("/app");

      // Step 0 — Who Are You. Fill the minimum required fields.
      await expect(page.getByText(/Let.s get you set up/)).toBeVisible({
        timeout: 10_000,
      });
      await page.getByPlaceholder("First name").fill("Flow");
      await page.getByPlaceholder("Last name").fill("Walker");
      await page
        .getByLabel(/I agree to the/)
        .check();

      // Next → step 1 (Meals)
      await page.getByRole("button", { name: /^Next$/ }).click();

      // Step 1 — meals load. Skip without picking extras (defaults are fine).
      await expect(page.getByText(/What does your family eat/)).toBeVisible({
        timeout: 10_000,
      });
      await page.getByRole("button", { name: /Skip for now/ }).click();

      // Step 2 — staples. Skip.
      await expect(
        page.getByText(/already in your kitchen/),
      ).toBeVisible({ timeout: 10_000 });
      await page.getByRole("button", { name: /Skip for now/ }).click();

      // Step 3 — regulars. Skip.
      await expect(page.getByText(/always in your cart/)).toBeVisible({
        timeout: 10_000,
      });
      await page.getByRole("button", { name: /Skip for now/ }).click();

      // Step 4 — store. Skip finishes onboarding.
      await expect(
        page.getByText(/Connect your grocery store/),
      ).toBeVisible({ timeout: 10_000 });
      await page.getByRole("button", { name: /Skip for now/ }).click();

      // Should now be on the app with Plan tab active
      await expect(page.locator('[data-tour="plan-tab"]')).toHaveClass(
        /active/,
        { timeout: 10_000 },
      );
      await expect(page.getByText(/Your next 10 days/)).toBeVisible();
    } finally {
      await context.close();
    }
  });
});
