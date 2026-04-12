"""Brand ownership lookup — queries the brand_ownership DB table.

Maps consumer brands to their parent companies. The curated mapping is seeded
from data/brand_ownership.yaml on startup and stored in the brand_ownership table.
Any integration (Kroger, Instacart, etc.) passes a brand string through
get_parent_company() to get ownership info.
"""

from __future__ import annotations

from sqlalchemy import text


def get_parent_company(brand: str, conn=None, category: str | None = None) -> str:
    """Look up the parent company for a brand.

    Args:
        brand: Brand string from product data (e.g. "Sara Lee").
        conn: DB connection (auto-acquired if None).
        category: Optional product category hint from Kroger (e.g. "Bakery", "Deli").
            Used to disambiguate brands that map to different parents by category.

    Returns:
        - "General Mills" etc. — known parent
        - "Same as brand" — brand is the company itself
        - "We're not sure" — not in our mapping
    """
    if not brand:
        return "We're not sure"

    if conn is None:
        from mealrunner.database import get_connection
        conn = get_connection()

    query = brand.strip()

    def _result(row):
        return row["parent_company"] if row["parent_company"] else query

    # 1. Exact match with specific category (if provided)
    if category:
        row = conn.execute(
            text("""SELECT parent_company FROM brand_ownership
                    WHERE LOWER(brand) = LOWER(:q) AND LOWER(category) = LOWER(:cat)
                    LIMIT 1"""),
            {"q": query, "cat": category.strip()},
        ).fetchone()
        if row:
            return _result(row)

    # 2. Exact match, default category
    row = conn.execute(
        text("""SELECT parent_company FROM brand_ownership
                WHERE LOWER(brand) = LOWER(:q) AND category = ''
                LIMIT 1"""),
        {"q": query},
    ).fetchone()
    if row:
        return _result(row)

    # 2b. Exact brand match, any category (fallback for category-split brands
    #     when no default row exists and the category hint didn't match)
    row = conn.execute(
        text("""SELECT parent_company FROM brand_ownership
                WHERE LOWER(brand) = LOWER(:q)
                ORDER BY id LIMIT 1"""),
        {"q": query},
    ).fetchone()
    if row:
        return _result(row)

    # 3. Substring: mapped brand contained in query string
    # ORDER BY LENGTH(brand) DESC picks longest (most specific) match
    row = conn.execute(
        text("""SELECT parent_company FROM brand_ownership
                WHERE LOWER(:q) LIKE '%%' || LOWER(brand) || '%%'
                AND category = ''
                ORDER BY LENGTH(brand) DESC LIMIT 1"""),
        {"q": query},
    ).fetchone()
    if row:
        return _result(row)

    # 4. Reverse substring: query contained in a mapped brand
    row = conn.execute(
        text("""SELECT parent_company FROM brand_ownership
                WHERE LOWER(brand) LIKE '%%' || LOWER(:q) || '%%'
                AND category = ''
                ORDER BY LENGTH(brand) DESC LIMIT 1"""),
        {"q": query},
    ).fetchone()
    if row:
        return _result(row)

    return "We're not sure"
