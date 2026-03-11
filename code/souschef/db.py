"""SQLite schema, connection management, and seed data loading."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import yaml

DB_PATH = os.environ.get(
    "SOUSCHEF_DB", str(Path(__file__).resolve().parents[2] / "souschef.db")
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS ingredients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    category TEXT NOT NULL,
    aisle TEXT NOT NULL DEFAULT '',
    default_unit TEXT NOT NULL DEFAULT 'count',
    store_pref TEXT NOT NULL DEFAULT 'either',
    is_pantry_staple INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    cuisine TEXT NOT NULL DEFAULT 'any',
    effort TEXT NOT NULL DEFAULT 'medium',
    cleanup TEXT NOT NULL DEFAULT 'medium',
    outdoor INTEGER NOT NULL DEFAULT 0,
    kid_friendly INTEGER NOT NULL DEFAULT 1,
    premade INTEGER NOT NULL DEFAULT 0,
    prep_minutes INTEGER NOT NULL DEFAULT 0,
    cook_minutes INTEGER NOT NULL DEFAULT 0,
    servings INTEGER NOT NULL DEFAULT 4,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS recipe_ingredients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id INTEGER NOT NULL REFERENCES recipes(id),
    ingredient_id INTEGER NOT NULL REFERENCES ingredients(id),
    quantity REAL NOT NULL,
    unit TEXT NOT NULL,
    prep_note TEXT NOT NULL DEFAULT '',
    component TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS pantry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ingredient_id INTEGER NOT NULL REFERENCES ingredients(id) UNIQUE,
    quantity REAL NOT NULL,
    unit TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS meal_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_of TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS meal_plan_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES meal_plans(id),
    day_of_week INTEGER NOT NULL,
    recipe_id INTEGER REFERENCES recipes(id),
    status TEXT NOT NULL DEFAULT 'suggested',
    locked INTEGER NOT NULL DEFAULT 0,
    side TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS grocery_lists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES meal_plans(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS essentials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    shopping_group TEXT NOT NULL DEFAULT '',
    store_pref TEXT NOT NULL DEFAULT 'either',
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS grocery_list_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id INTEGER NOT NULL REFERENCES grocery_lists(id),
    ingredient_id INTEGER NOT NULL REFERENCES ingredients(id),
    total_quantity REAL NOT NULL,
    unit TEXT NOT NULL,
    store TEXT NOT NULL DEFAULT 'either',
    aisle TEXT NOT NULL DEFAULT '',
    from_pantry REAL NOT NULL DEFAULT 0,
    checked INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_date TEXT NOT NULL,
    recipe_id INTEGER REFERENCES recipes(id),
    recipe_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'suggested',
    side TEXT NOT NULL DEFAULT '',
    locked INTEGER NOT NULL DEFAULT 0,
    is_followup INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS grocery_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS grocery_run_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES grocery_runs(id),
    item_name TEXT NOT NULL,
    checked INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS product_preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    search_term TEXT NOT NULL,
    upc TEXT NOT NULL,
    product_description TEXT NOT NULL,
    size TEXT NOT NULL DEFAULT '',
    times_picked INTEGER NOT NULL DEFAULT 1,
    last_picked TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(search_term, upc)
);

CREATE TABLE IF NOT EXISTS product_ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT 'default',
    upc TEXT NOT NULL,
    product_description TEXT NOT NULL DEFAULT '',
    rating INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, upc)
);

CREATE TABLE IF NOT EXISTS regulars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    ingredient_id INTEGER REFERENCES ingredients(id),
    shopping_group TEXT NOT NULL DEFAULT '',
    store_pref TEXT NOT NULL DEFAULT 'either',
    active INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS product_scores (
    upc TEXT PRIMARY KEY,
    nova_group INTEGER,
    nutriscore TEXT NOT NULL DEFAULT '',
    price REAL,
    promo_price REAL,
    in_stock INTEGER,
    curbside INTEGER,
    score_fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    price_fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS grocery_trips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_type TEXT NOT NULL DEFAULT 'plan',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    start_date TEXT,
    end_date TEXT,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS trip_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id INTEGER NOT NULL REFERENCES grocery_trips(id),
    name TEXT NOT NULL,
    shopping_group TEXT NOT NULL DEFAULT 'Other',
    source TEXT NOT NULL DEFAULT 'extra',
    for_meals TEXT NOT NULL DEFAULT '',
    meal_count INTEGER NOT NULL DEFAULT 0,
    checked INTEGER NOT NULL DEFAULT 0,
    checked_at TEXT,
    UNIQUE(trip_id, name)
);
"""


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # Additive migrations for existing DBs
    for col, table in [
        ("root TEXT NOT NULL DEFAULT ''", "ingredients"),
        ("search_term TEXT NOT NULL DEFAULT ''", "essentials"),
        ("source TEXT NOT NULL DEFAULT 'picked'", "product_preferences"),
        ("order_id TEXT NOT NULL DEFAULT ''", "product_preferences"),
        ("rating INTEGER NOT NULL DEFAULT 0", "product_preferences"),
        ("start_date TEXT NOT NULL DEFAULT ''", "grocery_lists"),
        ("end_date TEXT NOT NULL DEFAULT ''", "grocery_lists"),
        ("on_grocery INTEGER NOT NULL DEFAULT 0", "meals"),
        ("ordered INTEGER NOT NULL DEFAULT 0", "trip_items"),
        ("ordered_at TEXT", "trip_items"),
        ("product_upc TEXT NOT NULL DEFAULT ''", "trip_items"),
        ("product_name TEXT NOT NULL DEFAULT ''", "trip_items"),
        ("product_brand TEXT NOT NULL DEFAULT ''", "trip_items"),
        ("product_size TEXT NOT NULL DEFAULT ''", "trip_items"),
        ("product_price REAL", "trip_items"),
        ("product_image TEXT NOT NULL DEFAULT ''", "trip_items"),
        ("selected_at TEXT", "trip_items"),
        ("order_source TEXT NOT NULL DEFAULT 'none'", "grocery_trips"),
        ("receipt_data TEXT", "grocery_trips"),
        ("receipt_parsed_at TEXT", "grocery_trips"),
        ("receipt_item TEXT NOT NULL DEFAULT ''", "trip_items"),
        ("receipt_price REAL", "trip_items"),
        ("receipt_upc TEXT NOT NULL DEFAULT ''", "trip_items"),
        ("receipt_status TEXT NOT NULL DEFAULT ''", "trip_items"),
    ]:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass  # column already exists

    # One-time migration: accepted meals → on_grocery = 1, then clear status
    # We mark migrated meals with status='migrated' so this doesn't re-run
    try:
        conn.execute("UPDATE meals SET on_grocery = 1, status = 'migrated' WHERE status = 'accepted'")
    except sqlite3.OperationalError:
        pass

    # Migrate existing ratings from product_preferences to product_ratings
    try:
        rows = conn.execute(
            "SELECT upc, product_description, rating FROM product_preferences WHERE rating != 0"
        ).fetchall()
        for r in rows:
            conn.execute(
                """INSERT OR IGNORE INTO product_ratings (user_id, upc, product_description, rating)
                   VALUES ('default', ?, ?, ?)""",
                (r["upc"], r["product_description"], r["rating"]),
            )
    except sqlite3.OperationalError:
        pass  # rating column doesn't exist yet on first run

    # Migrate essentials + pantry staples → regulars table
    _migrate_to_regulars(conn)

    # Migrate meal_plan_slots → meals table
    _migrate_slots_to_meals(conn)

    # Remap old shopping groups → new groups
    _migrate_shopping_groups(conn)

    # Regulars default to inactive (unchecked) — user checks what they need each week
    _migrate_regulars_default_inactive(conn)

    # Migrate existing grocery state (file-based) into grocery_trips
    _migrate_grocery_to_trips(conn)

    conn.commit()


