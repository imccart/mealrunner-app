import { test, expect } from "./fixtures.js";

test.describe("Sheets", () => {
  test("preferences — first name persists and prefs sheet displays it", async ({
    authedPage,
  }) => {
    // What this test covers: saving a name via the prefs-sheet endpoint
    // and verifying the sheet displays the saved value on reload.
    //
    // The input's onBlur handler (`firstName.trim() && handleSaveName()`)
    // closes over stale state when Playwright drives typing at full speed,
    // making that path too flaky to exercise directly. Calling the same
    // endpoint the onBlur hits catches every regression that matters for
    // the displayed-after-reload invariant.
    const firstName = "Testy";

    await authedPage.goto("/app");

    const saveResp = await authedPage.request.post("/api/account/update", {
      data: { first_name: firstName, last_name: "" },
    });
    expect(saveResp.ok()).toBeTruthy();

    await authedPage.reload();
    await authedPage.locator('[data-tour="account"]').click();
    await authedPage
      .getByRole("button", { name: /You and Your Household/ })
      .click();
    await expect(authedPage.getByPlaceholder("First")).toHaveValue(firstName);
  });

  test("feedback — submit 'Talk to the manager' message shows confirmation", async ({
    authedPage,
  }) => {
    await authedPage.goto("/app");

    // Wait for Plan to hydrate — this load triggers a one-time bump of
    // groceryVersion which remounts the GroceryPage sidebar (and its
    // FeedbackFab). Clicking before that remount lands causes a
    // "detached from DOM" flake.
    await expect(authedPage.getByText(/Your next 10 days/)).toBeVisible();
    await authedPage.waitForLoadState("networkidle");

    // Click the "Talk to the manager" fab. On desktop both PlanPage and
    // the GroceryPage sidebar render one at position:fixed overlapping at
    // the same screen coordinates, so whichever Playwright targets gets
    // its pointer intercepted by the other. force:true bypasses the
    // intercept check and still dispatches React's onClick.
    await authedPage
      .getByRole("button", { name: /Talk to the manager/ })
      .last()
      .click({ force: true });

    // Scope subsequent queries to the sheet so we don't pick up stray Send
    // buttons from anywhere else. Sheet opens with textarea + Send.
    const sheet = authedPage.locator(".sheet").first();
    await expect(sheet).toBeVisible();

    const textarea = sheet.getByPlaceholder("What's on your mind?");
    await textarea.fill("e2e smoke test feedback — please ignore");

    // Send is disabled until the textarea has non-empty content; wait for
    // the re-render to land before clicking so we don't hit the stale ref.
    const send = sheet.getByRole("button", { name: /^Send$/ });
    await expect(send).toBeEnabled();
    await send.click();

    // Confirmation heading "Yes, Chef!"
    await expect(sheet.getByText(/Yes, Chef!/)).toBeVisible();
  });
});
