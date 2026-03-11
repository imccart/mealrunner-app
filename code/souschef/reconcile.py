"""Receipt parsing and order reconciliation."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

_CONFIG_DIR = Path.home() / ".souschef"
_ANTHROPIC_CREDS = _CONFIG_DIR / "anthropic_credentials.json"


def _get_client():
    """Get Anthropic client, using souschef config or env var."""
    import anthropic

    if _ANTHROPIC_CREDS.exists():
        with open(_ANTHROPIC_CREDS) as f:
            creds = json.load(f)
        return anthropic.Anthropic(api_key=creds["api_key"])
    return anthropic.Anthropic()  # falls back to ANTHROPIC_API_KEY env var


def parse_receipt_image(image_path: str) -> list[dict]:
    """Parse a receipt image using Claude vision. Returns list of {item, qty, price}."""
    import anthropic

    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    # Determine media type
    suffix = path.suffix.lower()
    media_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    media_type = media_types.get(suffix)
    if not media_type:
        raise ValueError(f"Unsupported image format: {suffix}")

    with open(path, "rb") as f:
        image_data = base64.standard_b64encode(f.read()).decode("utf-8")

    client = anthropic.Anthropic()
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_data,
                    },
                },
                {
                    "type": "text",
                    "text": (
                        "Parse this grocery receipt. Extract every purchased item as JSON.\n"
                        "Return ONLY a JSON array, no other text. Each object should have:\n"
                        '- "item": product name/description as shown on receipt\n'
                        '- "qty": quantity (integer, default 1)\n'
                        '- "price": total price for that line item (float)\n'
                        "Ignore subtotals, tax, totals, savings lines, and store info.\n"
                        "If an item was voided or removed, skip it.\n"
                    ),
                },
            ],
        }],
    )

    return _extract_json(message.content[0].text)


def parse_receipt_text(text: str) -> list[dict]:
    """Parse receipt email text using Claude. Returns list of {item, qty, price}."""
    client = _get_client()
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": (
                "Parse this grocery receipt/order confirmation. Extract every purchased item as JSON.\n"
                "Return ONLY a JSON array, no other text. Each object should have:\n"
                '- "item": product name/description\n'
                '- "qty": quantity (integer, default 1)\n'
                '- "price": total price for that line item (float)\n'
                "Ignore subtotals, tax, totals, savings lines, store info, and headers.\n"
                "If an item was substituted, include the substitution (what was actually received).\n\n"
                f"Receipt:\n{text}"
            ),
        }],
    )

    return _extract_json(message.content[0].text)


def parse_receipt_email(eml_path: str) -> list[dict]:
    """Parse a .eml file. Extracts text/HTML body and parses it."""
    import email
    from email import policy

    path = Path(eml_path)
    if not path.exists():
        raise FileNotFoundError(f"Email file not found: {eml_path}")

    with open(path, "rb") as f:
        msg = email.message_from_binary_file(f, policy=policy.default)

    # Try plain text first, fall back to HTML
    body = msg.get_body(preferencelist=("plain", "html"))
    if body is None:
        raise ValueError("Could not extract email body")

    text = body.get_content()

    # Strip HTML tags if needed
    if "<html" in text.lower():
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)

    return parse_receipt_text(text)


def _extract_json(response_text: str) -> list[dict]:
    """Extract JSON array from Claude's response, handling markdown code blocks."""
    text = response_text.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        text = text.strip()
    return json.loads(text)


def parse_receipt_pdf(pdf_path: str) -> list[dict]:
    """Parse a Kroger PDF receipt. Extracts structured item data directly (no LLM needed).

    Falls back to Claude text parsing if the structured format isn't detected.
    Returns list of {item, qty, price, upc}.
    """
    import fitz  # PyMuPDF

    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    doc = fitz.open(str(path))
    text = "\n".join(page.get_text() for page in doc)
    doc.close()

    # Try structured Kroger format first (has UPC lines)
    items = _parse_kroger_structured(text)
    if items:
        return items

    # Fall back to Claude text parsing
    return parse_receipt_text(text)


