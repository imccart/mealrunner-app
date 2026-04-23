import { test, expect } from "./fixtures.js";
import {
  pickLibraryMealWithIngredients,
  seedLibraryMeal,
  setMealOnDate,
  todayIso,
} from "./helpers.js";

// Walk the Aisles button only renders on the mobile (narrow) layout.
test.use({ viewport: { width: 390, height: 844 } });

test.describe("Walk the Aisles", () => {
  test("enter shopping mode, check an item, exit cleanly", async ({
    authedPage,
  }) => {
    const libMeal = await pickLibraryMealWithIngredients(authedPage);
    await seedLibraryMeal(authedPage, libMeal);
    await setMealOnDate(authedPage, todayIso(), libMeal.name);

    await authedPage.goto("/app");

    // Switch to the Grocery tab (mobile has it as a bottom-nav tab)
    await authedPage.locator('[data-tour="grocery-tab"]').click();

    // Grocery list should have active items
    const items = authedPage.locator('[class*="groceryItemRow"]');
    await expect(items.first()).toBeVisible({ timeout: 10_000 });
    const totalCount = await items.count();
    expect(totalCount).toBeGreaterThan(0);

    // Click "Walk the Aisles"
    await authedPage
      .getByRole("button", { name: /Walk the Aisles/ })
      .click();

    // Shopping mode header shows "0 of N"
    const count = authedPage.locator('[class*="shoppingCount"]');
    await expect(count).toBeVisible();
    await expect(count).toHaveText(new RegExp(`^0 of ${totalCount}$`));

    // Tap the first shopping item by name to check it off
    const shopItems = authedPage.locator('[class*="shoppingItemName"]');
    await expect(shopItems.first()).toBeVisible();
    await shopItems.first().click();

    // Counter should advance to "1 of N"
    await expect(count).toHaveText(new RegExp(`^1 of ${totalCount}$`));

    // Exit via Done
    await authedPage.getByRole("button", { name: /^Done$/ }).click();

    // Back on normal grocery list — Walk the Aisles button visible again
    await expect(
      authedPage.getByRole("button", { name: /Walk the Aisles/ }),
    ).toBeVisible();
  });
});
