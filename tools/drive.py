"""
Google Drive image storage.
Downloads images and uploads to a Drive folder per search.
Returns Drive URLs for display in browser.
"""
import asyncio
import json
import logging
import os
import re
from datetime import datetime
from typing import List, Optional, Dict

import httpx

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

_creds = None


def _get_token() -> str:
    """Get valid Google access token using built-in http.client transport."""
    global _creds

    if _creds is None:
        from google.oauth2.service_account import Credentials
        sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
        sa_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
        if sa_json:
            _creds = Credentials.from_service_account_info(json.loads(sa_json), scopes=SCOPES)
        elif sa_file:
            _creds = Credentials.from_service_account_file(sa_file, scopes=SCOPES)
        else:
            raise ValueError("No Google credentials configured")

    if not _creds.valid or not _creds.token:
        # Use stdlib http.client as transport — no external deps needed
        import google.auth.transport._http_client
        _creds.refresh(google.auth.transport._http_client.Request())

    return _creds.token


def _headers() -> dict:
    return {"Authorization": f"Bearer {_get_token()}"}


async def _create_folder(client: httpx.AsyncClient, name: str, parent_id: Optional[str]) -> str:
    body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        body["parents"] = [parent_id]
    resp = await client.post(
        "https://www.googleapis.com/drive/v3/files",
        headers={**_headers(), "Content-Type": "application/json"},
        json=body,
        params={"fields": "id"},
    )
    resp.raise_for_status()
    return resp.json()["id"]


async def _make_public(client: httpx.AsyncClient, file_id: str):
    try:
        await client.post(
            f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions",
            headers={**_headers(), "Content-Type": "application/json"},
            json={"type": "anyone", "role": "reader"},
        )
    except Exception as e:
        logger.warning(f"[Drive] make_public failed: {e}")


async def _upload_bytes(client: httpx.AsyncClient, data: bytes, filename: str,
                        folder_id: str, mime: str) -> str:
    """Multipart upload, returns file ID."""
    boundary = "DRIVE_UPLOAD_BOUNDARY_XYZ"
    meta = json.dumps({"name": filename, "parents": [folder_id]})
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{meta}\r\n"
        f"--{boundary}\r\nContent-Type: {mime}\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--".encode()

    resp = await client.post(
        "https://www.googleapis.com/upload/drive/v3/files",
        headers={
            **_headers(),
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
        content=body,
        params={"uploadType": "multipart", "fields": "id"},
    )
    resp.raise_for_status()
    return resp.json()["id"]


def _mime(url: str) -> str:
    u = url.lower().split("?")[0]
    if ".png" in u:  return "image/png"
    if ".webp" in u: return "image/webp"
    if ".gif" in u:  return "image/gif"
    return "image/jpeg"


def _fname(url: str, idx: int) -> str:
    name = url.split("?")[0].split("/")[-1]
    name = re.sub(r"[^\w\-.]", "_", name)
    if len(name) < 4:
        ext = {"image/png": "png", "image/webp": "webp"}.get(_mime(url), "jpg")
        name = f"photo_{idx:02d}.{ext}"
    return name[:80]


async def _fetch_and_upload(client: httpx.AsyncClient, img: dict, idx: int,
                             folder_id: str) -> Optional[dict]:
    url = img.get("url", "")
    if not url:
        return None
    try:
        r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200 or len(r.content) < 1500:
            return None
        file_id = await _upload_bytes(client, r.content, _fname(url, idx), folder_id, _mime(url))
        drive_url = f"https://drive.google.com/uc?export=view&id={file_id}"
        logger.info(f"[Drive] {_fname(url, idx)} → {file_id}")
        return {"url": drive_url, "alt": img.get("alt", ""), "original_url": url}
    except Exception as e:
        logger.warning(f"[Drive] skip {url[:60]}: {e}")
        return None


async def save_images_to_drive(product_name: str, images: List[dict]) -> Optional[Dict]:
    """
    Download images concurrently and upload to a new Google Drive folder.

    Returns:
        {"folder_url": str, "images": [{"url": drive_url, "alt": str}]}
        or None on complete failure.
    """
    if not images:
        return None

    parent_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
    folder_name = f"{product_name} {datetime.now().strftime('%Y-%m-%d %H:%M')}"

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            folder_id = await _create_folder(client, folder_name, parent_id)
            await _make_public(client, folder_id)

            # Download + upload concurrently
            tasks = [_fetch_and_upload(client, img, i + 1, folder_id)
                     for i, img in enumerate(images)]
            results = await asyncio.gather(*tasks)

            saved = [r for r in results if r is not None]

            if not saved:
                # Clean up empty folder
                try:
                    await client.delete(
                        f"https://www.googleapis.com/drive/v3/files/{folder_id}",
                        headers=_headers(),
                    )
                except Exception:
                    pass
                return None

            folder_url = f"https://drive.google.com/drive/folders/{folder_id}"
            logger.info(f"[Drive] {len(saved)} images saved → {folder_url}")
            return {
                "folder_url": folder_url,
                "images": [{"url": r["url"], "alt": r["alt"]} for r in saved],
            }

    except Exception as e:
        logger.error(f"[Drive] failed: {e}", exc_info=True)
        return None
