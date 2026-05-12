"""Tests for grocery list generation."""

from mealrunner.grocery import build_grocery_list, split_by_store
from mealrunner.staples import KEEP_ON_HAND, add_staple
from mealrunner.planner import fill_dates, week_range


def _make_meals(conn, week_of="2026-03-02"):
    start, end = week_range(week_of)
    meals = fill_dates(conn, start, end)
    return meals, start, end


def test_grocery_list_has_items(conn):
    meals, start, end = _make_meals(conn)
    gl = build_grocery_list(conn, meals, start, end)
    assert len(gl.items) > 0


def test_grocery_excludes_baseline_staples(conn):
    meals, start, end = _make_meals(conn)
    gl = build_grocery_list(conn, meals, start, end)
    baseline = {"salt", "black pepper", "garlic powder", "olive oil", "vegetable oil",
                "onion powder", "cumin", "chili powder", "italian seasoning", "paprika",
                "soy sauce", "worcestershire sauce"}
    for item in gl.items:
        assert item.ingredient_name not in baseline, f"Baseline staple {item.ingredient_name} in grocery list"


def test_grocery_excludes_staples(conn):
    """Items the user has marked as a staple (either mode) are filtered out
    of the meal-driven list entirely. Presence filter — user adds them via
    /grocery/add-staples when they actually need them."""
    meals, start, end = _make_meals(conn)
    gl_before = build_grocery_list(conn, meals, start, end, user_id="default")

    if gl_before.items:
        target = gl_before.items[0]
        add_staple(conn, "default", target.ingredient_name, KEEP_ON_HAND)

        gl_after = build_grocery_list(conn, meals, start, end, user_id="default")
        after_names = {i.ingredient_name for i in gl_after.items}
        assert target.ingredient_name not in after_names


def test_split_by_store(conn):
    meals, start, end = _make_meals(conn)
    gl = build_grocery_list(conn, meals, start, end)
    stores = split_by_store(gl)
    assert isinstance(stores, dict)
    for store, items in stores.items():
        assert store in ("sams", "kroger", "either")
        assert len(items) > 0


def test_no_duplicate_ingredients(conn):
    meals, start, end = _make_meals(conn)
    gl = build_grocery_list(conn, meals, start, end)
    names = [i.ingredient_name for i in gl.items]
    assert len(names) == len(set(names)), "Duplicate ingredients in grocery list"