def _migrate_to_regulars(conn: sqlite3.Connection) -> None:
    """One-time migration: merge essentials + pantry staples into regulars."""
    # Check if regulars already has data (already migrated)
    count = conn.execute("SELECT COUNT(*) AS n FROM regulars").fetchone()
    if count["n"] > 0:
        return

    # Migrate essentials
    try:
        essentials = conn.execute("SELECT * FROM essentials").fetchall()
        for e in essentials:
            # Try to link to an ingredient
            ing = conn.execute(
                "SELECT id FROM ingredients WHERE LOWER(name) = LOWER(?)", (e["name"],)
            ).fetchone()
            conn.execute(
                """INSERT OR IGNORE INTO regulars (name, ingredient_id, shopping_group, store_pref, active)
                   VALUES (?, ?, ?, ?, ?)""",
                (e["name"], ing["id"] if ing else None, e["shopping_group"], e["store_pref"], e["active"]),
            )
    except sqlite3.OperationalError:
        pass  # essentials table doesn't exist

    # Migrate pantry staples (ingredients with is_pantry_staple=1)
    try:
        staples = conn.execute(
            "SELECT id, name, aisle, store_pref FROM ingredients WHERE is_pantry_staple = 1"
        ).fetchall()
        for s in staples:
            conn.execute(
                """INSERT OR IGNORE INTO regulars (name, ingredient_id, shopping_group, store_pref, active)
                   VALUES (?, ?, ?, ?, 1)""",
                (s["name"], s["id"], s["aisle"] or "Other", s["store_pref"]),
            )
    except sqlite3.OperationalError:
        pass


