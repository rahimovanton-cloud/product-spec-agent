import logging
import re
from typing import List, Dict
from urllib.parse import quote, urlparse

from tools.perplexity import search_perplexity
from tools.jina import fetch_as_markdown, fetch_as_text
from tools.parsers import extract_manufacturer_url, extract_pdf_url, TIER1_DOMAINS, OFFICIAL_PDF_DOMAINS

logger = logging.getLogger(__name__)

PDF_BLOCKLIST = ['ggvideo.com', 'lang-ag.com']

MANUAL_USER_PROMPT = """Find official user manual PDF download links for: {product_name}

Search for:
- Official manufacturer manual/user guide PDF
- Download links from official website
- Support page with documentation

List all found PDF/manual links with their URLs."""

MANUAL_SYSTEM_PROMPT = """You are a product documentation expert.
Find user manuals, guides and PDF documentation for the requested product.
Include direct download links and source URLs in your response."""


def _extract_pdf_links(markdown: str) -> List[Dict]:
    """Extract all PDF/manual links from markdown text."""
    results = []

    # Markdown links: [title](url) - url may have citation [1] appended
    for m in re.finditer(r'\[([^\]]+)\]\((https?://[^)]+\.pdf[^)]*)\)', markdown, re.IGNORECASE):
        title, url = m.group(1).strip(), re.sub(r'\[\d+\]$', '', m.group(2)).rstrip()
        if not any(b in url.lower() for b in PDF_BLOCKLIST):
            results.append({"title": title, "url": url})

    # Plain PDF URLs (may have citation refs like [1] at end)
    for m in re.finditer(r'https?://\S+\.pdf\S*', markdown, re.IGNORECASE):
        url = re.sub(r'\[\d+\]$', '', m.group(0)).rstrip(').,')
        if not any(b in url.lower() for b in PDF_BLOCKLIST):
            if not any(r["url"] == url for r in results):
                results.append({"title": "PDF документ", "url": url})

    # Links with manual/guide keywords (not necessarily .pdf)
    for m in re.finditer(
        r'\[([^\]]*(?:manual|guide|инструкция|руководство|мануал|download)[^\]]*)\]\((https?://[^)]+)\)',
        markdown, re.IGNORECASE
    ):
        title = m.group(1).strip()
        url = re.sub(r'\[\d+\]$', '', m.group(2)).rstrip()
        if url not in [r["url"] for r in results]:
            results.append({"title": title, "url": url})

    return results[:8]


def _extract_support_urls(sources: list) -> List[str]:
    """Extract support/download page URLs from sources."""
    urls = []
    support_keywords = ['support', 'download', 'manual', 'docs', 'help', 'documentation']
    for src in sources:
        url = src.get("url", "") if isinstance(src, dict) else str(src)
        url_lower = url.lower()
        if any(k in url_lower for k in support_keywords):
            urls.append(url)
    return urls[:3]


def _get_support_page_url(manufacturer_url: str) -> str:
    """Try to construct a support page URL from manufacturer URL."""
    if not manufacturer_url:
        return None
    parsed = urlparse(manufacturer_url)
    domain = parsed.netloc.lower()

    # Known support URL patterns
    if 'sony' in domain:
        return None  # Sony support requires product model lookup
    if 'blackmagicdesign' in domain:
        return "https://www.blackmagicdesign.com/support"
    if 'dji' in domain:
        return "https://www.dji.com/downloads"
    if 'canon' in domain:
        return None
    return None


async def run_manual_pipeline(product_name: str) -> dict:
    try:
        return await _manual_pipeline(product_name)
    except Exception as e:
        logger.error(f"Manual pipeline error: {e}", exc_info=True)
        return {"error": str(e)}


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

    logger.info(f"[{product_name}] Perplexity content length: {len(content)}, sources: {len(sources)}")

    manuals = []

    # 2. Extract PDF links from Perplexity response text
    manuals += _extract_pdf_links(content)
    logger.info(f"[{product_name}] From content: {len(manuals)} manuals")

    # 3. Extract PDF URLs from sources directly
    # First check official PDF domains (manuals.sony.net, dl.sony.com, etc.)
    official_pdf = extract_pdf_url(sources, product_name)
    if official_pdf and not any(m["url"] == official_pdf for m in manuals):
        manuals.append({"title": f"Официальный мануал PDF", "url": official_pdf})

    for src in sources:
        url = src.get("url", "") if isinstance(src, dict) else str(src)
        if ".pdf" in url.lower() and not any(b in url.lower() for b in PDF_BLOCKLIST):
            if not any(m["url"] == url for m in manuals):
                manuals.append({"title": f"PDF — {url.split('/')[-1]}", "url": url})

    logger.info(f"[{product_name}] After sources: {len(manuals)} manuals")

    # 4. Fetch support/download pages from citations
    support_urls = _extract_support_urls(sources)
    manufacturer_url = extract_manufacturer_url(sources)
    official_name = product_name

    pages_to_fetch = support_urls.copy()
    if manufacturer_url and manufacturer_url not in pages_to_fetch:
        pages_to_fetch.insert(0, manufacturer_url)

    for page_url in pages_to_fetch[:4]:
        try:
            page_md = await fetch_as_markdown(page_url)
            logger.info(f"[{product_name}] Fetched {page_url}: {len(page_md)} chars")

            if page_url == manufacturer_url:
                name_m = re.search(r'^#+\s+(.+)$', page_md, re.MULTILINE)
                if name_m:
                    official_name = name_m.group(1).strip()

            page_manuals = _extract_pdf_links(page_md)
            for m in page_manuals:
                if not any(x["url"] == m["url"] for x in manuals):
                    manuals.append(m)
                    logger.info(f"[{product_name}] Found manual: {m['url']}")
        except Exception as e:
            logger.warning(f"Page fetch failed {page_url}: {e}")

    # 5. Jina search fallback if still nothing
    if not manuals:
        try:
            jina_url = f"https://s.jina.ai/{quote(product_name + ' user manual PDF download')}"
            jina_md = await fetch_as_markdown(jina_url)
            manuals += _extract_pdf_links(jina_md)
            logger.info(f"[{product_name}] Jina fallback found: {len(manuals)}")
        except Exception as e:
            logger.warning(f"Jina fallback failed: {e}")

    # 6. Deduplicate and clean titles
    seen = set()
    clean = []
    for m in manuals:
        if m["url"] not in seen:
            seen.add(m["url"])
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
