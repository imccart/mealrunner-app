"""Pantry inventory CRUD."""

from __future__ import annotations

import sqlite3

from souschef.models import PantryItem


def list_pantry(conn: sqlite3.Connection) -> list[PantryItem]:
    rows = conn.execute(
        """SELECT p.*, i.name AS ingredient_name
           FROM pantry p
           JOIN ingredients i ON i.id = p.ingredient_id
           ORDER BY i.name"""
    ).fetchall()
    return [
        PantryItem(
            id=r["id"],
            ingredient_id=r["ingredient_id"],
            quantity=r["quantity"],
            unit=r["unit"],
            updated_at=r["updated_at"],
            ingredient_name=r["ingredient_name"],
        )
        for r in rows
    ]


def add_pantry_item(
    conn: sqlite3.Connection, ingredient_name: str, quantity: float, unit: str
) -> PantryItem | None:
    ing = conn.execute(
        "SELECT id FROM ingredients WHERE name = ?", (ingredient_name,)
    ).fetchone()
    if ing is None:
        return None

    conn.execute(
        """INSERT INTO pantry (ingredient_id, quantity, unit)
           VALUES (?, ?, ?)
           ON CONFLICT(ingredient_id) DO UPDATE SET
               quantity = quantity + excluded.quantity,
               updated_at = datetime('now')""",
        (ing["id"], quantity, unit),
    )
    conn.commit()

    row = conn.execute(
        """SELECT p.*, i.name AS ingredient_name
           FROM pantry p JOIN ingredients i ON i.id = p.ingredient_id
           WHERE p.ingredient_id = ?""",
        (ing["id"],),
    ).fetchone()

    return PantryItem(
        id=row["id"],
        ingredient_id=row["ingredient_id"],
        quantity=row["quantity"],
        unit=row["unit"],
        updated_at=row["updated_at"],
        ingredient_name=row["ingredient_name"],
    )


def set_pantry_item(
    conn: sqlite3.Connection, ingredient_name: str, quantity: float, unit: str
) -> PantryItem | None:
    ing = conn.execute(
        "SELECT id FROM ingredients WHERE name = ?", (ingredient_name,)
    ).fetchone()
    if ing is None:
        return None

    if quantity <= 0:
        conn.execute("DELETE FROM pantry WHERE ingredient_id = ?", (ing["id"],))
        conn.commit()
        return PantryItem(id=None, ingredient_id=ing["id"], quantity=0, unit=unit,
                          ingredient_name=ingredient_name)

    conn.execute(
        """INSERT INTO pantry (ingredient_id, quantity, unit)
           VALUES (?, ?, ?)
           ON CONFLICT(ingredient_id) DO UPDATE SET
               quantity = excluded.quantity,
               updated_at = datetime('now')""",
        (ing["id"], quantity, unit),
    )
    conn.commit()

    row = conn.execute(
        """SELECT p.*, i.name AS ingredient_name
           FROM pantry p JOIN ingredients i ON i.id = p.ingredient_id
           WHERE p.ingredient_id = ?""",
        (ing["id"],),
    ).fetchone()

    return PantryItem(
        id=row["id"],
        ingredient_id=row["ingredient_id"],
        quantity=row["quantity"],
        unit=row["unit"],
        updated_at=row["updated_at"],
        ingredient_name=row["ingredient_name"],
    )


def clear_pantry(conn: sqlite3.Connection) -> int:
    cursor = conn.execute("DELETE FROM pantry")
    conn.commit()
    return cursor.rowcount


def get_pantry_quantity(conn: sqlite3.Connection, ingredient_id: int) -> float:
    row = conn.execute(
        "SELECT quantity FROM pantry WHERE ingredient_id = ?", (ingredient_id,)
    ).fetchone()
    return row["quantity"] if row else 0.0
