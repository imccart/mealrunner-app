"""Google Sheets integration for grocery list export."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from souschef.grocery import build_grocery_list, split_by_store
from souschef.models import Meal, MealPlan, MealWeek
from souschef.planner import DAY_NAMES

_CONFIG_DIR = Path.home() / ".souschef"
_CREDS_FILE = _CONFIG_DIR / "google_credentials.json"
_TOKEN_FILE = _CONFIG_DIR / "google_token.json"

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Shopping group display order
_GROUP_ORDER = [
    "Produce",
    "Meat",
    "Dairy & Eggs",
    "Bread & Bakery",
    "Pasta & Grains",
    "Spices & Baking",
    "Condiments & Sauces",
    "Canned Goods",
    "Frozen",
    "Breakfast & Beverages",
    "Snacks",
    "Other",
]


def _get_credentials():
    """Get or refresh Google OAuth2 credentials."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None

    if _TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not _CREDS_FILE.exists():
                raise FileNotFoundError(
                    f"Google credentials not found at {_CREDS_FILE}\n"
                    "Download OAuth client credentials from Google Cloud Console\n"
                    "and save as: ~/.souschef/google_credentials.json"
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(_CREDS_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0)

        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _TOKEN_FILE.write_text(creds.to_json())

    return creds


def _get_sheets_service():
    """Build the Google Sheets API service."""
    from googleapiclient.discovery import build

    creds = _get_credentials()
    return build("sheets", "v4", credentials=creds)


def export_grocery_list(
    conn: sqlite3.Connection,
    plan: MealPlan,
    spreadsheet_id: str | None = None,
    regulars_list: list | None = None,
    extra_items: list[str] | None = None,
    strikethrough_names: set[str] | None = None,
    grocery_list=None,
) -> str:
    """Export meal plan and grocery list to Google Sheets. Returns the spreadsheet URL.

    strikethrough_names: item names (lowered) to show with strikethrough — purchased items.
    grocery_list: pre-filtered GroceryList; built from plan if not provided.
    """
    service = _get_sheets_service()

    gl = grocery_list if grocery_list is not None else build_grocery_list(conn, plan)
    by_store = split_by_store(gl)

    if regulars_list is None:
        regulars_list = []
    if extra_items is None:
        extra_items = []
    if strikethrough_names is None:
        strikethrough_names = set()

    if spreadsheet_id:
        _update_spreadsheet(service, spreadsheet_id, plan, gl, by_store, regulars_list, extra_items, conn, strikethrough_names)
    else:
        spreadsheet_id = _create_spreadsheet(service, plan, gl, by_store, regulars_list, extra_items, conn, strikethrough_names)

    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"


def _create_spreadsheet(service, plan, gl, by_store, regulars_list, extra_items, conn=None, strikethrough_names=None) -> str:
    """Create a new spreadsheet with two tabs."""
    week_label = plan.start_date if isinstance(plan, MealWeek) else plan.week_of
    title = f"Meal Plan — Week of {week_label}"

    spreadsheet = (
        service.spreadsheets()
        .create(
            body={
                "properties": {"title": title},
                "sheets": [
                    {"properties": {"title": "Meal Plan"}},
                    {"properties": {"title": "Grocery List"}},
                ],
            }
        )
        .execute()
    )

    spreadsheet_id = spreadsheet["spreadsheetId"]
    _populate_meal_plan(service, spreadsheet_id, plan, gl)
    _populate_grocery_list(service, spreadsheet_id, gl, by_store, regulars_list, extra_items, conn, strikethrough_names)
    _format_sheets(service, spreadsheet_id)

    return spreadsheet_id


def _update_spreadsheet(service, spreadsheet_id, plan, gl, by_store, regulars_list, extra_items, conn=None, strikethrough_names=None):
    """Clear and update an existing spreadsheet."""
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [
                {
                    "updateSpreadsheetProperties": {
                        "properties": {
                            "title": f"Meal Plan — Week of {plan.start_date if isinstance(plan, MealWeek) else plan.week_of}"
                        },
                        "fields": "title",
                    }
                }
            ]
        },
    ).execute()

    for sheet_name in ("Meal Plan", "Grocery List"):
        service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id, range=f"'{sheet_name}'"
        ).execute()

    _populate_meal_plan(service, spreadsheet_id, plan, gl)
    _populate_grocery_list(service, spreadsheet_id, gl, by_store, regulars_list, extra_items, conn, strikethrough_names)


def _populate_meal_plan(service, spreadsheet_id, plan, gl):
    """Tab 1: Day + Meal, plus staples reminder."""
    rows = [["Day", "Meal", "Side"]]

    if isinstance(plan, MealWeek):
        for meal in plan.meals:
            meal_text = meal.recipe_name
            if meal.is_followup:
                meal_text += " (uses leftovers)"
            rows.append([meal.day_name, meal_text, meal.side])
    else:
        for slot in plan.slots:
            meal = slot.recipe_name
            if slot.is_followup:
                meal += " (uses leftovers)"
            rows.append([DAY_NAMES[slot.day_of_week], meal, slot.side])

    _write_rows(service, spreadsheet_id, "Meal Plan", rows)


