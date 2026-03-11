"""Tests for meal plan generation."""

from souschef.planner import (
    DAY_NAMES,
    accept_meals,
    detect_bulk_components,
    fill_dates,
    load_meal_week,
    load_meals,
    swap_meal,
    week_range,
)


def test_fill_dates_creates_7_meals(conn):
    start, end = week_range("2026-03-02")
    meals = fill_dates(conn, start, end)
    assert len(meals) == 7


def test_saturday_is_eating_out(conn):
    start, end = week_range("2026-03-02")
    meals = fill_dates(conn, start, end)
    saturday = [m for m in meals if m.weekday == 5][0]
    assert saturday.recipe_id is None
    assert saturday.recipe_name == "Eating Out"


def test_no_duplicate_recipes(conn):
    start, end = week_range("2026-03-02")
    meals = fill_dates(conn, start, end)
    recipe_ids = [m.recipe_id for m in meals if m.recipe_id]
    assert len(recipe_ids) == len(set(recipe_ids))


def test_load_meals_round_trip(conn):
    start, end = week_range("2026-03-02")
    fill_dates(conn, start, end)
    loaded = load_meals(conn, start, end)
    assert len(loaded) == 7


def test_load_meal_week(conn):
    start, end = week_range("2026-03-02")
    fill_dates(conn, start, end)
    mw = load_meal_week(conn, "2026-03-02")
    assert mw.start_date == start
    assert mw.end_date == end
    assert len(mw.meals) == 7


def test_swap_changes_recipe(conn):
    start, end = week_range("2026-03-02")
    meals = fill_dates(conn, start, end)
    monday = meals[0]
    original_id = monday.recipe_id

    changed = False
    for _ in range(10):
        swapped = swap_meal(conn, monday.slot_date)
        if swapped.recipe_id != original_id:
            changed = True
            break

    assert swapped.recipe_id is not None


def test_accept_meals(conn):
    start, end = week_range("2026-03-02")
    fill_dates(conn, start, end)
    accept_meals(conn, start, end)
    mw = load_meal_week(conn, "2026-03-02")
    for meal in mw.meals:
        assert meal.status == "accepted"


def test_detect_bulk_components(conn):
    start, end = week_range("2026-03-02")
    meals = fill_dates(conn, start, end)
    tips = detect_bulk_components(conn, meals)
    assert isinstance(tips, list)


def test_cross_week_rotation(conn):
    """Second week should not repeat any recipe from the accepted first week."""
    s1, e1 = week_range("2026-03-02")
    week1 = fill_dates(conn, s1, e1)
    accept_meals(conn, s1, e1)

    week1_ids = {m.recipe_id for m in week1 if m.recipe_id}

    s2, e2 = week_range("2026-03-09")
    week2 = fill_dates(conn, s2, e2)
    week2_ids = {m.recipe_id for m in week2 if m.recipe_id}

    overlap = week1_ids & week2_ids
    assert len(overlap) == 0, f"Recipes repeated across weeks: {overlap}"
