import { test, expect } from "./fixtures.js";
import { makeTestEmail } from "./fixtures.js";

test.describe("Household", () => {
  test("invite → new user sees prompt → accepting lands them in app", async ({
    authedPage,
    makeAuthedUser,
  }) => {
    // Member's email is generated up front so the owner can invite it.
    const memberEmail = makeTestEmail();

    // Owner sends an invite via the household API.
    const inviteResp = await authedPage.request.post(
      "/api/household/invite",
      { data: { email: memberEmail } },
    );
    expect(inviteResp.ok()).toBeTruthy();

    // Member logs in WITHOUT skip_onboarding — the invite prompt takes
    // precedence over onboarding, and leaving the member without a
    // settings(key='onboarding_complete') row avoids a unique-constraint
    // collision when _process_household_invite migrates settings to the
    // household owner (who already has that row from skip_onboarding=true).
    const { page: memberPage } = await makeAuthedUser({
      email: memberEmail,
      skip_onboarding: false,
    });
    await memberPage.goto("/app");

    // The member should see the invite prompt, not onboarding or Plan.
    await expect(
      memberPage.getByText(/invited you to their household/),
    ).toBeVisible({ timeout: 10_000 });

    // Clicking Join calls accept-invite and then window.location.reload().
    await memberPage.getByRole("button", { name: /^Join$/ }).click();

    // The invite prompt should disappear (replaced by onboarding, since we
    // used skip_onboarding=false and the member still needs a welcome flow).
    await expect(
      memberPage.getByText(/invited you to their household/),
    ).toBeHidden({ timeout: 15_000 });

    // Backend assertion: the invite is no longer pending (it's been accepted
    // server-side). This is the load-bearing invariant — UI state after
    // accept varies (household-member onboarding vs Plan) but the invite
    // must resolve.
    const pendingResp = await memberPage.request.get(
      "/api/household/pending-invite",
    );
    expect(pendingResp.ok()).toBeTruthy();
    const pending = await pendingResp.json();
    expect(pending.invite).toBeNull();

    // And the status endpoint should now report the member is in a shared
    // household (household_member: true is only set when real_user_id !=
    // effective owner user_id).
    const statusResp = await memberPage.request.get("/api/onboarding/status");
    const status = await statusResp.json();
    expect(status.household_member).toBe(true);
  });
});
