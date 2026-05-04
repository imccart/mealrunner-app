# Meals + Plan page

The data model and UX for picking dinners. Distinct from grocery: this doc is about **what's on the calendar**, not what's on the list.

## Data model

- **Flat meals model.** Individual meals on dates, no plan container. A "week" is a date range query.
- **Rolling 10-day window.** Today + 9 days. Past 7 days viewable via toggle (read-only).
- `recipe_type` column on `recipes`: `'meal'` or `'side'`.
- **Multiple sides per meal.** `meal_sides` junction table (up to 3).

## Plan page

- **Per-meal grocery toggle removed (session 53).** Meals auto-populate by default. "Nothing needed" in the meal action sheet marks a day as freeform (doesn't contribute ingredients).
- **4-tab nav:** Plan → Grocery → Order → Receipt. Desktop hides the Grocery tab — it's always visible as a sidebar.

## Recipes

- **Recipe library.** `user_id='__library__'` recipes for onboarding. Deep-copied to user on selection. Never visible in user views.
- **Inline recipe creation.** Typing a new name in `MealPickerSheet` creates a real recipe and auto-opens `MealIngredientsSheet`. "Eating Out" / "Nothing Planned" remain freeform (no recipe row).
- **Custom-typed sides** auto-create a recipe via `_resolve_side`.
- **Duplicate detection.** Meals/sides add inputs in My Kitchen show "Already exists" and block submission when name matches an existing recipe (case-insensitive).
- **Ingredient DB seeding on existing DBs.** `ensure_db_initialized` re-runs `_seed_ingredient_database` on every startup (idempotent, picks up new ingredients).

## Sides

Multi-select picker during meal selection. Up to 3 per meal via `meal_sides`. Custom side names auto-resolve to a new recipe.

## Day themes

Easy Monday, Taco Tuesday, Italian Wednesday, open Thursday, Outdoor Friday, Eating Out Saturday, Outdoor Sunday.

## Smart swap

Replacing a meal cross-references ingredients before suggesting removal/addition. Race condition fixed in session 18 (transaction wrapping + side ingredients).

## Meal ingredients sheet

`MealIngredientsSheet.jsx` — view/edit ingredients for the meal and all its sides from the Plan action sheet.

## Two-layer grocery model

Human layer (simple names, no qty) vs digital ordering layer (specific Kroger SKUs). The Plan page operates at the human layer; the Order page resolves to the digital layer.

## Notes

`notes` column on `meals`. Surfaced in Plan action sheet, Order "Picking for" section, and Walk the Aisles mode.
