"""Tests for pantry CRUD."""

from souschef.pantry import (
    add_pantry_item,
    clear_pantry,
    get_pantry_quantity,
    list_pantry,
    set_pantry_item,
)


def test_add_pantry_item(conn):
    item = add_pantry_item(conn, "chicken breast", 2.0, "lb")
    assert item is not None
    assert item.quantity == 2.0
    assert item.ingredient_name == "chicken breast"


def test_add_pantry_item_accumulates(conn):
    add_pantry_item(conn, "chicken breast", 2.0, "lb")
    item = add_pantry_item(conn, "chicken breast", 1.5, "lb")
    assert item.quantity == 3.5


def test_set_pantry_item(conn):
    add_pantry_item(conn, "chicken breast", 2.0, "lb")
    item = set_pantry_item(conn, "chicken breast", 5.0, "lb")
    assert item.quantity == 5.0


def test_set_pantry_item_removes_on_zero(conn):
    add_pantry_item(conn, "chicken breast", 2.0, "lb")
    item = set_pantry_item(conn, "chicken breast", 0, "lb")
    assert item.quantity == 0

    # Should not appear in list
    items = list_pantry(conn)
    assert len(items) == 0


def test_unknown_ingredient(conn):
    item = add_pantry_item(conn, "unicorn meat", 1.0, "lb")
    assert item is None


def test_list_pantry(conn):
    add_pantry_item(conn, "chicken breast", 2.0, "lb")
    add_pantry_item(conn, "ground beef", 1.0, "lb")
    items = list_pantry(conn)
    assert len(items) == 2


def test_clear_pantry(conn):
    add_pantry_item(conn, "chicken breast", 2.0, "lb")
    add_pantry_item(conn, "ground beef", 1.0, "lb")
    count = clear_pantry(conn)
    assert count == 2
    assert len(list_pantry(conn)) == 0


def test_get_pantry_quantity(conn):
    ing = conn.execute("SELECT id FROM ingredients WHERE name = 'chicken breast'").fetchone()
    assert get_pantry_quantity(conn, ing["id"]) == 0.0

    add_pantry_item(conn, "chicken breast", 3.0, "lb")
    assert get_pantry_quantity(conn, ing["id"]) == 3.0
