import { test, expect } from "./fixtures.js";
import { addRegular, addRegularsToGrocery } from "./helpers.js";

// These tests guard the household-isolation invariant: a logged-in user can
// neither read nor mutate another household's data via list endpoints or
// ID-parameterized routes. The middleware resolves member→owner so all
// queries scope to the household-owner's user_id; this test fails loudly if
// any future refactor breaks that scoping.

function flattenActive(groc) {
  return Object.values(groc.items_by_group || {}).flat();
}

test.describe("Cross-household isolation", () => {
  test("user B cannot read or mutate user A's data", async ({
    authedPage,
    makeAuthedUser,
  }) => {
    const aPage = authedPage;

    // ── A creates content in their own household ──
    const recipeResp = await aPage.request.post("/api/recipes", {
      data: { name: "A-only Test Meal", recipe_type: "meal" },
    });
    expect(recipeResp.ok()).toBeTruthy();
    const aRecipeId = (await recipeResp.json()).id;
    expect(aRecipeId).toBeTruthy();

    await addRegular(aPage, "A-only Beans", "Pantry");
    await addRegularsToGrocery(aPage, ["A-only Beans"]);

    const aGrocery = await (await aPage.request.get("/api/grocery")).json();
    const aRow = flattenActive(aGrocery).find(
      (i) => i.name.toLowerCase() === "a-only beans",
    );
    expect(aRow, "A's grocery row exists").toBeTruthy();
    const aGroceryId = aRow.id;

    // ── B logs in as a fresh user (separate household) ──
    const { page: bPage } = await makeAuthedUser({ skip_onboarding: true });

    // B's list endpoints must not reveal A's data
    const bRecipes = (await (await bPage.request.get("/api/recipes")).json())
      .recipes || [];
    expect(
      bRecipes.find((r) => r.id === aRecipeId),
      "B should not see A's recipe via /api/recipes",
    ).toBeUndefined();

    const bGrocery = await (await bPage.request.get("/api/grocery")).json();
    expect(
      flattenActive(bGrocery).find((i) => i.id === aGroceryId),
      "B should not see A's grocery row via /api/grocery",
    ).toBeUndefined();

    // ── B attempts to mutate A's rows by ID ──
    // Each route is gated by `WHERE id = :id AND user_id = :user_id`, so
    // these must silently no-op rather than mutate A's row.
    await bPage.request.post(`/api/grocery/buy-elsewhere/${aGroceryId}`);
    await bPage.request.post(`/api/grocery/toggle/${aGroceryId}`);
    await bPage.request.post(`/api/grocery/have-it/${aGroceryId}`);
    await bPage.request.delete(`/api/grocery/item/${aGroceryId}`);

    // Recipe deletion explicitly returns ok:false because the SELECT-gate
    // fails ownership.
    const cdel = await bPage.request.delete(`/api/recipes/${aRecipeId}`);
    const cdelJson = await cdel.json();
    expect(cdelJson.ok, "B's delete on A's recipe is rejected").toBeFalsy();

    // ── A's data must be intact ──
    const aGroceryAfter = await (await aPage.request.get("/api/grocery")).json();
    const aRowAfter = flattenActive(aGroceryAfter).find(
      (i) => i.id === aGroceryId,
    );
    expect(aRowAfter, "A's grocery row still exists after B's attacks").toBeTruthy();
    expect(Boolean(aRowAfter.checked), "A's row not toggled by B").toBeFalsy();
    expect(
      Boolean(aRowAfter.buy_elsewhere),
      "A's row not buy-elsewhere'd by B",
    ).toBeFalsy();
    expect(Boolean(aRowAfter.have_it), "A's row not have-it'd by B").toBeFalsy();

    const aRecipesAfter = (await (await aPage.request.get("/api/recipes")).json())
      .recipes || [];
    expect(
      aRecipesAfter.find((r) => r.id === aRecipeId),
      "A's recipe still exists",
    ).toBeTruthy();
  });

  test("owner removes a member: shared access severs, permission gates enforced", async ({
    authedPage,
    makeAuthedUser,
  }) => {
    const aPage = authedPage;

    // A invites C (must use a custom email so we can sign C in to it)
    const cEmail = `e2e-${Date.now()}-${Math.random()
      .toString(36)
      .slice(2, 6)}-c@mealrunner-test.invalid`;
    const inv = await aPage.request.post("/api/household/invite", {
      data: { email: cEmail },
    });
    expect(inv.ok()).toBeTruthy();

    // C signs in (skip_onboarding=false so accept-invite path works cleanly,
    // matching the existing household.spec.js pattern)
    const { page: cPage } = await makeAuthedUser({
      email: cEmail,
      skip_onboarding: false,
    });
    const accept = await cPage.request.post("/api/household/accept-invite");
    expect(accept.ok()).toBeTruthy();

    // After joining, C's queries resolve to A's household. Confirm the share
    // is live: A creates a recipe, C sees it.
    const recipeResp = await aPage.request.post("/api/recipes", {
      data: { name: "Shared Test Meal", recipe_type: "meal" },
    });
    const aRecipeId = (await recipeResp.json()).id;

    const cRecipesShared = (
      await (await cPage.request.get("/api/recipes")).json()
    ).recipes || [];
    expect(
      cRecipesShared.find((r) => r.id === aRecipeId),
      "C sees A's recipe while sharing the household",
    ).toBeTruthy();

    // Find C's user_id from the household members listing
    const members =
      (await (await aPage.request.get("/api/household/members")).json())
        .members || [];
    const cMember = members.find((m) => m.email === cEmail);
    const ownerMember = members.find((m) => m.role === "owner");
    expect(cMember, "C is in the members list").toBeTruthy();
    expect(ownerMember, "owner is in the members list").toBeTruthy();

    // ── Permission gates ──
    // Non-owner cannot remove anyone (403)
    const cTryRemove = await cPage.request.delete(
      `/api/household/members/${ownerMember.user_id}`,
    );
    expect(
      cTryRemove.status(),
      "non-owner gets 403 attempting remove",
    ).toBe(403);

    // Owner cannot remove self
    const ownerSelfRm = await aPage.request.delete(
      `/api/household/members/${ownerMember.user_id}`,
    );
    const ownerSelfJson = await ownerSelfRm.json();
    expect(ownerSelfJson.ok, "owner cannot remove self").toBeFalsy();

    // ── Owner removes C ──
    const removeResp = await aPage.request.delete(
      `/api/household/members/${cMember.user_id}`,
    );
    expect(removeResp.ok()).toBeTruthy();
    expect((await removeResp.json()).ok).toBeTruthy();

    // C is now in their own (empty) household — A's recipe is no longer visible
    const cRecipesAfter = (
      await (await cPage.request.get("/api/recipes")).json()
    ).recipes || [];
    expect(
      cRecipesAfter.find((r) => r.id === aRecipeId),
      "C does not see A's recipe after removal",
    ).toBeUndefined();

    // C cannot mutate A's recipe by id either
    const cDel = await cPage.request.delete(`/api/recipes/${aRecipeId}`);
    expect(
      (await cDel.json()).ok,
      "C cannot delete A's recipe after removal",
    ).toBeFalsy();

    // A's recipe still exists
    const aRecipesFinal = (
      await (await aPage.request.get("/api/recipes")).json()
    ).recipes || [];
    expect(
      aRecipesFinal.find((r) => r.id === aRecipeId),
      "A's recipe survived",
    ).toBeTruthy();
  });
});
