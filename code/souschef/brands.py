"""Brand ownership lookup — standalone utility for any store integration.

Maps consumer brands to their parent companies. The curated mapping lives in
data/brand_ownership.yaml. Any integration (Kroger, Instacart, etc.) passes
a brand string through get_parent_company() to get ownership info.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_BRANDS_FILE = _DATA_DIR / "brand_ownership.yaml"

# Cached mapping: lowercase brand name -> parent company (or None for self-owned)
_brand_map: dict[str, str | None] | None = None


def _load_brands() -> dict[str, str | None]:
    global _brand_map
    if _brand_map is not None:
        return _brand_map

    _brand_map = {}
    if not _BRANDS_FILE.exists():
        return _brand_map

    with open(_BRANDS_FILE) as f:
        data = yaml.safe_load(f) or {}

    for entry in data.get("brands", []):
        name = entry.get("brand", "").strip().lower()
        parent = entry.get("parent")  # None means self-owned
        if name:
            _brand_map[name] = parent

    return _brand_map


def get_parent_company(brand: str) -> str:
    """Look up the parent company for a brand.

    Returns:
        - "General Mills" etc. — known parent
        - "Same as brand" — brand is the company itself
        - "We're not sure" — not in our mapping
    """
    if not brand:
        return "We're not sure"

    mapping = _load_brands()
    query = brand.strip().lower()

    # Exact match
    if query in mapping:
        parent = mapping[query]
        return parent if parent else "Same as brand"

    # Substring: check if any mapped brand is contained in the query
    # e.g., "Annie's Homegrown Organic" matches "annie's"
    for key, parent in mapping.items():
        if key in query:
            return parent if parent else "Same as brand"

    # Reverse substring: query contained in a mapped brand
    for key, parent in mapping.items():
        if query in key:
            return parent if parent else "Same as brand"

    return "We're not sure"
