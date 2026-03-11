"""Recurring household essentials — items bought regularly, not tied to meals."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class Essential:
    id: int | None
    name: str
    shopping_group: str
    store_pref: str
    active: bool = True


def list_essentials(conn: sqlite3.Connection, active_only: bool = True) -> list[Essential]:
    query = "SELECT * FROM essentials"
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY shopping_group, name"
    rows = conn.execute(query).fetchall()
    return [
        Essential(
            id=r["id"],
            name=r["name"],
            shopping_group=r["shopping_group"],
            store_pref=r["store_pref"],
            active=bool(r["active"]),
        )
        for r in rows
    ]


def add_essential(
    conn: sqlite3.Connection, name: str, shopping_group: str, store_pref: str = "either"
) -> Essential:
    cursor = conn.execute(
        """INSERT INTO essentials (name, shopping_group, store_pref)
           VALUES (?, ?, ?)
           ON CONFLICT(name) DO UPDATE SET active = 1, shopping_group = excluded.shopping_group, store_pref = excluded.store_pref""",
        (name, shopping_group, store_pref),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM essentials WHERE name = ?", (name,)).fetchone()
    return Essential(
        id=row["id"],
        name=row["name"],
        shopping_group=row["shopping_group"],
        store_pref=row["store_pref"],
        active=bool(row["active"]),
    )


def remove_essential(conn: sqlite3.Connection, name: str) -> bool:
    cursor = conn.execute("UPDATE essentials SET active = 0 WHERE name = ? AND active = 1", (name,))
    conn.commit()
    return cursor.rowcount > 0


def get_active_essentials_by_group(conn: sqlite3.Connection) -> dict[str, list[Essential]]:
    items = list_essentials(conn, active_only=True)
    groups: dict[str, list[Essential]] = {}
    for item in items:
        groups.setdefault(item.shopping_group, []).append(item)
    return groups
