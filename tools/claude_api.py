import os
import base64
import httpx
import logging
from typing import Optional, Dict

from tools.parsers import parse_specs_json

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

HAIKU  = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"

_JSON_SCHEMA = """{
  "weight_kg": number or null,
  "width_mm": number or null,
  "height_mm": number or null,
  "depth_mm": number or null
}"""

PDF_SYSTEM = f"""Extract physical specifications from this technical document.
Return ONLY JSON:
{_JSON_SCHEMA}
Weight = body only (no lens, no accessories). No units inside values."""

MANUFACTURER_SYSTEM = f"""Extract physical specifications from this product page.
Return ONLY JSON:
{_JSON_SCHEMA}
Weight = body only. No units inside values."""

VISION_SYSTEM = f"""Look at this physical specifications image and extract dimensions.
Return ONLY JSON:
{_JSON_SCHEMA}
Weight = body only. No units inside values."""


async def _call_claude(
    messages: list,
    system: str,
    model: str = HAIKU,
    max_tokens: int = 300,
) -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": messages,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data.get("content", [{}])[0].get("text", "")


async def extract_specs_from_text(
    text: str,
    product_name: str,
    source: str = "text",
) -> Optional[Dict]:
    """Extract specs from plain text / markdown using Claude."""
    model = HAIKU if source in ("pdf", "official_pdf", "fallback") else SONNET
    system = PDF_SYSTEM if source in ("pdf", "official_pdf") else MANUFACTURER_SYSTEM

    prompt = f"Product: {product_name}\n\nContent:\n{text[:6000]}"
    try:
        result = await _call_claude(
            [{"role": "user", "content": prompt}],
            system=system,
            model=model,
        )
        return parse_specs_json(result)
    except Exception as e:
        logger.error(f"Claude text extraction failed ({source}): {e}")
        return None


async def extract_specs_from_image(
    image_url: str,
    product_name: str,
) -> Optional[Dict]:
    """Extract specs from an image using Claude Vision (base64 preferred, URL fallback)."""

    # Try downloading and sending as base64
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            img_resp = await client.get(image_url)
            img_resp.raise_for_status()

        ct = img_resp.headers.get("content-type", "image/jpeg").lower()
        if "png" in ct:
            media_type = "image/png"
        elif "gif" in ct:
            media_type = "image/gif"
        elif "webp" in ct:
            media_type = "image/webp"
        else:
            media_type = "image/jpeg"

        img_b64 = base64.standard_b64encode(img_resp.content).decode()

        messages = [{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": img_b64},
                },
                {
                    "type": "text",
                    "text": f"Extract physical specifications for {product_name} from this image.",
                },
            ],
        }]
        result = await _call_claude(messages, system=VISION_SYSTEM, model=SONNET, max_tokens=400)
        return parse_specs_json(result)

    except Exception as e:
        logger.warning(f"Vision base64 failed, trying URL: {e}")

    # Fallback: pass URL directly to Claude
    try:
        messages = [{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "url", "url": image_url},
                },
                {
                    "type": "text",
                    "text": f"Extract physical specifications for {product_name} from this image.",
                },
            ],
        }]
        result = await _call_claude(messages, system=VISION_SYSTEM, model=SONNET, max_tokens=400)
        return parse_specs_json(result)

    except Exception as e:
        logger.error(f"Vision URL fallback failed: {e}")
        return None
