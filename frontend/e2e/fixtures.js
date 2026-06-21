import { test as base, expect, request } from "@playwright/test";

const TEST_SECRET = process.env.PLAYWRIGHT_TEST_SECRET || "";

if (!TEST_SECRET) {
  console.warn(
    "[e2e] PLAYWRIGHT_TEST_SECRET is not set — tests that rely on the auth bypass will fail.",
  );
}

function makeTestEmail() {
  const ts = Date.now();
  const rand = Math.random().toString(36).slice(2, 8);
  return `e2e-${ts}-${rand}@mealrunner-test.invalid`;
}

async function loginAs(page, email) {
  // Retry on 5xx with a short backoff. The first request of a CI run can
  // hit a still-warming Railway container, the DB pool's first slot can
  // take a moment to handshake, and either path can surface as a transient
  // 500. Retries here are cheap and turn cold-start noise into a non-event.
  let lastResp;
  let lastBody = "";
  for (let attempt = 1; attempt <= 4; attempt++) {
    lastResp = await page.request.post("/api/auth/e2e-login", {
      data: { email, secret: TEST_SECRET },
    });
    if (lastResp.ok()) return await lastResp.json();
    lastBody = await lastResp.text();
    // Only retry transient server failures, not auth/validation errors
    if (lastResp.status() < 500) break;
    await new Promise((r) => setTimeout(r, attempt * 1500));
  }
  throw new Error(`e2e-login failed (${lastResp.status()}): ${lastBody}`);
}

async function deleteAllTestUsers(baseURL) {
  const ctx = await request.newContext({ baseURL });
  try {
    const resp = await ctx.post("/api/admin/e2e-cleanup", {
      data: { secret: TEST_SECRET },
    });
    if (!resp.ok()) {
      console.warn(`[e2e] cleanup returned ${resp.status()}`);
    }
  } finally {
    await ctx.dispose();
  }
}

export const test = base.extend({
  testEmail: async ({}, use) => {
    await use(makeTestEmail());
  },
  authedPage: async ({ page, testEmail }, use) => {
    await loginAs(page, testEmail);
    await use(page);
  },
  // A factory that returns { page, email } for additional fresh users in
  // the same test. Used by the household-invite test which needs two
  // authed sessions. Contexts are closed in teardown.
  makeAuthedUser: async ({ browser }, use) => {
    const created = [];
    async function create(opts = {}) {
      const context = await browser.newContext();
      const p = await context.newPage();
      const email = opts.email || makeTestEmail();
      const resp = await p.request.post("/api/auth/e2e-login", {
        data: { email, secret: TEST_SECRET, ...opts },
      });
      if (!resp.ok()) {
        throw new Error(
          `e2e-login failed (${resp.status()}): ${await resp.text()}`,
        );
      }
      created.push(context);
      return { page: p, email };
    }
    await use(create);
    for (const c of created) {
      try {
        await c.close();
      } catch {}
    }
  },
});

export { expect, deleteAllTestUsers, makeTestEmail };
