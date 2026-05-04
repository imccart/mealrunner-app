import { test, expect } from "./fixtures.js";
import {
  pickLibraryMealWithIngredients,
  seedLibraryMeal,
  setMealOnDate,
  todayIso,
  dateOffset,
  addRegular,
  addRegularsToGrocery,
} from "./helpers.js";

async function fetchGrocery(page) {
  const resp = await page.request.get("/api/grocery");
  if (!resp.ok()) {
    throw new Error(`/api/grocery ${resp.status()}: ${await resp.text()}`);
  }
  return await resp.json();
}

async function fetchOrder(page) {
  const resp = await page.request.get("/api/order");
  if (!resp.ok()) {
    throw new Error(`/api/order ${resp.status()}: ${await resp.text()}`);
  }
  return await resp.json();
}

function flattenActive(groc) {
  return Object.values(groc.items_by_group || {}).flat();
}

function activeNamesLower(groc) {
  return flattenActive(groc).map((i) => i.name.toLowerCase());
}

async function fetchMealId(page, dateIso) {
  const meals = await (await page.request.get("/api/meals")).json();
  const day = (meals.days || []).find((d) => d.date === dateIso);
  if (!day || !day.meal || !day.meal.id) {
    throw new Error(`No meal found on ${dateIso}`);
  }
  return String(day.meal.id);
}

