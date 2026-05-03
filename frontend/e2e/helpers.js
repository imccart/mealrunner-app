import { expect } from "@playwright/test";

export async function fetchLibrary(page) {
  const resp = await page.request.get("/api/onboarding/library");
  if (!resp.ok()) {
    throw new Error(`GET /api/onboarding/library ${resp.status()}`);
  }
  return await resp.json();
}

export async function pickLibraryMealWithIngredients(page) {
  const lib = await fetchLibrary(page);
  const meal = (lib.meals || []).find(
    (m) => Array.isArray(m.ingredients) && m.ingredients.length > 0,
  );
  if (!meal) {
    throw new Error("No library meals with ingredients found");
  }
  return meal;
}

export async function seedLibraryMeal(page, meal) {
  const resp = await page.request.post("/api/onboarding/select-recipes", {
    data: {
      meal_ids: [meal.id],
      side_ids: [],
      custom_meals: [],
      custom_sides: [],
    },
  });
  if (!resp.ok()) {
    throw new Error(`POST /api/onboarding/select-recipes ${resp.status()}`);
  }
}

export async function setMealOnDate(page, dateIso, recipeName) {
  const meals = await (await page.request.get("/api/meals")).json();
  if (!meals?.days) throw new Error("Could not load /api/meals");

  const recipesResp = await page.request.get("/api/recipes");
  const recipes = (await recipesResp.json()).recipes || [];
  const match = recipes.find(
    (r) => r.name.toLowerCase() === recipeName.toLowerCase(),
  );
  if (!match) {
    throw new Error(
      `Recipe "${recipeName}" not found on user. Seed it first via seedLibraryMeal.`,
    );
  }

  const resp = await page.request.post(`/api/meals/${dateIso}/set`, {
    data: { recipe_id: match.id, sides: [] },
  });
  if (!resp.ok()) {
    throw new Error(`POST /api/meals/${dateIso}/set ${resp.status()}`);
  }
  return match;
}

export function todayIso() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export async function stageGroceryRow(page, { id, receipt_status = "", meal_ids = "" }) {
  const secret = process.env.PLAYWRIGHT_TEST_SECRET || "";
  const resp = await page.request.post("/api/admin/e2e-stage-grocery-row", {
    data: { secret, id, receipt_status, meal_ids },
  });
  if (!resp.ok()) {
    throw new Error(
      `POST /api/admin/e2e-stage-grocery-row ${resp.status()}: ${await resp.text()}`,
    );
  }
}

export async function createGroceryRow(page, fields) {
  const secret = process.env.PLAYWRIGHT_TEST_SECRET || "";
  const resp = await page.request.post("/api/admin/e2e-create-grocery-row", {
    data: { secret, ...fields },
  });
  if (!resp.ok()) {
    throw new Error(
      `POST /api/admin/e2e-create-grocery-row ${resp.status()}: ${await resp.text()}`,
    );
  }
  return await resp.json();
}

export async function addRegular(page, name, shopping_group = "") {
  const resp = await page.request.post("/api/regulars", {
    data: { name, shopping_group, store_pref: "either" },
  });
  if (!resp.ok()) {
    throw new Error(`POST /api/regulars ${resp.status()}: ${await resp.text()}`);
  }
  return await resp.json();
}

export async function addRegularsToGrocery(page, names) {
  const resp = await page.request.post("/api/grocery/add-regulars", {
    data: { selected: names },
  });
  if (!resp.ok()) {
    throw new Error(
      `POST /api/grocery/add-regulars ${resp.status()}: ${await resp.text()}`,
    );
  }
  return await resp.json();
}

export function dateOffset(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

// ── Tip jar helpers ─────────────────────────────────────

export async function simulateTipCompleted(page, { sessionId, subscriptionId = null }) {
  const secret = process.env.PLAYWRIGHT_TEST_SECRET || "";
  const resp = await page.request.post("/api/admin/e2e-stripe-tip-completed", {
    data: { secret, session_id: sessionId, subscription_id: subscriptionId },
  });
  if (!resp.ok()) {
    throw new Error(
      `POST /api/admin/e2e-stripe-tip-completed ${resp.status()}: ${await resp.text()}`,
    );
  }
  return await resp.json();
}

export async function simulateSubscriptionRenewal(page, { subscriptionId, amountCents, seq = 1 }) {
  const secret = process.env.PLAYWRIGHT_TEST_SECRET || "";
  const resp = await page.request.post(
    "/api/admin/e2e-stripe-subscription-renewal",
    { data: { secret, subscription_id: subscriptionId, amount_cents: amountCents, seq } },
  );
  if (!resp.ok()) {
    throw new Error(
      `POST /api/admin/e2e-stripe-subscription-renewal ${resp.status()}: ${await resp.text()}`,
    );
  }
}

export async function simulateSubscriptionCancel(page, { subscriptionId }) {
  const secret = process.env.PLAYWRIGHT_TEST_SECRET || "";
  const resp = await page.request.post(
    "/api/admin/e2e-stripe-subscription-cancel",
    { data: { secret, subscription_id: subscriptionId } },
  );
  if (!resp.ok()) {
    throw new Error(
      `POST /api/admin/e2e-stripe-subscription-cancel ${resp.status()}: ${await resp.text()}`,
    );
  }
}

export async function simulatePaymentFailed(page, { subscriptionId, amountCents }) {
  const secret = process.env.PLAYWRIGHT_TEST_SECRET || "";
  const resp = await page.request.post(
    "/api/admin/e2e-stripe-payment-failed",
    { data: { secret, subscription_id: subscriptionId, amount_cents: amountCents } },
  );
  if (!resp.ok()) {
    throw new Error(
      `POST /api/admin/e2e-stripe-payment-failed ${resp.status()}: ${await resp.text()}`,
    );
  }
}

export { expect };
