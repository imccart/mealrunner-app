import { test, expect, makeTestEmail } from "./fixtures.js";

// Exercises the production magic-link auth path end-to-end:
//   POST /api/auth/login (whitelist + token issuance + email send)
//     ↓
//   GET  /api/auth/verify?token=...  (token consume + session cookie + redirect to /app)
//     ↓
//   GET  /api/auth/me  (session cookie authenticates)
//
// All other tests use the e2e-login bypass; this is the only test that
// covers the real flow friends-beta users will hit. For email capture, we
// hit /api/admin/e2e-magic-link-token (gated by PLAYWRIGHT_TEST_SECRET +
// the e2e email domain) rather than intercepting an inbox.

const TEST_SECRET = process.env.PLAYWRIGHT_TEST_SECRET || "";

test.describe("Magic-link auth flow", () => {
  test("login → verify token → session cookie authenticates", async ({
    browser,
  }) => {
    const context = await browser.newContext();
    const page = await context.newPage();
    const email = makeTestEmail();

    try {
      // ── Step 1: request the magic link ──
      const loginResp = await page.request.post("/api/auth/login", {
        data: { email },
      });
      expect(loginResp.ok()).toBeTruthy();
      const loginJson = await loginResp.json();
      expect(loginJson.ok, "login accepted (e2e emails are auto-allowed)").toBe(
        true,
      );

      // ── Step 2: fetch the issued token ──
      const tokenResp = await page.request.post(
        "/api/admin/e2e-magic-link-token",
        { data: { secret: TEST_SECRET, email } },
      );
      expect(tokenResp.ok()).toBeTruthy();
      const { token } = await tokenResp.json();
      expect(token, "magic-link token was issued").toBeTruthy();

      // ── Step 3: verify the token (production code path) ──
      // /api/auth/verify redirects to /app on success, /app?auth=expired on
      // failure. Following the redirect lands us on the SPA shell.
      const verifyResp = await page.request.get(
        `/api/auth/verify?token=${encodeURIComponent(token)}`,
      );
      // Final URL after redirect chain should be /app (no auth=expired).
      const finalUrl = verifyResp.url();
      expect(
        finalUrl.includes("/app"),
        `verify redirected to /app (got ${finalUrl})`,
      ).toBeTruthy();
      expect(
        finalUrl.includes("auth=expired"),
        "verify did NOT redirect to /app?auth=expired",
      ).toBeFalsy();

      // ── Step 4: session cookie is set; /api/auth/me returns the user ──
      const meResp = await page.request.get("/api/auth/me");
      expect(meResp.status(), "/api/auth/me returns 200 when authed").toBe(200);
      const me = await meResp.json();
      expect(me.email, "session is bound to the right user").toBe(email);

      // ── Step 5: token is single-use — re-verifying outside the grace
      //    window must not produce a new session. We can't time-travel for
      //    the 60s grace, but we can confirm the same token doesn't issue
      //    fresh tokens from the admin endpoint (used_at is set).
      const tokenResp2 = await page.request.post(
        "/api/admin/e2e-magic-link-token",
        { data: { secret: TEST_SECRET, email } },
      );
      const { token: token2 } = await tokenResp2.json();
      expect(
        token2,
        "after consume, no unconsumed token remains for this email",
      ).toBeNull();
    } finally {
      await context.close();
    }
  });

  test("verify with bad token redirects to /app?auth=expired", async ({
    browser,
  }) => {
    const context = await browser.newContext();
    const page = await context.newPage();
    try {
      const resp = await page.request.get(
        "/api/auth/verify?token=not-a-real-token",
      );
      const finalUrl = resp.url();
      expect(
        finalUrl.includes("auth=expired"),
        `bad token lands on /app?auth=expired (got ${finalUrl})`,
      ).toBeTruthy();

      // No session cookie was set, so /api/auth/me should be 401.
      const meResp = await page.request.get("/api/auth/me");
      expect(meResp.status()).toBe(401);
    } finally {
      await context.close();
    }
  });
});
