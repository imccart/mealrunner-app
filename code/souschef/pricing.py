"""Price polling and community aggregation for price tracking."""

from __future__ import annotations

import time
from sqlalchemy import text
from souschef.database import DictConnection


def _poll_single_product(upc: str, location_id: str) -> dict | None:
    """Fetch current price for a single UPC at a location via Kroger API."""
    import requests
    from souschef.kroger import BASE_URL, _headers

    try:
        resp = requests.get(
            f"{BASE_URL}/products",
            params={"filter.term": upc, "filter.locationId": location_id, "filter.limit": 1},
            headers=_headers(),
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        items = resp.json().get("data", [])
        if not items:
            return None
        item = items[0]
        sub = item.get("items", [{}])[0] if item.get("items") else {}
        price_info = sub.get("price", {})
        fulfillment = sub.get("fulfillment", {})
        regular = price_info.get("regular")
        if regular is None:
            return None
        return {
            "price": regular,
            "promo_price": price_info.get("promo"),
            "in_stock": 1 if fulfillment.get("curbside") or fulfillment.get("inStore") else 0,
        }
    except Exception:
        return None


def poll_user_prices(conn: DictConnection, user_id: str) -> dict:
    """Poll Kroger prices for a user's recently ordered products.

    Returns dict with counts: {'polled': int, 'errors': int}.
    """
    from souschef.stores import get_kroger_location_id

    location_id = get_kroger_location_id(conn, user_id)
    if not location_id:
        return {"polled": 0, "errors": 0}

    # Get unique UPCs from recent orders (last 30 days)
    rows = conn.execute(
        text("""SELECT DISTINCT product_upc FROM trip_items ti
           JOIN grocery_trips gt ON gt.id = ti.trip_id
           WHERE gt.user_id = :uid AND ti.product_upc != ''
           AND ti.selected_at IS NOT NULL
           AND ti.selected_at::timestamptz > NOW() - INTERVAL '30 days'"""),
        {"uid": user_id},
    ).fetchall()

    upcs = [r["product_upc"] for r in rows]
    if not upcs:
        return {"polled": 0, "errors": 0}

    # Cap at 50 products per poll cycle
    upcs = upcs[:50]

    polled = 0
    errors = 0
    for upc in upcs:
        try:
            price_data = _poll_single_product(upc, location_id)
            if price_data:
                conn.execute(
                    text("""INSERT INTO product_prices
                       (upc, location_id, store_chain, price, promo_price, in_stock, source, user_id)
                       VALUES (:upc, :loc, 'kroger', :price, :promo, :stock, 'poll', :uid)"""),
                    {"upc": upc, "loc": location_id,
                     "price": price_data["price"],
                     "promo": price_data.get("promo_price"),
                     "stock": price_data.get("in_stock"),
                     "uid": user_id},
                )
                polled += 1
            # Rate limit: sleep between calls to avoid Kroger 429
            time.sleep(0.5)
        except Exception:
            errors += 1

    conn.commit()
    return {"polled": polled, "errors": errors}


def rollup_community_prices(conn: DictConnection) -> dict:
    """Aggregate raw product_prices into anonymized community_prices (daily rollup).

    Returns dict with counts: {'rolled_up': int, 'pruned': int}.
    """
    # Upsert daily aggregates from raw prices
    result = conn.execute(
        text("""INSERT INTO community_prices (upc, location_id, store_chain, date, avg_price, min_price, max_price, promo_price, sample_count)
           SELECT upc, location_id, store_chain,
                  fetched_at::date::text AS date,
                  AVG(price) AS avg_price,
                  MIN(price) AS min_price,
                  MAX(price) AS max_price,
                  MAX(promo_price) AS promo_price,
                  COUNT(*) AS sample_count
           FROM product_prices
           WHERE price IS NOT NULL
           GROUP BY upc, location_id, store_chain, fetched_at::date::text
           ON CONFLICT (upc, location_id, date) DO UPDATE SET
                  avg_price = excluded.avg_price,
                  min_price = excluded.min_price,
                  max_price = excluded.max_price,
                  promo_price = COALESCE(excluded.promo_price, community_prices.promo_price),
                  sample_count = excluded.sample_count"""),
    )
    rolled_up = result.rowcount if hasattr(result, 'rowcount') else 0

    # Prune raw prices older than 90 days
    prune_result = conn.execute(
        text("DELETE FROM product_prices WHERE fetched_at::timestamptz < NOW() - INTERVAL '90 days'"),
    )
    pruned = prune_result.rowcount if hasattr(prune_result, 'rowcount') else 0

    conn.commit()
    return {"rolled_up": rolled_up, "pruned": pruned}


def run_price_polling(conn: DictConnection) -> None:
    """Run price polling for all opted-in users, then aggregate."""
    # Find users who opted in
    rows = conn.execute(
        text("SELECT user_id FROM settings WHERE key = 'price_polling' AND value = '1'"),
    ).fetchall()

    for row in rows:
        user_id = row["user_id"]
        try:
            result = poll_user_prices(conn, user_id)
            print(f"[pricing] Polled {result['polled']} prices for user {user_id[:8]}..., {result['errors']} errors", flush=True)
        except Exception as e:
            print(f"[pricing] Error polling user {user_id[:8]}...: {e}", flush=True)

    # Rollup community prices
    try:
        result = rollup_community_prices(conn)
        print(f"[pricing] Community rollup: {result['rolled_up']} rows, {result['pruned']} pruned", flush=True)
    except Exception as e:
        print(f"[pricing] Rollup error: {e}", flush=True)
