import io
import json
import logging
import os
import re
from datetime import datetime
from typing import List, Optional

import httpx
from google.oauth2.service_account import Credentials
import google.auth.transport.requests

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

_creds = None


def _get_token() -> str:
    """Get a valid Google OAuth2 access token for service account."""
    global _creds
    sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    sa_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")

    if _creds is None:
        if sa_json:
            _creds = Credentials.from_service_account_info(json.loads(sa_json), scopes=SCOPES)
        elif sa_file:
            _creds = Credentials.from_service_account_file(sa_file, scopes=SCOPES)
        else:
            raise ValueError("No Google credentials configured")

    if not _creds.valid:
        _creds.refresh(google.auth.transport.requests.Request())

    return _creds.token


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_get_token()}"}


def _create_folder(client: httpx.Client, name: str, parent_id: Optional[str] = None) -> str:
    """Create a Drive folder, return folder ID."""
    body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        body["parents"] = [parent_id]

    resp = client.post(
        "https://www.googleapis.com/drive/v3/files",
        headers={**_auth_headers(), "Content-Type": "application/json"},
        json=body,
        params={"fields": "id"},
    )
    resp.raise_for_status()
    return resp.json()["id"]


def _make_public(client: httpx.Client, file_id: str):
    """Make a Drive file/folder publicly readable (view only)."""
    client.post(
        f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions",
        headers={**_auth_headers(), "Content-Type": "application/json"},
        json={"type": "anyone", "role": "reader"},
    )


def _upload_image(client: httpx.Client, image_bytes: bytes, filename: str,
                  folder_id: str, mime_type: str) -> str:
    """Upload image bytes to Drive folder using multipart upload."""
    boundary = "boundary_xyz_12345"
    metadata = json.dumps({"name": filename, "parents": [folder_id]})

    body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{metadata}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode() + image_bytes + f"\r\n--{boundary}--".encode()

    resp = client.post(
        "https://www.googleapis.com/upload/drive/v3/files",
        headers={
            **_auth_headers(),
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
        content=body,
        params={"uploadType": "multipart", "fields": "id"},
    )
    resp.raise_for_status()
    return resp.json()["id"]


def _mime_from_url(url: str) -> str:
    u = url.lower().split("?")[0]
    if ".png" in u:
        return "image/png"
    if ".webp" in u:
        return "image/webp"
    if ".gif" in u:
        return "image/gif"
    return "image/jpeg"


def _filename_from_url(url: str, index: int) -> str:
    path = url.split("?")[0]
    name = path.split("/")[-1]
    name = re.sub(r"[^\w\-.]", "_", name)
    if not name or len(name) < 4:
        ext = {"image/png": "png", "image/webp": "webp"}.get(_mime_from_url(url), "jpg")
        name = f"photo_{index:02d}.{ext}"
    return name


def save_images_to_drive(product_name: str, images: List[dict]) -> Optional[str]:
    """
    Download images and upload to a new Google Drive folder.
    Returns shareable folder URL, or None if nothing was uploaded.
    """
    parent_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")  # optional parent folder
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    folder_name = f"{product_name} — {date_str}"

    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            folder_id = _create_folder(client, folder_name, parent_id)
            _make_public(client, folder_id)

            uploaded = 0
            for i, img in enumerate(images):
                url = img.get("url", "")
                if not url:
                    continue
                try:
                    resp = client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                    if resp.status_code == 200 and len(resp.content) > 2000:
                        mime = _mime_from_url(url)
                        fname = _filename_from_url(url, i + 1)
                        _upload_image(client, resp.content, fname, folder_id, mime)
                        uploaded += 1
                        logger.info(f"[Drive] Uploaded {fname}")
                except Exception as e:
                    logger.warning(f"[Drive] Skip {url[:60]}: {e}")

            if uploaded == 0:
                # Clean up empty folder
                try:
                    client.delete(
                        f"https://www.googleapis.com/drive/v3/files/{folder_id}",
                        headers=_auth_headers(),
                    )
                except Exception:
                    pass
                return None

            folder_url = f"https://drive.google.com/drive/folders/{folder_id}"
            logger.info(f"[Drive] Saved {uploaded} images → {folder_url}")
            return folder_url

    except Exception as e:
        logger.error(f"[Drive] save_images_to_drive failed: {e}", exc_info=True)
        return None
