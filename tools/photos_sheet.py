"""
Create a dedicated sheet tab for image search results.
Uses only Google Sheets API (no Drive API needed).
"""
import json
import logging
import os
import re
from datetime import datetime
from typing import List, Optional

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_gc = None


def _get_client() -> gspread.Client:
    global _gc
    if _gc is not None:
        return _gc
    sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    sa_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if sa_json:
        creds = Credentials.from_service_account_info(json.loads(sa_json), scopes=SCOPES)
    elif sa_file:
        creds = Credentials.from_service_account_file(sa_file, scopes=SCOPES)
    else:
        raise ValueError("No Google credentials configured")
    _gc = gspread.authorize(creds)
    return _gc


def _safe_sheet_name(product_name: str, date_str: str) -> str:
    """Sheet tab names must be ≤ 100 chars, no special chars."""
    name = f"Photos {product_name} {date_str}"
    # Remove chars forbidden in sheet names
    name = re.sub(r'[\\/*?\[\]:]', '', name)
    return name[:100]


def save_images_to_sheet(product_name: str, images: List[dict]) -> Optional[str]:
    """
    Create a new sheet tab with IMAGE() formulas for each image.
    Returns a link to the sheet tab, or None on failure.
    """
    if not images:
        return None

    sheets_id = os.getenv("GOOGLE_SHEETS_ID", "")
    if not sheets_id:
        return None

    try:
        gc = _get_client()
        sh = gc.open_by_key(sheets_id)

        date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        sheet_name = _safe_sheet_name(product_name, date_str)

        # Create new worksheet tab
        try:
            ws = sh.add_worksheet(title=sheet_name, rows=50, cols=4)
        except gspread.exceptions.APIError as e:
            if "already exists" in str(e).lower():
                ws = sh.worksheet(sheet_name)
            else:
                raise

        # Header row
        ws.update("A1:D1", [["#", "Фото", "URL", "Alt"]])

        # Format header bold
        ws.format("A1:D1", {
            "textFormat": {"bold": True},
            "backgroundColor": {"red": 0.2, "green": 0.2, "blue": 0.2},
            "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
        })

        # Set row heights for images (150px ≈ row height 113 in Sheets units)
        # Set column widths
        requests = [
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": ws.id,
                        "dimension": "COLUMNS",
                        "startIndex": 1,  # column B
                        "endIndex": 2,
                    },
                    "properties": {"pixelSize": 250},
                    "fields": "pixelSize",
                }
            },
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": ws.id,
                        "dimension": "COLUMNS",
                        "startIndex": 2,  # column C
                        "endIndex": 3,
                    },
                    "properties": {"pixelSize": 400},
                    "fields": "pixelSize",
                }
            },
        ]
        # Set row heights for image rows
        for i in range(len(images)):
            requests.append({
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": ws.id,
                        "dimension": "ROWS",
                        "startIndex": i + 1,  # rows 2..N+1
                        "endIndex": i + 2,
                    },
                    "properties": {"pixelSize": 200},
                    "fields": "pixelSize",
                }
            })

        sh.batch_update({"requests": requests})

        # Fill data rows with IMAGE() formulas
        rows = []
        for i, img in enumerate(images):
            url = img.get("url", "")
            alt = img.get("alt", "")
            image_formula = f'=IMAGE("{url}", 1)'  # mode 1 = fit in cell
            rows.append([str(i + 1), image_formula, url, alt])

        if rows:
            ws.update(f"A2:D{len(rows) + 1}", rows,
                      value_input_option=gspread.utils.ValueInputOption.user_entered)

        # Build link to this specific tab
        sheet_url = (
            f"https://docs.google.com/spreadsheets/d/{sheets_id}"
            f"/edit#gid={ws.id}"
        )
        logger.info(f"[Photos] Created sheet '{sheet_name}': {sheet_url}")
        return sheet_url

    except Exception as e:
        logger.error(f"[Photos] save_images_to_sheet failed: {e}", exc_info=True)
        return None
