# MealRunner — CC Spec: Community Data Collection

## Overview

A lightweight user-contributed data system, starting with brand ownership but built to be extensible. When the app displays "We're not sure" for a Parent Co. field, users can tap it to suggest what they know. Submissions land in a DB table for periodic review and incorporation into the curated mapping.

Same "Yes, Chef!" confirmation pattern as the feedback button.

---

## Backend

### New Table

```sql
CREATE TABLE community_data (
    id TEXT PRIMARY KEY,
    user_id TEXT REFERENCES users(id),
    household_id TEXT,
    data_type TEXT NOT NULL,        -- 'brand_ownership' for now, extensible
    subject TEXT NOT NULL,          -- the brand name in question
    suggested_value TEXT NOT NULL,  -- what the user thinks the answer is
    created_at TEXT DEFAULT (datetime('now'))
);
```

The `data_type` field makes this reusable for future data gaps — NOVA disputes, ingredient corrections, product suggestions, etc. Just add a new value.

### New Endpoint

```
POST /api/community-data
```

Request body:
```json
{
    "data_type": "brand_ownership",
    "subject": "Applegate",
    "suggested_value": "Hormel"
}
```

Auto-captured server-side:
- `user_id` from session
- `household_id` from session
- `created_at` server timestamp

Returns 200 on success. No validation needed beyond non-empty fields — we'll parse quality on review.

---

## Frontend

### Trigger

On the Order page product cards, the "Parent Co.: We're not sure" display becomes a tap target. Visual treatment: subtle underline or a small `?` icon to indicate it's interactive. Should feel like a hint, not a button.

### Bottom Sheet

Tapping opens a small bottom sheet:

**Header:** "Who makes this?"
**Subtext:** *Help us fill in the gaps.*
**Input:** Single text field, placeholder: "e.g. General Mills"
**Button:** Submit
**Dismiss:** X or swipe down

On submit:
- POST to `/api/community-data`
- Dismiss sheet
- Show confirmation: **"Yes, Chef!"** — same pattern as feedback button

### States

- "Parent Co.: General Mills" — not tappable, just display
- "Parent Co.: Same as brand" — not tappable, just display
- "Parent Co.: We're not sure" — tappable, opens sheet

---

## Extensibility Notes

The same `community_data` table and endpoint handles future data types by passing a different `data_type` value. Examples:

| data_type | subject | suggested_value |
|-----------|---------|-----------------|
| `brand_ownership` | "Applegate" | "Hormel" |
| `nova_dispute` | "Siete Grain Free Chips" | "3" |
| `ingredient_synonym` | "hamburger meat" | "ground beef" |

No schema changes needed to add new collection points — just wire up a new tap target with the appropriate `data_type`.

---

## Review Process (Manual, for now)

Periodically query:
```sql
SELECT subject, suggested_value, COUNT(*) as votes
FROM community_data
WHERE data_type = 'brand_ownership'
GROUP BY subject, suggested_value
ORDER BY votes DESC;
```

High-confidence entries (multiple users agreeing) get added to `brand_ownership.yaml`. "We're not sure" disappears for that brand on next deploy.