def _populate_grocery_list(service, spreadsheet_id, gl, by_store, regulars_list, extra_items, conn=None, strikethrough_names=None):
    """Tab 2: One flat list grouped by shopping section. Purchased items get strikethrough."""
    struck = strikethrough_names or set()

    # Collect all items as (name, for_text, shopping_group)
    seen: set[str] = set()
    all_entries: list[tuple[str, str, str]] = []

    # Regulars first (highest priority)
    for item in regulars_list:
        seen.add(item.name.lower())
        all_entries.append((item.name, "", item.shopping_group))

    # Meal ingredients
    for items in by_store.values():
        for item in items:
            if item.ingredient_name.lower() not in seen:
                seen.add(item.ingredient_name.lower())
                meal_text = ", ".join(item.meals) if item.meals else ""
                group = item.aisle or "Other"
                all_entries.append((item.ingredient_name, meal_text, group))

    # Extra free-form items
    for name in extra_items:
        if name.lower() not in seen:
            seen.add(name.lower())
            all_entries.append((name, "other", "Other"))

    # Group by shopping section
    groups: dict[str, list[tuple[str, str]]] = {}
    for name, for_text, group in all_entries:
        groups.setdefault(group, []).append((name, for_text))

    # Build rows and track which are struck through
    rows = []
    struck_rows: set[int] = set()
    ordered = [g for g in _GROUP_ORDER if g in groups]
    remaining = sorted(g for g in groups if g not in _GROUP_ORDER)
    for group in ordered + remaining:
        rows.append([group])
        for name, for_text in sorted(groups[group]):
            if name.lower() in struck:
                struck_rows.add(len(rows))
            rows.append([name, for_text])
        rows.append([])

    _write_rows(service, spreadsheet_id, "Grocery List", rows)
    _add_grocery_formatting(service, spreadsheet_id, rows, struck_rows)


def _write_rows(service, spreadsheet_id, sheet_name, rows):
    """Write rows to a sheet."""
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_name}'!A1",
        valueInputOption="USER_ENTERED",
        body={"values": rows},
    ).execute()


def _get_sheet_id(service, spreadsheet_id, sheet_name):
    """Get the numeric sheet ID for a named sheet."""
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for s in meta["sheets"]:
        if s["properties"]["title"] == sheet_name:
            return s["properties"]["sheetId"]
    return None


def _format_sheets(service, spreadsheet_id):
    """Auto-resize columns on both sheets."""
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    requests = []

    for sheet in meta["sheets"]:
        sheet_id = sheet["properties"]["sheetId"]
        requests.append(
            {
                "autoResizeDimensions": {
                    "dimensions": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": 0,
                        "endIndex": 3,
                    }
                }
            }
        )

    if requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": requests}
        ).execute()


def _add_grocery_formatting(service, spreadsheet_id, rows, struck_rows=None):
    """Bold section headers, add checkboxes, and strikethrough purchased items."""
    sheet_id = _get_sheet_id(service, spreadsheet_id, "Grocery List")
    if sheet_id is None:
        return

    struck = struck_rows or set()

    # Identify group header rows
    group_rows = set()
    for i, row in enumerate(rows):
        if len(row) == 1 and row[0] and not row[0].startswith(" "):
            group_rows.add(i)

    requests = []

    for i, row in enumerate(rows):
        if not row or not row[0]:
            continue

        if i in group_rows:
            # Bold group headers with background
            requests.append(
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": i,
                            "endRowIndex": i + 1,
                            "startColumnIndex": 0,
                            "endColumnIndex": 3,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "textFormat": {"bold": True},
                                "backgroundColor": {
                                    "red": 0.85,
                                    "green": 0.92,
                                    "blue": 1.0,
                                },
                            }
                        },
                        "fields": "userEnteredFormat(textFormat,backgroundColor)",
                    }
                }
            )
        else:
            is_struck = i in struck
            # Checkbox — checked if purchased
            requests.append(
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": i,
                            "endRowIndex": i + 1,
                            "startColumnIndex": 2,
                            "endColumnIndex": 3,
                        },
                        "cell": {
                            "dataValidation": {
                                "condition": {"type": "BOOLEAN"},
                                "strict": True,
                            },
                            "userEnteredValue": {"boolValue": is_struck},
                        },
                        "fields": "dataValidation,userEnteredValue",
                    }
                }
            )
            # Strikethrough + gray for purchased items
            if is_struck:
                requests.append(
                    {
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": i,
                                "endRowIndex": i + 1,
                                "startColumnIndex": 0,
                                "endColumnIndex": 2,
                            },
                            "cell": {
                                "userEnteredFormat": {
                                    "textFormat": {
                                        "strikethrough": True,
                                        "foregroundColor": {
                                            "red": 0.6,
                                            "green": 0.6,
                                            "blue": 0.6,
                                        },
                                    },
                                }
                            },
                            "fields": "userEnteredFormat(textFormat)",
                        }
                    }
                )

    if requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": requests}
        ).execute()
