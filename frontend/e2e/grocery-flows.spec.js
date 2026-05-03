import { test, expect } from "./fixtures.js";
import {
  pickLibraryMealWithIngredients,
  seedLibraryMeal,
  setMealOnDate,
  todayIso,
} from "./helpers.js";

async function fetchGrocery(page) {
  const resp = await page.request.get("/api/grocery");
  if (!resp.ok()) {
    throw new Error(`/api/grocery ${resp.status()}: ${await resp.text()}`);
  }
  return await resp.json();
}

function flattenActive(groc) {
  return Object.values(groc.items_by_group || {}).flat();
}

function activeNamesLower(groc) {
  return flattenActive(groc).map((i) => i.name.toLowerCase());
}

test.describe("Grocery flows", () => {
  test("order select — item moves to ordered, off active list, no phantom on re-sync", async ({
    authedPage,
  }) => {
    const libMeal = await pickLibraryMealWithIngredients(authedPage);
    await seedLibraryMeal(authedPage, libMeal);
    await setMealOnDate(authedPage, todayIso(), libMeal.name);

    const before = await fetchGrocery(authedPage);
    const beforeItems = flattenActive(before);
    expect(beforeItems.length).toBeGreaterThan(0);
    const target = beforeItems[0];

    const product = {
      upc: `e2e-${Date.now()}`,
      name: `Test Pick ${target.name}`,
      brand: "TestBrand",
      size: "1 ct",
      price: 1.99,
      image: "",
    };

    const selResp = await authedPage.request.post("/api/order/select", {
      data: { item_name: target.name, product, quantity: 1 },
    });
    expect(selResp.ok()).toBe(true);

    // /api/grocery runs _ensure_active_trip again — this is the exact path
    // that used to phantom-insert a sibling row for the just-ordered name.
    // The phantom would manifest as TWO rows with this name in items_by_group:
    // the original (now flagged ordered) and a fresh active sibling. The
    // frontend filters ordered rows from rendering, but both rows still come
    // back from the API. So assert exactly one row with this name exists.
    const after = await fetchGrocery(authedPage);
    const targetLower = target.name.toLowerCase();
    const matches = activeNamesLower(after).filter((n) => n === targetLower);
    expect(matches).toHaveLength(1);
    expect(after.ordered).toContain(targetLower);

    // Reload UI — desktop sidebar should not show this name in active list.
    // (Ordered rows live on the Order page, not the grocery sidebar.)
    await authedPage.goto("/app");
    const groceryRows = authedPage
      .locator('[class*="groceryItemRow"]')
      .filter({ hasText: target.name });
    await expect(groceryRows).toHaveCount(0, { timeout: 10_000 });
  });

  test("have-it — persists across reload and meal-sync re-runs (no phantom)", async ({
    authedPage,
  }) => {
    const libMeal = await pickLibraryMealWithIngredients(authedPage);
    await seedLibraryMeal(authedPage, libMeal);
    await setMealOnDate(authedPage, todayIso(), libMeal.name);

    const before = await fetchGrocery(authedPage);
    const beforeItems = flattenActive(before);
    expect(beforeItems.length).toBeGreaterThan(0);
    const target = beforeItems[0];
    const targetLower = target.name.toLowerCase();

    const haveResp = await authedPage.request.post(
      `/api/grocery/have-it/${target.id}`,
    );
    expect(haveResp.ok()).toBe(true);

    // First sync (have-it endpoint runs one internally). Re-fetch for clarity.
    const after = await fetchGrocery(authedPage);
    expect(activeNamesLower(after)).not.toContain(targetLower);
    expect(after.have_it).toContain(targetLower);

    // Trigger another _ensure_active_trip pass. If existing_map didn't widen
    // to include have-it rows, this is where a phantom active sibling would
    // get inserted.
    const after2 = await fetchGrocery(authedPage);
    expect(activeNamesLower(after2)).not.toContain(targetLower);
    expect(after2.have_it).toContain(targetLower);

    // UI: reload and confirm no active row in the grocery sidebar.
    await authedPage.goto("/app");
    const groceryRows = authedPage
      .locator('[class*="groceryItemRow"]')
      .filter({ hasText: target.name });
    await expect(groceryRows).toHaveCount(0, { timeout: 10_000 });
  });

  test("recipe ingredient edits propagate to grocery on next refresh", async ({
    authedPage,
  }) => {
    const libMeal = await pickLibraryMealWithIngredients(authedPage);
    await seedLibraryMeal(authedPage, libMeal);
    const recipe = await setMealOnDate(authedPage, todayIso(), libMeal.name);

    // Sync once so meal-source rows for the existing ingredients are seeded.
    await fetchGrocery(authedPage);

    // Add a new ingredient with a name that won't fuzzy-match any seed.
    const markerRaw = `zzqqxxmarker${Date.now()}`;
    const addResp = await authedPage.request.post(
      `/api/recipes/${recipe.id}/ingredients`,
      { data: { name: markerRaw } },
    );
    expect(addResp.ok()).toBe(true);
    const addBody = await addResp.json();
    expect(addBody.ok).toBe(true);
    const canonicalName = (addBody.name || markerRaw).toLowerCase();

    // Next /api/grocery call runs _refresh_trip_meal_items, which should
    // pick up the new recipe ingredient.
    const afterAdd = await fetchGrocery(authedPage);
    expect(activeNamesLower(afterAdd)).toContain(canonicalName);

    // Find the ri_id so we can DELETE it.
    const ingResp = await authedPage.request.get(
      `/api/recipes/${recipe.id}/ingredients`,
    );
    const ingBody = await ingResp.json();
    const newIng = (ingBody.ingredients || []).find(
      (i) => i.name.toLowerCase() === canonicalName,
    );
    expect(newIng).toBeDefined();

    const delResp = await authedPage.request.delete(
      `/api/recipes/${recipe.id}/ingredients/${newIng.id}`,
    );
    expect(delResp.ok()).toBe(true);

    // Next /api/grocery call should drop the orphaned meal-source row.
    const afterDel = await fetchGrocery(authedPage);
    expect(activeNamesLower(afterDel)).not.toContain(canonicalName);
  });
});
