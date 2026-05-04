# Order flow + Receipt reconciliation

The two halves of the "buy → reconcile" loop. Order page sends a cart to Kroger; Receipt page closes the loop by matching what arrived against what was on the list.

## Store integrations

- **Store-agnostic model.** Each store row has an `api` field. Kroger is auto-detected from store name.
- **Fulfillment mode** (pickup / delivery) is chosen at order time, not store setup.
- **Household store sharing.** `allow_household` column on `user_kroger_tokens`. Toggle in Account sheet. Server-side check on order/submit. Endpoint name is generic: `/api/store/allow-household`.
- **Shared Kroger account indicator.** Order page and Account sheet show whose account is being used when ordering through a household member's shared Kroger account.

## Order page

Simple ← prev / next → navigation through items.

- Editable search box pre-filled with item name; Enter submits.
- Quantity selector and "Anything else?" as modal popups.
- Paginated search.
- Prior selections enriched with price / NOVA / parent company / FDA violations.
- Thumbs-down products suppressed.
- **End-of-list state:** mobile shows stacked Keep shopping / Send to store (Sheet) / Compare (Sheet). Desktop shows Keep shopping + a hint to use the sidebar.
- **Desktop sidebar:** Active / Ordered / Buying elsewhere section headers, comparison toggle, send button.
- **Mobile:** tappable header counts.

## Order submission

- `submitted_at` timestamp on trip items, set **before** Kroger API call (rolled back on failure) to prevent duplicate submits on crash/deploy.
- Submitted items excluded from future order flows.
- Order page refreshes after submit → empty state.
- **Quantity cap:** clamped to `[1, 24]` in the `select_product` endpoint.

## Meal attribution

Order page "Picking for" section and Walk the Aisles mode both show meal names and notes below each item — helps with protein / variant decisions in context.

## Product ratings

Thumbs up/down on reconciled receipt items. Ratings surface on the Order page (prior selections + search results, sorted by rating).

## Product key system

`product_key = UPC` if available, else `brand|description`. Enables rating / preferences for receipt items without UPCs. `receipt_extra_items` table stores unmatched receipt items. Receipt dedup prevents double-counting on re-upload.

## Receipt upload

- **Take photo** (full-res camera via `getUserMedia` / `ImageCapture` API) + **Choose from library** (file picker). `CameraCapture.jsx` component.
- **Kroger PDFs** parsed structurally via PyMuPDF — no Claude API call.
- **Image receipts** parsed via Claude Vision **single-pass**: ONE call extracts items AND matches against the grocery list simultaneously. Visual context (size, brand, package type) improves matching.
  - Prompt includes a synonym list (Lip Balm = chapstick, Tissue = kleenex, Cotton Swab = q-tip, Plastic Wrap = saran wrap, Aluminum Foil = tin foil, Adhesive Bandage = band-aid, Hand Soap, Dish Soap) plus instruction to apply similar logic.
  - Explicit "match across categories — food, personal care, household, cleaning, pets" and an anti-hallucination check against the list post-response.
  - Unmatched receipt lines dropped (raw abbreviations not useful).
- `ANTHROPIC_API_KEY` env var required for image receipts.

## Receipt page UX

- Upload buttons are card-style ('Take a photo' / 'Choose a file') with icons + subtitle hints, stacked on narrow screens.
- Header subtitle shows "N to confirm" / "N extra to review" / "Upload a receipt..." instead of a misleading lifetime "N confirmed" count.
- All reconciliation items default to **expanded** (collapsed set inverted) so users see what needs confirming.
- Matched items show the **grocery name** as primary label, with "from receipt: {actual line text}" always below.

## Reconciliation scoping

- Matches against all unreconciled items regardless of checked state — auto-prune handles stale.
- Submitted items that fail UPC match get a second pass through `diff_grocery_list` (smarter word-subset matching).
- **Confirming a match** sets `checked=1, ordered=0`.
- **Not-fulfilled** items reset to active (cleared `ordered` / `submitted` / product data) so they can be re-ordered.

## Substitution detection

When `diff_order` matches by name (not UPC), the item is marked `substituted` instead of `matched`. Ratings apply to the **received** product (uses `receipt_upc` first).

## Confirmation flow

- ☰ expand on each item.
- **Matched:** Confirm / Not-this / Rate.
- **Unmatched extras:** "This is..." (manual match to grocery item) / Rate / Dismiss. Dismissed extras are flagged (not deleted) for learning.
- **Previous purchases** toggle with collapsible weeks. Purchase history endpoint at `GET /purchases`.

## Brand + violation enrichment

- `brand_ownership` table seeded from `data/brand_ownership.yaml` on startup (ON CONFLICT DO NOTHING). `brands.py` queries the DB, not YAML. Supports exact, substring, and reverse substring matching.
- `unknown_brands` table logs brand names with no parent company match, with frequency count. Admin endpoint at `/api/admin/unknown-brands`.
- `company_violations` table caches FDA openFDA food recall data per parent company. `violations.py` fetches on startup via `refresh_fda_data()`. Order page shows expandable details (total recalls, Class I count, most recent date) under the parent-company line.

## Behind the Label info

Account sheet section explains data sources (Open Food Facts for NOVA / Nutri-Score). Order page product cards have info toggles on badges.

## Nearby / comparison stores

`nearby_stores` table stores user-selected comparison stores. Selectable during onboarding step 4 and in Account → Online Store Integrations. `POST /stores/nearby` saves; `GET /stores/nearby` retrieves. Used by price comparison on Order page.
