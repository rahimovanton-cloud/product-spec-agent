import logging
import re
from typing import List, Dict, Optional

from tools.perplexity import search_perplexity
from tools.jina import fetch_as_markdown
from tools.parsers import extract_manufacturer_url

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = r'https?://[^\s\'"<>]+\.(?:jpg|jpeg|png|webp|gif)(?:\?[^\s\'"<>]*)?'

# Domains that usually have clean product images
GOOD_IMAGE_DOMAINS = [
    'blackmagicdesign.com', 'sony.com', 'canon.com', 'nikon.com',
    'panasonic.com', 'fujifilm.com', 'dji.com', 'arri.com', 'red.com',
    'atomos.com', 'teradek.com', 'rode.com', 'sennheiser.com',
    'bhphotovideo.com', 'adorama.com',
]

SKIP_PATTERNS = [
    'icon', 'logo', 'avatar', 'banner', 'sprite', 'thumbnail',
    'pixel', 'tracking', '1x1', 'badge', 'social', 'favicon',
]


def _score_image(url: str, alt: str = "") -> int:
    """Score image relevance. Higher = better."""
    score = 0
    url_lower = url.lower()
    alt_lower = alt.lower()

    if any(bad in url_lower for bad in SKIP_PATTERNS):
        return -1
    if any(bad in alt_lower for bad in SKIP_PATTERNS):
        return -1

    if any(d in url_lower for d in GOOD_IMAGE_DOMAINS):
        score += 10
    if any(k in url_lower for k in ('product', 'gallery', 'hero', 'front', 'main', 'camera', 'body')):
        score += 5
    if any(k in alt_lower for k in ('product', 'front', 'side', 'hero')):
        score += 3

    # Size hints
    for size in re.findall(r'(\d{3,4})x(\d{3,4})', url_lower):
        w, h = int(size[0]), int(size[1])
        if w >= 400 and h >= 300:
            score += 4
        elif w < 100 or h < 100:
            score -= 5

    if '.jpg' in url_lower or '.jpeg' in url_lower:
        score += 1

    return score


def _extract_images_from_markdown(markdown: str) -> List[Dict]:
    """Extract image URLs with alt text from markdown."""
    results = []

    # Markdown images: ![alt](url)
    for m in re.finditer(r'!\[([^\]]*)\]\((' + IMAGE_EXTENSIONS + r')\)', markdown, re.IGNORECASE):
        alt, url = m.group(1), m.group(2)
        score = _score_image(url, alt)
        if score >= 0:
            results.append({"url": url, "alt": alt or "Фото продукта", "score": score})

    # Plain image URLs in text
    for m in re.finditer(IMAGE_EXTENSIONS, markdown, re.IGNORECASE):
        url = m.group(0)
        if not any(r["url"] == url for r in results):
            score = _score_image(url)
            if score >= 0:
                results.append({"url": url, "alt": "Фото продукта", "score": score})

    # Sort by score
    results.sort(key=lambda x: x["score"], reverse=True)

    # Deduplicate similar URLs (keep highest scored)
    seen_bases = set()
    clean = []
    for r in results:
        base = re.sub(r'\?.*$', '', r["url"])
        if base not in seen_bases:
            seen_bases.add(base)
            clean.append(r)

    return clean


async def run_images_pipeline(product_name: str) -> dict:
    try:
        return await _images_pipeline(product_name)
    except Exception as e:
        logger.error(f"Images pipeline error: {e}", exc_info=True)
        return {"error": str(e)}


IMAGES_USER_PROMPT = """Find official high-resolution product photos for: {product_name}

Search for:
- Official manufacturer product page with photos
- High-resolution product images
- Product gallery on manufacturer website

Provide direct image URLs and the manufacturer product page URL."""

IMAGES_SYSTEM_PROMPT = """You are a product research expert.
Find official product photos and images for the requested product.
Include direct image URLs and product page links in your response."""


async def _images_pipeline(product_name: str) -> dict:
    logger.info(f"[{product_name}] Image search")

    # 1. Perplexity search
    perp = await search_perplexity(
        product_name,
        system_prompt=IMAGES_SYSTEM_PROMPT,
        user_prompt=IMAGES_USER_PROMPT.format(product_name=product_name),
    )
    sources = perp.get("citations", []) or []

    images = []
    official_name = product_name

    logger.info(f"[{product_name}] Sources: {len(sources)}")

    # 2. Fetch manufacturer page first (best source)
    manufacturer_url = extract_manufacturer_url(sources)
    logger.info(f"[{product_name}] Manufacturer URL: {manufacturer_url}")
    if manufacturer_url:
        try:
            page_md = await fetch_as_markdown(manufacturer_url)
            logger.info(f"[{product_name}] Manufacturer page: {len(page_md)} chars")

            name_m = re.search(r'^#+\s+(.+)$', page_md, re.IGNORECASE | re.MULTILINE)
            if name_m:
                official_name = name_m.group(1).strip()

            page_images = _extract_images_from_markdown(page_md)
            logger.info(f"[{product_name}] Manufacturer images: {len(page_images)}")
            images += page_images
        except Exception as e:
            logger.warning(f"Manufacturer page failed: {e}")

    # 3. Try all citation sources
    for src in sources[:6]:
        url = src.get("url", "") if isinstance(src, dict) else str(src)
        if url == manufacturer_url:
            continue
        try:
            page_md = await fetch_as_markdown(url)
            extra = _extract_images_from_markdown(page_md)
            for img in extra:
                if not any(i["url"] == img["url"] for i in images):
                    images.append(img)
            logger.info(f"[{product_name}] From {url[:50]}: {len(extra)} images")
            if len(images) >= 8:
                break
        except Exception:
            pass

    # 4. Fallback: B&H Photo is static and has product images
    if len(images) < 3:
        try:
            from urllib.parse import quote
            bh_url = f"https://www.bhphotovideo.com/c/search?Ntt={quote(product_name)}"
            bh_md = await fetch_as_markdown(bh_url)
            extra = _extract_images_from_markdown(bh_md)
            for img in extra:
                if not any(i["url"] == img["url"] for i in images):
                    images.append(img)
            logger.info(f"[{product_name}] B&H fallback: {len(extra)} images")
        except Exception as e:
            logger.warning(f"B&H fallback failed: {e}")

    # Re-sort all collected images
    images.sort(key=lambda x: x["score"], reverse=True)
    top = images[:8]

    logger.info(f"[{product_name}] Total images found: {len(images)}, top: {len(top)}")

    if not top:
        return {"error": f"Фото для «{product_name}» не найдены"}

    return {
        "official_name": official_name if official_name != product_name else None,
        "images": [{"url": img["url"], "alt": img["alt"]} for img in top],
        "count": len(top),
    }