test.describe("Grocery invariants", () => {
  test("move-meal preserves meal_id and grocery row identity (swap-days moves slot_date, not attributes)", async ({
    authedPage,
  }) => {
    // Per planner.py:swap_dates — "moving slot_date, not by swapping recipe
    // attributes between two meal rows. Preserving each meal's id keeps
    // grocery_items.meal_ids stable across the swap, so _refresh_trip_meal_items
    // doesn't see the swap as a new occurrence and re-surface ingredients the
    // user already bought / checked off."
    //
    // Two invariants under test: (1) meal_id stays the same at the new slot
    // date, (2) the grocery row spawned by the meal keeps its same id and
    // stays active across the move (no fresh re-insert).
    const libMeal = await pickLibraryMealWithIngredients(authedPage);
    await seedLibraryMeal(authedPage, libMeal);

    const date1 = todayIso();
    const date2 = dateOffset(2);
    await setMealOnDate(authedPage, date1, libMeal.name);

    const mealIdBefore = await fetchMealId(authedPage, date1);
    const before = await fetchGrocery(authedPage);
    const beforeItems = flattenActive(before);
    expect(beforeItems.length).toBeGreaterThan(0);
    const target = beforeItems[0];
    const targetIdBefore = target.id;

    // Move via swap-days. date2 is empty so this is a "move" semantically
    // (the planner handles the empty-target branch by just relocating).
    const swapResp = await authedPage.request.post("/api/meals/swap-days", {
      data: { date_a: date1, date_b: date2 },
    });
    expect(swapResp.ok()).toBe(true);

    // Invariant 1: meal_id at date2 is the same as the meal_id was at date1.
    // (swap_dates UPDATEs slot_date, doesn't INSERT a new meal row.)
    const mealIdAfter = await fetchMealId(authedPage, date2);
    expect(mealIdAfter).toBe(mealIdBefore);

    // Invariant 2: the grocery row still exists with the SAME id and is still
    // on the active list. If swap_dates had swapped recipe_id between two meal
    // rows instead of moving slot_date, _refresh_trip_meal_items would see a
    // "new occurrence" (different meal_id ↔ same name pair) and could either
    // dedupe (lose state) or insert a phantom sibling.
    const after = await fetchGrocery(authedPage);
    const targetAfter = flattenActive(after).find((i) => i.id === targetIdBefore);
    expect(targetAfter).toBeDefined();
    expect(targetAfter.name.toLowerCase()).toBe(target.name.toLowerCase());

    // And no phantom sibling for the same canonical name.
    const matches = activeNamesLower(after).filter(
      (n) => n === target.name.toLowerCase(),
    );
    expect(matches).toHaveLength(1);
  });

  test("buy-elsewhere round-trip: row enters the buy_elsewhere set on /api/order; toggling it off restores it to pending", async ({
    authedPage,
  }) => {
    // Buy-elsewhere is order-page state, not grocery-page state. The row
    // stays on the grocery list (visible) but exits the ordering flow into
    // a separate "Buying elsewhere" sidebar section. The endpoint is a
    // toggle: a second POST to /grocery/buy-elsewhere/{id} clears the flag
    // (same shape the frontend's handleUndoBuyElsewhere uses). The unified
    // /grocery/undo endpoint does NOT clear buy_elsewhere — it only resets
    // completed-state columns (checked / have_it / removed / ordered /
    // product_*) since buy-elsewhere'd rows are still active, not completed.
    const libMeal = await pickLibraryMealWithIngredients(authedPage);
    await seedLibraryMeal(authedPage, libMeal);
    await setMealOnDate(authedPage, todayIso(), libMeal.name);

    const before = await fetchGrocery(authedPage);
    const beforeItems = flattenActive(before);
    expect(beforeItems.length).toBeGreaterThan(0);
    const target = beforeItems[0];
    const targetLower = target.name.toLowerCase();

    // Mark buy-elsewhere.
    const beResp = await authedPage.request.post(
      `/api/grocery/buy-elsewhere/${target.id}`,
    );
    expect(beResp.ok()).toBe(true);

    // Order page: row is in `buy_elsewhere`, NOT in `pending` or `selected`.
    const order = await fetchOrder(authedPage);
    const beNames = (order.buy_elsewhere || []).map((i) =>
      (i.name || "").toLowerCase(),
    );
    const pendingNames = (order.pending || []).map((i) =>
      (i.name || "").toLowerCase(),
    );
    expect(beNames).toContain(targetLower);
    expect(pendingNames).not.toContain(targetLower);

    // Grocery page: row is STILL visible on the active list (buy-elsewhere
    // doesn't kick a row off the grocery list, only off the ordering flow).
    const grocAfter = await fetchGrocery(authedPage);
    expect(activeNamesLower(grocAfter)).toContain(targetLower);

    // Toggle buy-elsewhere off — same endpoint, second POST.
    const undoResp = await authedPage.request.post(
      `/api/grocery/buy-elsewhere/${target.id}`,
    );
    expect(undoResp.ok()).toBe(true);

    // Row is back in /api/order pending (no product was selected).
    const orderRestored = await fetchOrder(authedPage);
    const beNames2 = (orderRestored.buy_elsewhere || []).map((i) =>
      (i.name || "").toLowerCase(),
    );
    const pendingNames2 = (orderRestored.pending || []).map((i) =>
      (i.name || "").toLowerCase(),
    );
    expect(beNames2).not.toContain(targetLower);
    expect(pendingNames2).toContain(targetLower);
  });

  test("pantry vs regulars: regulars auto-add via add-regulars; pantry items don't", async ({
    authedPage,
  }) => {
    // Regulars = "every trip" — `/grocery/add-regulars` pushes them all on the
    // list (one tap of the Add my regulars button). Source = 'regular'.
    // Pantry = "on hand" — added via /grocery/add-pantry only when the user
    // explicitly checks them as needing replenishment. Source = 'pantry'.
    // Different code paths, different sources. This test asserts the boundary:
    // creating a pantry row does NOT cause it to appear on the grocery list.
    const regularName = `e2e-regular-${Date.now()}`;
    const pantryName = `e2e-pantry-${Date.now()}`;

    await addRegular(authedPage, regularName);

    const pantryResp = await authedPage.request.post("/api/pantry", {
      data: { name: pantryName, quantity: 1, unit: "count" },
    });
    expect(pantryResp.ok()).toBe(true);

    // Neither should be on the grocery list yet — the regular needs the
    // explicit "Add my regulars" tap, and the pantry item never auto-syncs.
    const before = await fetchGrocery(authedPage);
    const beforeNames = activeNamesLower(before);
    expect(beforeNames).not.toContain(regularName.toLowerCase());
    expect(beforeNames).not.toContain(pantryName.toLowerCase());

    // Trigger "Add my regulars" with only the regular selected.
    await addRegularsToGrocery(authedPage, [regularName]);

    // Now: regular is on the list with source='regular'; pantry is still off.
    const after = await fetchGrocery(authedPage);
    const afterItems = flattenActive(after);
    const regRow = afterItems.find(
      (i) => i.name.toLowerCase() === regularName.toLowerCase(),
    );
    expect(regRow).toBeDefined();
    expect(regRow.source).toBe("regular");
    expect(activeNamesLower(after)).not.toContain(pantryName.toLowerCase());
  });
});
