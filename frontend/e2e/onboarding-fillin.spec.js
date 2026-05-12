import { test, expect, makeTestEmail } from "./fixtures.js";

// Companion to onboarding.spec.js, which only covers the skip-everything
// happy path. This test fills in real data — name, a meal, a staple, a
// regular — and asserts the picks show up on /api/recipes, /api/staples
// (both modes), /api/auth/me. Friends-beta cold-start path.

test.describe("Onboarding fill-in path", () => {
  test("fresh user picks a meal, staple, and regular; data persists end-to-end", async ({
    browser,
  }) => {
    // Need skip_onboarding=false so the wizard renders. Mirror the existing
    // onboarding.spec.js pattern of opening a fresh context manually.
    const context = await browser.newContext();
    const page = await context.newPage();
    const email = makeTestEmail();

    const loginResp = await page.request.post("/api/auth/e2e-login", {
      data: {
        email,
        secret: process.env.PLAYWRIGHT_TEST_SECRET || "",
        skip_onboarding: false,
      },
    });
    expect(loginResp.ok()).toBeTruthy();

    try {
      // Pull the library + staples up front so we can click the same items
      // by name (rather than guessing which row is which).
      const lib = await (
        await page.request.get("/api/onboarding/library")
      ).json();
      const firstMeal = (lib.meals || [])[0];
      expect(firstMeal, "library has at least one meal").toBeTruthy();

      const staplesResp = await (
        await page.request.get("/api/onboarding/staples")
      ).json();
      const firstStaple = (staplesResp.staples || [])[0];
      expect(firstStaple, "staples list has at least one entry").toBeTruthy();

      // 'eggs' is in REGULAR_CATEGORIES.Dairy & Eggs and only appears once,
      // so it's safe to click by text without disambiguation.
      const regularName = "eggs";

      await page.goto("/app");

      // ── Step 0: name + zip + TOS ──
      await expect(page.getByText(/Let.s get you set up/)).toBeVisible({
        timeout: 10_000,
      });
      await page.getByPlaceholder("First name").fill("Filly");
      await page.getByPlaceholder("Last name").fill("McFill");
      await page.getByPlaceholder("Zip code").fill("30307");
      await page.getByLabel(/I agree to the/).check();
      await page.getByRole("button", { name: /^Next$/ }).click();

      // ── Step 1: pick first library meal ──
      await expect(page.getByText(/What does your family eat/)).toBeVisible({
        timeout: 10_000,
      });
      // Tile button's accessible name is exactly the meal name.
      await page
        .getByRole("button", { name: firstMeal.name, exact: true })
        .first()
        .click();
      await page.getByRole("button", { name: /^Next$/ }).click();

      // ── Step 2: pick first staple ──
      await expect(
        page.getByText(/already in your kitchen/),
      ).toBeVisible({ timeout: 10_000 });
      // Staples render as <div className="checkItem"> with onClick on the
      // div and a <span> inside containing the name. Clicking the span
      // bubbles to the div's handler.
      await page.getByText(firstStaple.name, { exact: true }).first().click();
      await page.getByRole("button", { name: /^Next$/ }).click();

      // ── Step 3: pick a regular ──
      await expect(page.getByText(/always in your cart/)).toBeVisible({
        timeout: 10_000,
      });
      await page.getByText(regularName, { exact: true }).first().click();
      await page.getByRole("button", { name: /^Next$/ }).click();

      // ── Step 4: skip the store integration (Kroger needs real OAuth) ──
      await expect(
        page.getByText(/Connect your grocery store/),
      ).toBeVisible({ timeout: 10_000 });
      await page.getByRole("button", { name: /Skip for now/ }).click();

      // ── Land on Plan ──
      await expect(page.locator('[data-tour="plan-tab"]')).toHaveClass(
        /active/,
        { timeout: 10_000 },
      );

      // ── Backend assertions: data actually persisted ──
      const me = await (await page.request.get("/api/auth/me")).json();
      expect(me.first_name, "first_name saved").toBe("Filly");
      expect(me.last_name, "last_name saved").toBe("McFill");

      const recipes =
        (await (await page.request.get("/api/recipes")).json()).recipes || [];
      const matchedMeal = recipes.find(
        (r) => r.name.toLowerCase() === firstMeal.name.toLowerCase(),
      );
      expect(
        matchedMeal,
        `picked library meal "${firstMeal.name}" is in user's recipes`,
      ).toBeTruthy();

      const everyTrip =
        (await (await page.request.get("/api/staples?mode=every_trip")).json()).staples || [];
      const matchedReg = everyTrip.find(
        (s) => s.name.toLowerCase() === regularName,
      );
      expect(
        matchedReg,
        `picked regular "${regularName}" is in user's every-trip staples`,
      ).toBeTruthy();

      const keepOnHand =
        (await (await page.request.get("/api/staples?mode=keep_on_hand")).json()).staples || [];
      const matchedStaple = keepOnHand.find(
        (s) => s.name.toLowerCase() === firstStaple.name.toLowerCase(),
      );
      expect(
        matchedStaple,
        `picked staple "${firstStaple.name}" is in user's pantry`,
      ).toBeTruthy();
    } finally {
      await context.close();
    }
  });
});
