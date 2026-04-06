"""User store configuration: names, keys, modes, API integrations."""

from __future__ import annotations

from sqlalchemy import text

from mealrunner.database import DictConnection


def list_stores(conn: DictConnection, user_id: str = "default") -> list[dict]:
    """Return configured stores for a user."""
    rows = conn.execute(
        text("SELECT id, name, key, mode, api, location_id FROM stores WHERE user_id = :user_id ORDER BY id"),
        {"user_id": user_id},
    ).fetchall()
    return [dict(r) for r in rows]


def get_kroger_location_id(conn: DictConnection, user_id: str) -> str | None:
    """Get the Kroger location_id for a user, or None if not set."""
    row = conn.execute(
        text("SELECT location_id FROM stores WHERE user_id = :user_id AND api = 'kroger' AND location_id != '' LIMIT 1"),
        {"user_id": user_id},
    ).fetchone()
    return row["location_id"] if row else None


def set_kroger_location_id(conn: DictConnection, user_id: str, location_id: str) -> bool:
    """Set the location_id on the user's Kroger store. Creates a Kroger store if none exists."""
    row = conn.execute(
        text("SELECT id FROM stores WHERE user_id = :user_id AND api = 'kroger' LIMIT 1"),
        {"user_id": user_id},
    ).fetchone()
    if row:
        conn.execute(
            text("UPDATE stores SET location_id = :loc WHERE id = :id"),
            {"loc": location_id, "id": row["id"]},
        )
    else:
        conn.execute(
            text("""INSERT INTO stores (user_id, name, key, mode, api, location_id)
                    VALUES (:user_id, 'Kroger', 'k', 'in-person', 'kroger', :loc)"""),
            {"user_id": user_id, "loc": location_id},
        )
    conn.commit()
    return True


def add_store(conn: DictConnection, user_id: str, name: str, key: str,
              mode: str = "in-person", api: str = "none") -> dict:
    """Add a new store. key is the short letter used in prompts."""
    existing = conn.execute(
        text("SELECT id FROM stores WHERE user_id = :user_id AND key = :key"),
        {"user_id": user_id, "key": key.lower()},
    ).fetchone()
    if existing:
        raise ValueError(f"Key '{key}' already in use")

    row = conn.execute(
        text("""INSERT INTO stores (user_id, name, key, mode, api)
                VALUES (:user_id, :name, :key, :mode, :api) RETURNING id"""),
        {"user_id": user_id, "name": name, "key": key.lower(), "mode": mode, "api": api},
    ).fetchone()
    conn.commit()
    return {"id": row["id"], "name": name, "key": key.lower(), "mode": mode, "api": api}


def remove_store(conn: DictConnection, user_id: str, key: str) -> str | None:
    """Remove a store by key. Returns removed store name or None."""
    row = conn.execute(
        text("SELECT name FROM stores WHERE user_id = :user_id AND key = :key"),
        {"user_id": user_id, "key": key.lower()},
    ).fetchone()
    if not row:
        return None
    conn.execute(
        text("DELETE FROM stores WHERE user_id = :user_id AND key = :key"),
        {"user_id": user_id, "key": key.lower()},
    )
    conn.commit()
    return row["name"]


def get_store_by_key(conn: DictConnection, user_id: str, key: str) -> dict | None:
    """Look up a store by its short key."""
    row = conn.execute(
        text("SELECT id, name, key, mode, api FROM stores WHERE user_id = :user_id AND key = :key"),
        {"user_id": user_id, "key": key.lower()},
    ).fetchone()
    return dict(row) if row else None


def prompt_keys_help(stores: list[dict]) -> str:
    """Build a prompt hint string like '(k = Kroger, s = Sam's Club)'."""
    parts = [f"{s['key']} = {s['name']}" for s in stores]
    return f"({', '.join(parts)})"


def refresh_nearby_stores(conn: DictConnection, user_id: str, own_location_id: str, zip_code: str) -> int:
    """Cache the 3 nearest Kroger stores (excluding user's own). Returns count saved."""
    from mealrunner.kroger import search_kroger_locations

    try:
        locations = search_kroger_locations(zip_code, limit=5)
    except Exception:
        return 0

    conn.execute(text("DELETE FROM nearby_stores WHERE user_id = :uid"), {"uid": user_id})
    rank = 0
    for loc in locations:
        if loc["location_id"] == own_location_id:
            continue
        rank += 1
        if rank > 3:
            break
        conn.execute(
            text("""INSERT INTO nearby_stores (user_id, location_id, name, address, rank)
                VALUES (:uid, :loc, :name, :addr, :rank)
                ON CONFLICT(user_id, location_id) DO UPDATE SET
                    name = excluded.name, address = excluded.address, rank = excluded.rank"""),
            {"uid": user_id, "loc": loc["location_id"], "name": loc["name"],
             "addr": loc["address"], "rank": rank},
        )
    conn.commit()
    return rank


def save_nearby_stores(conn: DictConnection, user_id: str, stores: list[dict]) -> int:
    """Save user-selected nearby stores (replaces any existing). Returns count saved."""
    conn.execute(text("DELETE FROM nearby_stores WHERE user_id = :uid"), {"uid": user_id})
    for rank, store in enumerate(stores, 1):
        conn.execute(
            text("""INSERT INTO nearby_stores (user_id, location_id, name, address, rank)
                VALUES (:uid, :loc, :name, :addr, :rank)
                ON CONFLICT(user_id, location_id) DO UPDATE SET
                    name = excluded.name, address = excluded.address, rank = excluded.rank"""),
            {"uid": user_id, "loc": store["location_id"], "name": store["name"],
             "addr": store.get("address", ""), "rank": rank},
        )
    conn.commit()
    return len(stores)


def get_nearby_stores(conn: DictConnection, user_id: str) -> list[dict]:
    """Return cached nearby stores for a user, ordered by rank."""
    rows = conn.execute(
        text("SELECT location_id, name, address, rank FROM nearby_stores WHERE user_id = :uid ORDER BY rank"),
        {"uid": user_id},
    ).fetchall()
    return [dict(r) for r in rows]