def _parse_kroger_structured(text: str) -> list[dict]:
    """Parse Kroger's digital receipt format.

    Expected pattern per item:
      Product Name, size
      $price
      qty x $unit_price each
      [Item Coupon/Sale lines...]
      UPC: 0001234567890
    """
    items = []
    lines = text.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # Look for UPC lines — then walk backwards to find the item
        upc_match = re.match(r"UPC:\s*(\d+)", line)
        if upc_match:
            upc = upc_match.group(1)

            # Walk backwards to find qty line and price line
            qty = 1
            price = None
            item_name = None

            for j in range(i - 1, max(i - 10, -1), -1):
                prev = lines[j].strip()
                if prev.startswith("Item Coupon/Sale:"):
                    continue

                # Qty line: "1 x $1.89 each" or "5 x $1.00 $1.50 each"
                qty_match = re.match(r"(\d+)\s*x\s*\$[\d.]+", prev)
                if qty_match:
                    qty = int(qty_match.group(1))
                    continue

                # Price line: "$5.00" alone
                price_match = re.match(r"^\$([\d.]+)$", prev)
                if price_match and price is None:
                    price = float(price_match.group(1))
                    continue

                # If we haven't found the item name yet and this isn't a known pattern,
                # it's the product name (first non-pattern line above the price)
                if item_name is None and prev and not prev.startswith("$"):
                    item_name = prev
                    break

            if item_name:
                items.append({
                    "item": item_name,
                    "qty": qty,
                    "price": price,
                    "upc": upc,
                })

        i += 1

    return items


def diff_order(submitted: list[dict], receipt_items: list[dict]) -> dict:
    """Compare submitted order against receipt. Returns categorized diff.

    Matches by UPC first (exact), then falls back to fuzzy name matching.

    Returns dict with:
      - matched: items in both
      - removed: items in submitted but not on receipt
      - added: items on receipt but not in submitted
    """
    def _norm(name: str) -> str:
        return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()

    # Index receipt items by UPC (if available) and track usage
    receipt_by_upc: dict[str, list[dict]] = {}
    for r in receipt_items:
        if r.get("upc"):
            receipt_by_upc.setdefault(r["upc"], []).append(r)

    receipt_remaining = list(receipt_items)  # items not yet matched
    matched = []
    removed = []

    # Pass 1: UPC match
    for sub in submitted:
        sub_upc = sub.get("upc", "")
        if sub_upc and sub_upc in receipt_by_upc and receipt_by_upc[sub_upc]:
            r_item = receipt_by_upc[sub_upc].pop(0)
            receipt_remaining.remove(r_item)
            matched.append({"submitted": sub, "receipt": r_item, "match": "upc"})
        else:
            removed.append(sub)

    # Pass 2: fuzzy name match for remaining unmatched submitted items
    still_removed = []
    for sub in removed:
        sub_norm = _norm(sub.get("product", sub.get("item", "")))
        sub_words = set(sub_norm.split())

        best_match = None
        best_score = 0

        for r_item in receipt_remaining:
            r_norm = _norm(r_item["item"])
            r_words = set(r_norm.split())
            overlap = len(sub_words & r_words)
            total = max(len(sub_words), len(r_words), 1)
            score = overlap / total
            if score > best_score:
                best_score = score
                best_match = r_item

        if best_match and best_score >= 0.4:
            receipt_remaining.remove(best_match)
            matched.append({"submitted": sub, "receipt": best_match, "match": "name"})
        else:
            still_removed.append(sub)

    # Anything on receipt not matched is an addition
    added = receipt_remaining

    return {
        "matched": matched,
        "removed": still_removed,
        "added": added,
    }


def diff_grocery_list(grocery_names: list[str], receipt_items: list[dict]) -> dict:
    """Match receipt items against grocery list item names.

    Uses fuzzy name matching since grocery names are simple ("avocado", "ground beef")
    and receipt items are full product descriptions.

    Returns dict with:
      - matched: list of {"grocery_name": str, "receipt": dict}
      - unmatched: receipt items that didn't match anything on the list
    """
    def _norm(name: str) -> str:
        return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()

    def _compact(name: str) -> str:
        """Strip everything but alphanumeric — collapses spaces, hyphens, etc."""
        return re.sub(r"[^a-z0-9]", "", name.lower())

    remaining_names = {_norm(n): n for n in grocery_names}
    matched = []
    unmatched = []

    for r_item in receipt_items:
        r_norm = _norm(r_item["item"])
        r_words = set(r_norm.split())
        r_compact = _compact(r_item["item"])

        best_name = None
        best_score = 0

        for g_norm, g_original in remaining_names.items():
            g_words = set(g_norm.split())
            g_compact = _compact(g_original)

            # Spaceless substring match: "lacroix" in "lacroixlimeflavored..."
            if g_compact and g_compact in r_compact:
                score = max(len(g_compact) / len(r_compact), 0.6)
            # Word subset match: "ground beef" words in "Kroger 93/7 Ground Beef Tray"
            elif g_words and g_words.issubset(r_words):
                score = max(len(g_words) / len(r_words), 0.6)
            else:
                overlap = len(g_words & r_words)
                total = max(len(g_words), 1)
                score = overlap / total

            if score > best_score:
                best_score = score
                best_name = (g_norm, g_original)

        if best_name and best_score >= 0.5:
            remaining_names.pop(best_name[0])
            matched.append({"grocery_name": best_name[1], "receipt": r_item})
        else:
            unmatched.append(r_item)

    return {
        "matched": matched,
        "unmatched": unmatched,
    }