def _migrate_slots_to_meals(conn: sqlite3.Connection) -> None:
    """One-time migration: convert meal_plan_slots to flat meals table."""
    from datetime import date, timedelta

    # Check if old tables exist and have data
    try:
        old_count = conn.execute("SELECT COUNT(*) AS n FROM meal_plan_slots").fetchone()
        if old_count["n"] == 0:
            return
    except sqlite3.OperationalError:
        return  # old tables don't exist (fresh install)

    # Check if we already migrated
    new_count = conn.execute("SELECT COUNT(*) AS n FROM meals").fetchone()
    if new_count["n"] > 0:
        return  # already migrated

    plans = conn.execute("SELECT id, week_of FROM meal_plans").fetchall()
    for plan in plans:
        try:
            week_of = date.fromisoformat(plan["week_of"])
        except (ValueError, TypeError):
            continue

        slots = conn.execute(
            """SELECT s.*, COALESCE(r.name, '') AS rname
               FROM meal_plan_slots s
               LEFT JOIN recipes r ON r.id = s.recipe_id
               WHERE s.plan_id = ?""",
            (plan["id"],),
        ).fetchall()

        for slot in slots:
            slot_date = (week_of + timedelta(days=slot["day_of_week"])).isoformat()
            recipe_name = slot["rname"] or ""
            # Saturday (day 5) with no recipe = Eating Out
            if not recipe_name and slot["day_of_week"] == 5:
                recipe_name = "Eating Out"

            conn.execute(
                """INSERT OR IGNORE INTO meals
                   (slot_date, recipe_id, recipe_name, status, side, locked, is_followup)
                   VALUES (?, ?, ?, ?, ?, ?, 0)""",
                (
                    slot_date,
                    slot["recipe_id"],
                    recipe_name,
                    slot["status"],
                    slot["side"],
                    slot["locked"],
                ),
            )

    # Migrate grocery_lists: fill start_date/end_date from plan's week_of
    gl_rows = conn.execute(
        """SELECT gl.id, mp.week_of FROM grocery_lists gl
           JOIN meal_plans mp ON mp.id = gl.plan_id
           WHERE gl.start_date = ''"""
    ).fetchall()
    for gl in gl_rows:
        try:
            week_of = date.fromisoformat(gl["week_of"])
            end = (week_of + timedelta(days=6)).isoformat()
            conn.execute(
                "UPDATE grocery_lists SET start_date = ?, end_date = ? WHERE id = ?",
                (gl["week_of"], end, gl["id"]),
            )
        except (ValueError, TypeError):
            pass


_GROUP_REMAP = {
    "Fruit & Veggie": "Produce",
    "Dairy": "Dairy & Eggs",
    "Bread and Pasta": "Pasta & Grains",  # default; bread items get Bread & Bakery below
    "Condiments": "Condiments & Sauces",
    "Cans and Soups": "Canned Goods",
    "Snacks and Other": "Snacks",
}

# Items that should go to Bread & Bakery instead of Pasta & Grains
_BREAD_ITEMS = {"bread", "bun", "buns", "hamburger buns", "hot dog buns", "tortilla",
                "tortillas", "flour tortillas", "corn tortillas", "pita", "bagel",
                "rolls", "cornbread", "cornbread mix"}

