import os
import httpx
import logging

logger = logging.getLogger(__name__)

JINA_API_KEY = os.getenv("JINA_API_KEY", "")  # optional — free tier works without key


def _jina_url(url: str) -> str:
    if url.startswith("https://r.jina.ai/"):
        return url
    return f"https://r.jina.ai/{url}"


def _headers(accept: str = "text/markdown") -> dict:
    h = {"Accept": accept}
    if JINA_API_KEY:
        h["Authorization"] = f"Bearer {JINA_API_KEY}"
    return h


async def fetch_as_markdown(url: str) -> str:
    """Fetch any URL as Markdown via Jina AI reader."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(_jina_url(url), headers=_headers("text/markdown"))
        if resp.status_code == 200:
            return resp.text
        logger.warning(f"Jina markdown fetch {resp.status_code}: {url}")
        return ""


async def fetch_as_text(url: str) -> str:
    """Fetch any URL as plain text via Jina AI reader."""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(_jina_url(url), headers=_headers("text/plain"))
        if resp.status_code == 200:
            return resp.text
        logger.warning(f"Jina text fetch {resp.status_code}: {url}")
        return ""
