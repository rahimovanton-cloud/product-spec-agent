import os
import httpx
import logging

logger = logging.getLogger(__name__)

PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")

SYSTEM_PROMPT = """You are a technical specifications expert for professional video/photo equipment.
Return ONLY a JSON object with exact physical specifications.
Format:
{
  "official_name": "exact product name",
  "weight_kg": number (body only, without lens or accessories),
  "width_mm": number,
  "height_mm": number,
  "depth_mm": number,
  "confidence": "high/medium/low",
  "notes": "any notes about measurements"
}
Use null for unknown values. Do NOT include units inside values."""

USER_PROMPT = """Find EXACT physical specifications for: {product_name}

Required:
- Weight (body only, kg)
- Width (mm)
- Height (mm)
- Depth (mm)

Return ONLY the JSON object."""


async def search_perplexity(product_name: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "sonar-pro",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": USER_PROMPT.format(product_name=product_name)},
                ],
                "max_tokens": 1024,
                "temperature": 0.1,
                "return_citations": True,
            },
        )
        response.raise_for_status()
        return response.json()