# Items that should go to Spices & Baking
_SPICE_ITEMS = {"cumin", "chili powder", "paprika", "oregano", "cinnamon", "black pepper",
                "garlic powder", "onion powder", "cayenne", "nutmeg", "thyme", "basil",
                "seasoning", "sugar", "flour", "baking powder", "baking soda", "vanilla",
                "vanilla extract", "cocoa", "cocoa powder", "brown sugar", "powdered sugar",
                "all-purpose flour", "cornstarch"}


def _migrate_shopping_groups(conn: sqlite3.Connection) -> None:
    """Remap old shopping group names to new ones in ingredients and regulars tables."""
    # Check if already migrated (look for any new group name)
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM ingredients WHERE aisle = 'Produce'"
    ).fetchone()
    if row["n"] > 0:
        return  # already migrated

    # Check if old groups exist
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM ingredients WHERE aisle = 'Fruit & Veggie'"
    ).fetchone()
    if row["n"] == 0:
        return  # fresh install with new groups, nothing to migrate

    # Remap ingredients
    for old_group, new_group in _GROUP_REMAP.items():
        conn.execute(
            "UPDATE ingredients SET aisle = ? WHERE aisle = ?",
            (new_group, old_group),
        )

    # Fix bread items: move from Pasta & Grains → Bread & Bakery
    for item in _BREAD_ITEMS:
        conn.execute(
            "UPDATE ingredients SET aisle = 'Bread & Bakery' WHERE LOWER(name) = ? AND aisle = 'Pasta & Grains'",
            (item,),
        )

    # Fix spice items: move from Condiments & Sauces → Spices & Baking
    for item in _SPICE_ITEMS:
        conn.execute(
            "UPDATE ingredients SET aisle = 'Spices & Baking' WHERE LOWER(name) = ? AND aisle IN ('Condiments & Sauces', 'Pasta & Grains', 'Other')",
            (item,),
        )

    # Remap regulars table too
    for old_group, new_group in _GROUP_REMAP.items():
        conn.execute(
            "UPDATE regulars SET shopping_group = ? WHERE shopping_group = ?",
            (new_group, old_group),
        )
    for item in _BREAD_ITEMS:
        conn.execute(
            "UPDATE regulars SET shopping_group = 'Bread & Bakery' WHERE LOWER(name) = ? AND shopping_group = 'Pasta & Grains'",
            (item,),
        )
    for item in _SPICE_ITEMS:
        conn.execute(
            "UPDATE regulars SET shopping_group = 'Spices & Baking' WHERE LOWER(name) = ? AND shopping_group IN ('Condiments & Sauces', 'Pasta & Grains', 'Other')",
            (item,),
        )

    # Remap essentials table too (if it exists)
    try:
        for old_group, new_group in _GROUP_REMAP.items():
            conn.execute(
                "UPDATE essentials SET shopping_group = ? WHERE shopping_group = ?",
                (new_group, old_group),
            )
    except sqlite3.OperationalError:
        pass


def _migrate_regulars_default_inactive(conn: sqlite3.Connection) -> None:
    """One-time: flip all regulars to inactive (unchecked by default).

    Users check what they need each week rather than unchecking what they don't.
    """
    # Use a marker: if any regular is already inactive, migration has run
    row = conn.execute("SELECT COUNT(*) AS n FROM regulars WHERE active = 0").fetchone()
    if row["n"] > 0:
        return  # already migrated or has inactive items
    # Only flip if there are regulars to flip
    row = conn.execute("SELECT COUNT(*) AS n FROM regulars WHERE active = 1").fetchone()
    if row["n"] > 0:
        conn.execute("UPDATE regulars SET active = 0")


