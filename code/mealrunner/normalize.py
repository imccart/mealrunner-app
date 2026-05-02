"""Canonical item name normalization.

Maps user-typed item names to canonical ingredient names from the DB.
Silent best-effort: always returns a usable name, never blocks the user.
"""

from __future__ import annotations

import re

from sqlalchemy import text

from mealrunner.database import DictConnection


# Cached ingredient index — built once per process, cleared on None conn
_ingredient_cache: dict[str, tuple[str, int]] | None = None
_ingredient_names: list[tuple[str, int]] | None = None


def _build_cache(conn: DictConnection) -> None:
    """Load all ingredient names into an in-memory index."""
    global _ingredient_cache, _ingredient_names
    rows = conn.execute(text("SELECT id, name FROM ingredients")).fetchall()
    _ingredient_cache = {}
    _ingredient_names = []
    for r in rows:
        canonical = r["name"].lower()
        _ingredient_cache[canonical] = (r["name"], r["id"])
        _ingredient_names.append((canonical, r["id"]))


def _ensure_cache(conn: DictConnection) -> None:
    if _ingredient_cache is None:
        _build_cache(conn)


def invalidate_cache() -> None:
    """Call when ingredients table changes (e.g., new ingredient added)."""
    global _ingredient_cache, _ingredient_names
    _ingredient_cache = None
    _ingredient_names = None


def _norm(name: str) -> str:
    """Normalize: lowercase, strip non-alphanumeric except spaces."""
    return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()


