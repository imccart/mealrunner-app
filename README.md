# MealRunner

Meal planning and grocery logistics for families. Plan meals, build a grocery list, order from Kroger, reconcile what arrived.

**Shopping logistics with meals as the input** — not a recipe app.

## Features

- **10-day rolling meal plan** with drag-and-drop reordering, side dishes, and freeform meals (Eating Out, Leftovers)
- **Grocery list** auto-generated from planned meals, plus regulars and pantry items
- **Build My List** multi-step flow: carryover from last trip, regulars, pantry restock
- **Kroger integration** for product search, NOVA/Nutri-Score badges, and cart submission
- **Learning** suggests frequently-bought items as regulars
- **Onboarding** 4-step setup for new users

## Tech Stack

- **Backend:** Python 3.10+, FastAPI, SQLAlchemy Core
- **Frontend:** React + Vite
- **Database:** SQLite (local) or PostgreSQL (production) via `DATABASE_URL`
- **APIs:** Kroger, Open Food Facts, Google Sheets (optional)

## Quick Start

```bash
# Backend
pip install -e ".[web,kroger]"
uvicorn mealrunner.web.app:app --reload --port 8000

# Frontend
cd frontend
npm install && npm run dev

# Visit http://localhost:5173
```

## Deployment

See [DEPLOY.md](DEPLOY.md) for Railway deployment instructions.

## License

All rights reserved. This is a personal project by [Aletheia](https://github.com/aletheia).
