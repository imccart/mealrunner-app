"""User store configuration: names, keys, modes, API integrations."""

from __future__ import annotations

import json
from pathlib import Path

_CONFIG_DIR = Path.home() / ".souschef"
_STORES_FILE = _CONFIG_DIR / "stores.json"


def _load_stores() -> list[dict]:
    if not _STORES_FILE.exists():
        return []
    with open(_STORES_FILE) as f:
        return json.load(f)


def _save_stores(stores: list[dict]) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(_STORES_FILE, "w") as f:
        json.dump(stores, f, indent=2)


def list_stores() -> list[dict]:
    """Return configured stores. Each has: name, key, mode, api."""
    return _load_stores()


def add_store(name: str, key: str, mode: str = "in-person", api: str = "none") -> dict:
    """Add a new store. key is the short letter used in prompts."""
    stores = _load_stores()
    # Check for duplicate key
    for s in stores:
        if s["key"] == key.lower():
            raise ValueError(f"Key '{key}' already used by {s['name']}")
    store = {
        "name": name,
        "key": key.lower(),
        "mode": mode,  # pickup, delivery, in-person
        "api": api,    # kroger, none
    }
    stores.append(store)
    _save_stores(stores)
    return store


def remove_store(key: str) -> str | None:
    """Remove a store by key. Returns removed store name or None."""
    stores = _load_stores()
    for i, s in enumerate(stores):
        if s["key"] == key.lower():
            removed = stores.pop(i)
            _save_stores(stores)
            return removed["name"]
    return None


def get_store_by_key(key: str) -> dict | None:
    """Look up a store by its short key."""
    for s in _load_stores():
        if s["key"] == key.lower():
            return s
    return None


def prompt_keys_help(stores: list[dict]) -> str:
    """Build a prompt hint string like '(k = Kroger, s = Sam's Club)'."""
    parts = [f"{s['key']} = {s['name']}" for s in stores]
    return f"({', '.join(parts)})"
