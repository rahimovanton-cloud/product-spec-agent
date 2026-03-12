import logging
import re
from typing import List, Dict, Optional
from urllib.parse import quote

from tools.perplexity import search_perplexity
from tools.jina import fetch_as_markdown, fetch_as_text
from tools.parsers import extract_manufacturer_url, TIER1_DOMAINS

logger = logging.getLogger(__name__)

PDF_BLOCKLIST = ['ggvideo.com', 'lang-ag.com']


def _extract_pdf_links(markdown: str, base_url: str = "") -> List[Dict]:
    """Extract all PDF/manual links from markdown text."""
    results = []

    # Markdown links: [title](url)
    for m in re.finditer(r'\[([^\]]+)\]\((https?://[^)]+\.pdf[^)]*)\)', markdown, re.IGNORECASE):
        title, url = m.group(1).strip(), m.group(2)
        if not any(b in url.lower() for b in PDF_BLOCKLIST):
            results.append({"title": title, "url": url})

    # Plain PDF URLs
    for m in re.finditer(r'https?://\S+\.pdf\S*', markdown, re.IGNORECASE):
        url = m.group(0).rstrip(')')
        if not any(b in url.lower() for b in PDF_BLOCKLIST):
            if not any(r["url"] == url for r in results):
                results.append({"title": "PDF документ", "url": url})

    # Links with manual/guide keywords (not necessarily .pdf)
    for m in re.finditer(
        r'\[([^\]]*(?:manual|guide|инструкция|руководство|мануал|download)[^\]]*)\]\((https?://[^)]+)\)',
        markdown, re.IGNORECASE
    ):
        title, url = m.group(1).strip(), m.group(2)
        if url not in [r["url"] for r in results]:
            results.append({"title": title, "url": url})

    return results[:8]


async def run_manual_pipeline(product_name: str) -> dict:
    try:
        return await _manual_pipeline(product_name)
    except Exception as e:
        logger.error(f"Manual pipeline error: {e}", exc_info=True)
        return {"error": str(e)}


MANUAL_USER_PROMPT = """Find official user manual PDF download links for: {product_name}

Search for:
- Official manufacturer manual/user guide PDF
- Download links from official website
- Support page with documentation

List all found PDF/manual links with their URLs."""

MANUAL_SYSTEM_PROMPT = """You are a product documentation expert.
Find user manuals, guides and PDF documentation for the requested product.
Include direct download links and source URLs in your response."""


async def _manual_pipeline(product_name: str) -> dict:
    logger.info(f"[{product_name}] Manual search")

    # 1. Perplexity search for manuals
    perp = await search_perplexity(
        product_name,
        system_prompt=MANUAL_SYSTEM_PROMPT,
        user_prompt=MANUAL_USER_PROMPT.format(product_name=product_name),
    )
    content = perp.get("choices", [{}])[0].get("message", {}).get("content", "")
    sources = perp.get("citations", []) or []

    manuals = []

    # 2. Extract PDF links from Perplexity response text
    manuals += _extract_pdf_links(content)

    # 3. Extract PDF URLs from sources directly
    for src in sources:
        url = src.get("url", "") if isinstance(src, dict) else str(src)
        if ".pdf" in url.lower() and not any(b in url.lower() for b in PDF_BLOCKLIST):
            if not any(m["url"] == url for m in manuals):
                manuals.append({"title": f"PDF — {url.split('/')[-1]}", "url": url})

    # 4. Fetch manufacturer page and look for manuals
    manufacturer_url = extract_manufacturer_url(sources)
    official_name = product_name

    if manufacturer_url:
        try:
            page_md = await fetch_as_markdown(manufacturer_url)

            # Extract name
            name_m = re.search(r'^#+\s+(.+)$', page_md, re.MULTILINE)
            if name_m:
                official_name = name_m.group(1).strip()

            page_manuals = _extract_pdf_links(page_md, manufacturer_url)
            for m in page_manuals:
                if not any(x["url"] == m["url"] for x in manuals):
                    manuals.append(m)
        except Exception as e:
            logger.warning(f"Manufacturer page failed: {e}")

    # 5. Deduplicate and clean titles
    seen = set()
    clean = []
    for m in manuals:
        if m["url"] not in seen:
            seen.add(m["url"])
            # Clean title
            title = m["title"]
            if len(title) > 60:
                title = title[:57] + "..."
            clean.append({"title": title, "url": m["url"]})

    if not clean:
        return {"error": f"Мануалы для «{product_name}» не найдены"}

    return {
        "official_name": official_name if official_name != product_name else None,
        "manuals": clean[:6],
        "count": len(clean),
    }
