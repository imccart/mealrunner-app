import { test, expect } from "./fixtures.js";
import {
  pickLibraryMealWithIngredients,
  seedLibraryMeal,
  setMealOnDate,
  stageGroceryRow,
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

  test("stale receipt-tagged row from prior occurrence does not block fresh meal-sync insert", async ({
    authedPage,
  }) => {
    // Regression: pre-Phase-B legacy rows (or any receipt-tagged row whose
    // meal_ids no longer intersect the active plan) used to block meal-sync
    // from inserting a fresh meal-source sibling for the same canonical name.
    // Symptom from prod feedback id=108 (2026-05-03): user added "Frozen
    // Pizza Night" today; the March-29 receipt-matched rows for "frozen
    // pizza" and "edamame" sat with meal_ids='' AND were silently in
    // covered_keys (which only checked receipt_status != ''), so the new
    // meal's ingredients never populated to the grocery list. Fix: covered
    // _keys requires the covering row's meal_ids to intersect the active
    // plan's meal_ids.
    const libMeal = await pickLibraryMealWithIngredients(authedPage);
    await seedLibraryMeal(authedPage, libMeal);
    await setMealOnDate(authedPage, todayIso(), libMeal.name);

    // First sync: meal-source rows get inserted normally.
    const before = await fetchGrocery(authedPage);
    const beforeItems = flattenActive(before);
    expect(beforeItems.length).toBeGreaterThan(0);
    const target = beforeItems[0];
    const targetLower = target.name.toLowerCase();

    // Stage the row to look like a stale legacy artifact: receipt-matched
    // (so it's excluded from existing_map) AND meal_ids='' (so under the
    // fixed logic, the row's meal_ids don't intersect the active plan's
    // meal_ids and the canonical name is NOT covered).
    await stageGroceryRow(authedPage, {
      id: target.id,
      receipt_status: "matched",
      meal_ids: "",
    });

    // Next /api/grocery call: meal-sync should insert a fresh meal-source
    // row for this canonical name. Without the fix the active list comes
    // back empty for this name (covered_keys blocks the insert).
    const after = await fetchGrocery(authedPage);
    const matches = activeNamesLower(after).filter((n) => n === targetLower);
    expect(matches.length).toBeGreaterThanOrEqual(1);
  });

  test("buy ingredient for meal A, add meal B that needs same ingredient — fresh row appears for B", async ({
    authedPage,
  }) => {
    // The whole point of meal_ids was to distinguish meal occurrences. If
    // user buys X for meal A (row → receipt='matched', meal_ids='A'), then
    // adds meal B that also wants X, the user expects X to appear on the
    // grocery list FOR B. Per-name covered_keys collapsed both meals into
    // a single boolean and blocked the insert. Per-meal-id covered tracking
    // computes uncovered = fresh_need - covered, inserts only if non-empty,
    // and uses uncovered for the new row's meal_ids/for_meals.
    const libMeal = await pickLibraryMealWithIngredients(authedPage);
    await seedLibraryMeal(authedPage, libMeal);

    // Date1: meal A. First sync seeds meal-source rows.
    const date1 = todayIso();
    await setMealOnDate(authedPage, date1, libMeal.name);
    const before = await fetchGrocery(authedPage);
    const beforeItems = flattenActive(before);
    expect(beforeItems.length).toBeGreaterThan(0);
    const target = beforeItems[0];
    const targetLower = target.name.toLowerCase();

    // Look up meal_id A for the seeded date so we can keep it in meal_ids
    // (this row is "still attached to a meal currently on the plan").
    const mealsBeforeBuy = await (
      await authedPage.request.get("/api/meals")
    ).json();
    const dayA = (mealsBeforeBuy.days || []).find((d) => d.date === date1);
    expect(dayA).toBeDefined();
    expect(dayA.id).toBeTruthy();
    const mealIdA = String(dayA.id);

    // Simulate buying the ingredient for meal A: row stays bound to A but
    // exits existing_map via receipt_status='matched'.
    await stageGroceryRow(authedPage, {
      id: target.id,
      receipt_status: "matched",
      meal_ids: mealIdA,
    });

    // Now add meal B (same recipe, different date — gets a new meal_id).
    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    const date2 = `${tomorrow.getFullYear()}-${String(
      tomorrow.getMonth() + 1,
    ).padStart(2, "0")}-${String(tomorrow.getDate()).padStart(2, "0")}`;
    await setMealOnDate(authedPage, date2, libMeal.name);

    // Next /api/grocery call: meal-sync sees fresh_need={A, B}, covered={A}
    // (row1's meal_ids intersect active plan), uncovered={B}. Should insert
    // a fresh meal-source row for B. Without per-meal-id tracking, the old
    // covered_keys would say "X is already covered" and skip the insert.
    const after = await fetchGrocery(authedPage);
    const matches = activeNamesLower(after).filter((n) => n === targetLower);
    expect(matches.length).toBeGreaterThanOrEqual(1);
  });
});