def _migrate_grocery_to_trips(conn: sqlite3.Connection) -> None:
    """One-time migration: import file-based grocery state into grocery_trips."""
    # Idempotent: skip if any trips already exist
    row = conn.execute("SELECT COUNT(*) AS n FROM grocery_trips").fetchone()
    if row["n"] > 0:
        return

    import json
    from pathlib import Path

    config_dir = Path.home() / ".souschef"
    saved_list = config_dir / "current_list.json"
    reconcile_file = config_dir / "reconcile_result.json"

    if not saved_list.exists():
        return  # nothing to migrate

    try:
        with open(saved_list) as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return

    # Parse date range from saved selections
    date_key = data.get("date_key", "")
    start_date = ""
    end_date = ""
    if "/" in date_key:
        parts = date_key.split("/")
        if len(parts) == 2:
            start_date, end_date = parts

    # Load checked items from reconcile file
    checked_names: set[str] = set()
    if reconcile_file.exists():
        try:
            with open(reconcile_file) as f:
                checked_names = {n.lower() for n in json.load(f).get("matched", [])}
        except (json.JSONDecodeError, IOError):
            pass

    # Create the trip
    cursor = conn.execute(
        """INSERT INTO grocery_trips (trip_type, start_date, end_date, active)
           VALUES ('plan', ?, ?, 1)""",
        (start_date, end_date),
    )
    trip_id = cursor.lastrowid

    # We can't fully rebuild meal items here (no conn-based grocery build in migration),
    # so migrate extras only. The API will auto-populate meal items on first load.
    extras = data.get("extras", [])
    for name in extras:
        is_checked = name.lower() in checked_names
        conn.execute(
            """INSERT OR IGNORE INTO trip_items (trip_id, name, shopping_group, source, checked)
               VALUES (?, ?, 'Other', 'extra', ?)""",
            (trip_id, name, int(is_checked)),
        )


def seed_from_yaml(conn: sqlite3.Connection, data_dir: str | None = None) -> None:
    if data_dir is None:
        data_dir = str(Path(__file__).resolve().parents[2] / "data")

    ingredients_file = Path(data_dir) / "seed_ingredients.yaml"
    recipes_file = Path(data_dir) / "seed_recipes.yaml"

    if ingredients_file.exists():
        _seed_ingredients(conn, ingredients_file)
    if recipes_file.exists():
        _seed_recipes(conn, recipes_file)

    conn.commit()


def _seed_ingredients(conn: sqlite3.Connection, path: Path) -> None:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    for ing in data.get("ingredients", []):
        conn.execute(
            """INSERT OR IGNORE INTO ingredients
               (name, category, aisle, default_unit, store_pref, is_pantry_staple, root)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                ing["name"],
                ing.get("category", "pantry"),
                ing.get("aisle", ""),
                ing.get("default_unit", "count"),
                ing.get("store_pref", "either"),
                int(ing.get("is_pantry_staple", False)),
                ing.get("root", ""),
            ),
        )


def _seed_recipes(conn: sqlite3.Connection, path: Path) -> None:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    for rec in data.get("recipes", []):
        cursor = conn.execute(
            """INSERT OR IGNORE INTO recipes
               (name, cuisine, effort, cleanup, outdoor, kid_friendly, premade,
                prep_minutes, cook_minutes, servings, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rec["name"],
                rec.get("cuisine", "any"),
                rec.get("effort", "medium"),
                rec.get("cleanup", "medium"),
                int(rec.get("outdoor", False)),
                int(rec.get("kid_friendly", True)),
                int(rec.get("premade", False)),
                rec.get("prep_minutes", 0),
                rec.get("cook_minutes", 0),
                rec.get("servings", 4),
                rec.get("notes", ""),
            ),
        )

        if cursor.rowcount == 0:
            continue

        recipe_id = cursor.lastrowid
        for item in rec.get("ingredients", []):
            ing_row = conn.execute(
                "SELECT id FROM ingredients WHERE name = ?", (item["name"],)
            ).fetchone()
            if ing_row is None:
                continue
            conn.execute(
                """INSERT INTO recipe_ingredients
                   (recipe_id, ingredient_id, quantity, unit, prep_note, component)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    recipe_id,
                    ing_row["id"],
                    item.get("quantity", 1),
                    item.get("unit", "count"),
                    item.get("prep_note", ""),
                    item.get("component", ""),
                ),
            )


def ensure_db(db_path: str | None = None) -> sqlite3.Connection:
    conn = get_connection(db_path)
    init_db(conn)
    row = conn.execute("SELECT COUNT(*) AS n FROM recipes").fetchone()
    if row["n"] == 0:
        seed_from_yaml(conn)
    return conn
