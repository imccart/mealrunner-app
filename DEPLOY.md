# MealRunner — Deployment Guide

## Local Development

```bash
# Install dependencies
cd code
pip install -e ".[web,kroger,reconcile]"

# Build frontend
cd ../frontend
npm install && npm run build

# Start server
cd ..
uvicorn mealrunner.web.app:app --reload --port 8000

# Access at http://localhost:8000/app
# Vite dev server (hot reload): cd frontend && npm run dev → localhost:5173
```

## Railway Deployment

### 1. Create Project

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login and init
railway login
railway init
```

### 2. Add PostgreSQL

```bash
railway add --plugin postgresql
```

Railway automatically sets `DATABASE_URL` — the app reads it and switches from SQLite to PostgreSQL.

### 3. Set Environment Variables

```bash
# Required: none (DATABASE_URL is auto-set by Railway PostgreSQL plugin)

# Optional:
railway variables set KROGER_CLIENT_ID=xxx
railway variables set KROGER_CLIENT_SECRET=xxx
railway variables set KROGER_LOCATION_ID=xxx
railway variables set ANTHROPIC_API_KEY=xxx
```

### 4. Build Frontend Before Deploy

The React frontend must be pre-built — Railway doesn't run `npm` during Python builds.

```bash
cd frontend
npm install && npm run build
# dist/ is served by FastAPI at /app
```

Ensure `frontend/dist/` is committed (or add a build step in `railway.toml`).

### 5. Deploy

```bash
railway up
```

Or connect your GitHub repo for automatic deploys on push.

### 6. Verify

```bash
# Health check
curl https://your-app.up.railway.app/health

# App
open https://your-app.up.railway.app/app
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Auto (Railway) | PostgreSQL connection string |
| `PORT` | Auto (Railway) | Server port (default: 8000) |
| `KROGER_CLIENT_ID` | No | Kroger API client ID |
| `KROGER_CLIENT_SECRET` | No | Kroger API client secret |
| `KROGER_LOCATION_ID` | No | Kroger store location |
| `ANTHROPIC_API_KEY` | No | For receipt parsing |

## Database

- **Local**: SQLite at `~/.mealrunner/mealrunner.db` (auto-created)
- **Production**: PostgreSQL via `DATABASE_URL`
- Tables auto-create on first request (`create_all`)
- Migrations run automatically (additive `ALTER TABLE` for new columns)
- Seed data loads from `data/*.yaml` if recipes table is empty

## Architecture

```
Client (React SPA at /app)
  ↓ JSON API
FastAPI (mealrunner.web.app)
  ↓ SQLAlchemy Core
PostgreSQL (Railway) or SQLite (local)
```

The `database.py` module handles dialect differences:
- `postgres://` → `postgresql://` URL fix
- SQLite PRAGMA foreign_keys via engine event listener
- `DictConnection` wrapper for backward-compatible `row["column"]` access
