# Deploy Workflow

How code gets from edit → commit → Railway. Covers the two-machine sync rule, staging/production deploy mechanics, frontend build requirements, service worker caching, and the Railway CLI gap.

## Two-machine sync rule

The project folder lives in Synology Drive, two-way synced between **the desktop and the laptop**. The git repo (`.git/`) lives on the **laptop only**.

**Rules:**
- Edit code from any machine. Synology will sync the working tree.
- **Run `git` only from the laptop.** Never `git status / add / commit / push / branch / checkout` from the desktop — the desktop has no `.git`, and partial sync states cause tracking conflicts.
- Before committing on the laptop, confirm Synology sync is **idle on both ends** (no pending up/down arrows). Don't edit on the desktop while a commit is in flight on the laptop.
- Deploy (`git push origin master`, staging push) happens **from the laptop** after sync settles.
- If Claude is invoked on the desktop, expect "edits ready, commit from laptop" handoffs. Don't attempt git operations on desktop.

## Deploy targets

| Target | URL | Source branch |
|---|---|---|
| Production | getmealrunner.app | `master` |
| Staging | staging.getmealrunner.app | `staging` |

Both deploy automatically when their branch is pushed.

### Commands

```sh
# Deploy production:
git push origin master

# Deploy staging (force-update from master, since dev happens on master):
git branch -f staging master && git push origin staging

# Deploy both:
git push origin master staging   # after `git branch -f staging master`
```

**Always push to staging first for testing unless the user says otherwise.** Both deploys are independent and can go simultaneously.

### "Everything up-to-date" gotcha

When `git push origin staging` says "Everything up-to-date", Railway **won't trigger a new deploy** even if the branch was force-updated locally.

**Fix:** create an empty commit before pushing:
```sh
git commit --allow-empty -m "Trigger staging redeploy"
git push origin staging
```

Hit in session 43. Manual redeploy from the Railway dashboard also didn't pick up the latest in that case.

---

## Frontend changes require build + commit of `dist/`

The MealRunner backend serves the React app from `frontend/dist/` (mounted by `web/app.py`). The `dist/` directory is **committed to git**. Editing `frontend/src/` and pushing without rebuilding ships nothing — the old bundle still serves.

### Workflow when touching frontend code

```sh
# 1. Edit frontend/src/...
# 2. Build:
cd frontend && npm run build
# 3. Stage everything:
git add frontend/src/... frontend/dist
# 4. Commit + push as usual
```

Build is fast (~1.5s with Vite). The dist commit shows up as a renamed `assets/index-<hash>.js` plus tiny `index.html` change.

**Forgot in session 54** on the OrderPage end-state fix — committed only the source change, push went out, nothing changed in production. Had to rebuild and push a follow-up commit.

**Wrapup checkpoint:** whenever a session edits anything under `frontend/src/`, the wrapup must include a build step before commit.

---

## Service worker cache versioning

Bump `CACHE_NAME` in `frontend/public/sw.js` whenever **cache-first** static assets change (icons, manifest, etc.). Without a version bump, browsers won't install a new SW and stale cached assets persist indefinitely.

**Past failure:** After the ladle→runner-R rebrand, icons were correct in source/dist but mobile browsers kept serving the old ladle from SW cache because `mealrunner-v1` never changed.

Mitigated as of session 49 — icons and manifest now use **network-first**, so this is less critical going forward. But if any new cache-first assets are added, remember to bump the version on changes.

The build step doesn't auto-bump `sw.js` cache version.

---

## Railway CLI is NOT installed

The user does **not** have the Railway CLI on this machine. Do not run or suggest `railway run`, `railway link`, `railway logs`, `railway shell`, `railway --version`, or any other `railway ...` command.

Confirmed directly after a prior session tried `railway --version`. Repeatedly suggesting Railway CLI commands wastes turns.

### Alternative paths

For one-shot scripts that need to hit the prod database (migrations, dedup scripts):

1. **Run via Railway dashboard's "Run command" / shell** on the web service (browser-based, no CLI required).
2. **Connect to the prod DB directly** with the public PostgreSQL URL stored in the `database` agent and a Python REPL or psql — be aware the sandbox may block this even with verbal user approval (credentials-in-transcript hard rule).
3. **Have the user run the SQL** themselves via the Railway dashboard's database console, with me providing the exact statements.

For deploys: pushing to `master` triggers a Railway production deploy automatically; pushing to `staging` deploys to staging. No CLI needed.

For checking deploy status / logs: have the user open the Railway dashboard rather than trying CLI.

---

## E2E on push (advisory)

GitHub Actions runs the Playwright suite at `frontend/e2e/` on every push (`.github/workflows/e2e.yml`, advisory — won't block merge). Targets staging, so push to `staging` first if you want a clean signal.

Manual trigger: `gh workflow run e2e.yml --ref master` (workflow_dispatch).

Test users always `e2e-*@mealrunner-test.invalid`. Auth bypass via `POST /api/auth/e2e-login` is gated on `PLAYWRIGHT_TEST_SECRET` env var — production has no secret → endpoint 404s.

Run instructions and deferred tests: `frontend/e2e/README.md`.

### CI housekeeping (deferred)

Bump `actions/checkout@v4`, `setup-node@v4`, `upload-artifact@v4` to v5 / Node-24-compatible versions before **June 2, 2026** deprecation.
