import os
import json
import asyncio
import logging
from datetime import datetime
from typing import Dict, Optional

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

GOOGLE_SHEETS_ID   = os.getenv("GOOGLE_SHEETS_ID")
GOOGLE_SHEETS_NAME = os.getenv("GOOGLE_SHEETS_NAME", "Specs")

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
        raise ValueError("Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE")

    _gc = gspread.authorize(creds)
    return _gc


def _do_save(product_name: str, official_name: Optional[str], specs: Dict, manual_pdf_url: Optional[str]):
    gc = _get_client()
    sh = gc.open_by_key(GOOGLE_SHEETS_ID)
    ws = sh.worksheet(GOOGLE_SHEETS_NAME)

    row = [
        product_name,
        official_name or product_name,
        specs.get("weight_kg", ""),
        specs.get("width_mm", ""),
        specs.get("height_mm", ""),
        specs.get("depth_mm", ""),
        ", ".join(specs.get("sources_used", [])),
        specs.get("confidence", ""),
        specs.get("notes", ""),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        manual_pdf_url or "",
    ]
    ws.append_row(row)
    logger.info(f"Saved to Sheets: {product_name}")


async def save_specs_to_sheet(
    product_name: str,
    official_name: Optional[str],
    specs: Dict,
    manual_pdf_url: Optional[str] = None,
) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _do_save, product_name, official_name, specs, manual_pdf_url)
