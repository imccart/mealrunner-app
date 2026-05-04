# Price tracking

Three layers: passive logging on every user action, optional active polling on a background thread, and anonymized community aggregation.

## Passive logging

Search results, product selections, and receipt items are all logged to `product_prices` whenever the user touches them. This is always on — no setting.

## Active polling (opt-in)

12-hour cycle via background thread.

- Polling updates the **`product_scores` cache**, not just `product_prices`. So repeat searches hit the cache instead of doing individual Kroger lookups for each user.
- User opts in via Account → Price Tracking accordion.

## Community aggregation (opt-in)

Anonymized daily rollup → `community_prices`. Opt-in sharing only. Settings live in Account → Price Tracking.

## Tables

- `product_prices` — raw log of observed prices.
- `community_prices` — daily rollup across opted-in users.
- `product_scores` — cache of polled prices, used by search to skip per-UPC Kroger lookups.

## Insights endpoints

- **`GET /price-tracking/best-day?scope=trip|usuals`** — day-of-week price patterns. Normalizes each UPC to its mean and averages the pct diff across the basket.
- **`GET /price-tracking/basket-trend`** — sums matched `grocery_items.receipt_price` (no qty multiplication — it's the line total) plus unmatched `receipt_extra_items`, grouped by week. Only "real" shopping weeks (≥10 items or ≥$50) feed the headline average.