def _compact(name: str) -> str:
    """Strip everything but alphanumeric."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


_VES_TO_F = {"loaves": "loaf", "leaves": "leaf", "halves": "half",
             "calves": "calf", "shelves": "shelf", "thieves": "thief",
             "knives": "knife", "wives": "wife", "lives": "life"}


def _depluralize(word: str) -> str:
    """Simple English depluralization.

    The "-es" rule must only fire when the singular actually takes "-es"
    (sibilants and -o words: boxes/watches/tomatoes). For "apples" or
    "horses", the plural is bare "-s" and stripping "-es" would mangle it.
    Same trap with "-ves": loaf→loaves needs "ves"→"f", but olive→olives
    is just "+s". We use a small allowlist for the f-pattern.
    """
    if word in _VES_TO_F:
        return _VES_TO_F[word]
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"      # berries → berry
    if word.endswith("es") and len(word) > 3:
        stem = word[:-2]
        if stem.endswith(("sh", "ch", "x", "z", "ss", "o")):
            return stem             # boxes → box, tomatoes → tomato
        return word[:-1]            # apples → apple, horses → horse, olives → olive
    if word.endswith("s") and len(word) > 2:
        return word[:-1]            # beans → bean
    return word


def _pluralize(word: str) -> str:
    """Simple English pluralization."""
    if word.endswith("y") and not word.endswith(("ay", "ey", "oy", "uy")):
        return word[:-1] + "ies"
    if word.endswith(("s", "sh", "ch", "x", "z", "o")):
        return word + "es"
    return word + "s"


def compare_key(name: str) -> str:
    """Canonical comparison key for dedup across grocery_items.

    Used at every place we ask "is this the same item as that?" — manual-add
    existence checks, meal-sync existing_map, frontend onListSet membership.
    Output is NEVER stored; the displayed name on the row stays as typed (or
    as resolved by normalize_item_name against the canonical seed).

    Lowercases, normalizes whitespace, depluralizes the last word. We only
    collapse plurals — qualifier-stripping (e.g., "soy milk" → "milk") is
    intentionally NOT done since those are different products at the store.
    """
    n = " ".join((name or "").lower().split())
    if not n:
        return n
    words = n.split()
    words[-1] = _depluralize(words[-1])
    return " ".join(words)


def resolve_user_canonical(conn: DictConnection, user_id: str, raw_name: str) -> str:
    """For freeform names not in the canonical seed, prefer the form the user
    has used before. So if "mini cucumbers" exists anywhere in their grocery
    history (active, have-it'd, checked, removed, ordered, even old), a fresh
    add of "mini cucumber" canonicalizes to "mini cucumbers".

    Should only be called when normalize_item_name returned no canonical match
    (i.e. ingredient_id is None). For seed-matched names, the seed form is
    already the canonical and there's no need to scan history.

    One indexed SELECT keyed on user_id, then Python compare_key over each
    name. Sub-2ms even for users with thousands of historical rows. Returns
    raw_name if no historical match.
    """
    name = (raw_name or "").strip().lower()
    if not name:
        return name
    key = compare_key(name)
    rows = conn.execute(
        text("SELECT name FROM grocery_items WHERE user_id = :uid"),
        {"uid": user_id},
    ).fetchall()
    for r in rows:
        if compare_key(r["name"]) == key:
            return r["name"]
    return name


def normalize_item_name(conn: DictConnection, raw_name: str) -> tuple[str, int | None]:
    """Normalize a user-typed item name to a canonical ingredient name.

    Returns (canonical_name, ingredient_id).
    If no match found, returns (raw_name.lower(), None).
    Never raises — always returns a usable name.
    """
    _ensure_cache(conn)

    name = raw_name.strip().lower()
    if not name:
        return (name, None)

    # 1. Exact match
    if name in _ingredient_cache:
        return _ingredient_cache[name]

    # 2. Plural/singular variants
    deplu = _depluralize(name)
    if deplu != name and deplu in _ingredient_cache:
        return _ingredient_cache[deplu]
    plu = _pluralize(name)
    if plu in _ingredient_cache:
        return _ingredient_cache[plu]

    # Also try depluralize on last word (e.g., "green bean" → check "green beans")
    words = name.split()
    if len(words) > 1:
        last_plu = " ".join(words[:-1]) + " " + _pluralize(words[-1])
        if last_plu in _ingredient_cache:
            return _ingredient_cache[last_plu]
        last_deplu = " ".join(words[:-1]) + " " + _depluralize(words[-1])
        if last_deplu != name and last_deplu in _ingredient_cache:
            return _ingredient_cache[last_deplu]

    # 3. Compact match (ignoring spaces/hyphens: "mac n cheese" vs "mac and cheese")
    name_compact = _compact(name)
    if len(name_compact) >= 3:
        for canonical, ing_id in _ingredient_names:
            if _compact(canonical) == name_compact:
                return (canonical, ing_id)

    # 4. Fuzzy: stem-aware word overlap (same logic as reconcile.py)
    name_words = set(_norm(name).split())
    if name_words:
        best_score = 0.0
        best_match = None
        for canonical, ing_id in _ingredient_names:
            can_words = set(canonical.split())
            # Word subset: user typed all words in ingredient name
            if can_words and can_words.issubset(name_words):
                score = len(can_words) / len(name_words)
                if score > best_score:
                    best_score = score
                    best_match = (canonical, ing_id)
                continue
            if name_words.issubset(can_words):
                score = len(name_words) / len(can_words)
                if score > best_score:
                    best_score = score
                    best_match = (canonical, ing_id)
                continue
            # Stem overlap
            overlap = 0
            for nw in name_words:
                for cw in can_words:
                    if nw.startswith(cw) or cw.startswith(nw):
                        overlap += 1
                        break
            total = max(len(name_words), len(can_words))
            score = overlap / total if total else 0
            if score > best_score:
                best_score = score
                best_match = (canonical, ing_id)

        if best_score >= 0.7 and best_match:
            return best_match

    # 5. Substring containment (bidirectional, like regulars._match_ingredient)
    #    Only for short names to avoid false positives
    if len(name) >= 4:
        for canonical, ing_id in _ingredient_names:
            if canonical in name or name in canonical:
                # Require reasonable length ratio to avoid "oil" matching "broil"
                ratio = min(len(name), len(canonical)) / max(len(name), len(canonical))
                if ratio >= 0.5:
                    return (canonical, ing_id)

    # No match — fall through gracefully
    return (name, None)
