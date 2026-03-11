import logging
from typing import Dict, Optional
from urllib.parse import quote

from tools.perplexity import search_perplexity
from tools.claude_api import extract_specs_from_text, extract_specs_from_image
from tools.jina import fetch_as_markdown, fetch_as_text
from tools.sheets import save_specs_to_sheet
from tools.parsers import (
    parse_specs_json,
    extract_manufacturer_url,
    extract_pdf_url,
    parse_manufacturer_page,
    verify_and_compare,
    build_telegram_message,
)

logger = logging.getLogger(__name__)


async def run_pipeline(product_name: str, chat_id: int) -> str:
    try:
        return await _pipeline(product_name)
    except Exception as e:
        logger.error(f"Pipeline error for '{product_name}': {e}", exc_info=True)
        return f"Ошибка при поиске характеристик для: {product_name}"


async def _pipeline(product_name: str) -> str:

    # ── 1. Perplexity ─────────────────────────────────────────────────────────
    logger.info(f"[{product_name}] 1/6 Perplexity search")
    perp_response = await search_perplexity(product_name)

    content  = perp_response.get("choices", [{}])[0].get("message", {}).get("content", "")
    sources  = perp_response.get("citations", []) or []
    perplexity_specs = parse_specs_json(content) or {}
    official_name    = perplexity_specs.pop("official_name", None) or product_name

    # ── 2. Extract URLs ────────────────────────────────────────────────────────
    pdf_url          = extract_pdf_url(sources, product_name)
    manufacturer_url = extract_manufacturer_url(sources)
    logger.info(f"  pdf: {pdf_url}")
    logger.info(f"  mfr: {manufacturer_url}")

    pdf_specs          = {}
    manufacturer_specs = {}
    print_pdf_url      = None
    manual_pdf_url     = None

    # ── 3. PDF via Jina ───────────────────────────────────────────────────────
    if pdf_url:
        logger.info(f"[{product_name}] 2/6 PDF fetch")
        try:
            pdf_text = await fetch_as_text(pdf_url)
            if pdf_text:
                pdf_specs = await extract_specs_from_text(pdf_text, product_name, source="pdf") or {}
                logger.info(f"  pdf specs: {pdf_specs}")
        except Exception as e:
            logger.warning(f"  PDF failed: {e}")

    # ── 4. Manufacturer page ──────────────────────────────────────────────────
    if manufacturer_url:
        logger.info(f"[{product_name}] 3/6 Manufacturer page")
        try:
            page_md   = await fetch_as_markdown(manufacturer_url)
            page_data = parse_manufacturer_page(page_md, product_name)

            # Update official name if found
            if page_data.get("official_name") and official_name == product_name:
                official_name = page_data["official_name"]

            print_pdf_url   = page_data.get("print_pdf_url")
            specs_image_url = page_data.get("specs_image_url")

            # 4a. Claude Vision on specs image
            if specs_image_url:
                logger.info(f"  Specs image found: {specs_image_url}")
                try:
                    manufacturer_specs = await extract_specs_from_image(specs_image_url, product_name) or {}
                    logger.info(f"  Image specs: {manufacturer_specs}")
                except Exception as e:
                    logger.warning(f"  Image extraction failed: {e}")

            # 4b. Fallback: text excerpt
            if not manufacturer_specs and page_data.get("page_text_excerpt"):
                manufacturer_specs = await extract_specs_from_text(
                    page_data["page_text_excerpt"], product_name, source="manufacturer"
                ) or {}

            # 4c. Official PDF (only if no PDF specs yet)
            if print_pdf_url and not pdf_specs:
                logger.info(f"  Official PDF: {print_pdf_url}")
                try:
                    official_pdf_text = await fetch_as_text(print_pdf_url)
                    if official_pdf_text:
                        official_pdf_specs = await extract_specs_from_text(
                            official_pdf_text, product_name, source="official_pdf"
                        ) or {}
                        if official_pdf_specs:
                            pdf_specs     = official_pdf_specs
                            manual_pdf_url = print_pdf_url
                except Exception as e:
                    logger.warning(f"  Official PDF failed: {e}")

        except Exception as e:
            logger.warning(f"  Manufacturer page failed: {e}")

    # ── 5. Verify & Compare ───────────────────────────────────────────────────
    logger.info(f"[{product_name}] 4/6 Verify & compare")
    final_specs = verify_and_compare(pdf_specs, manufacturer_specs, perplexity_specs, product_name)
    logger.info(f"  result: {final_specs}")

    # ── 6. Fallback — B&H search ──────────────────────────────────────────────
    if final_specs.get("needs_fallback"):
        logger.info(f"[{product_name}] 5/6 B&H fallback")
        try:
            bh_url  = f"https://r.jina.ai/https://www.bhphotovideo.com/c/search?Ntt={quote(product_name)}"
            bh_text = await fetch_as_text(bh_url)
            if bh_text:
                fb_specs = await extract_specs_from_text(bh_text, product_name, source="fallback") or {}
                if any(fb_specs.get(f) for f in ["weight_kg", "width_mm", "height_mm", "depth_mm"]):
                    final_specs.update(fb_specs)
                    final_specs["parse_success"]  = True
                    final_specs["needs_fallback"] = False
                    final_specs["notes"] = (final_specs.get("notes", "") + " [B&H]").strip()
        except Exception as e:
            logger.warning(f"  Fallback failed: {e}")

    # ── No result ─────────────────────────────────────────────────────────────
    if not final_specs.get("parse_success"):
        return f"Не удалось найти характеристики для: {product_name}"

    # ── 7. Save to Google Sheets ──────────────────────────────────────────────
    logger.info(f"[{product_name}] 6/6 Saving to Sheets")
    try:
        await save_specs_to_sheet(product_name, official_name, final_specs, manual_pdf_url)
    except Exception as e:
        logger.warning(f"  Sheets failed: {e}")

    # ── Build & return message ────────────────────────────────────────────────
    return build_telegram_message(product_name, official_name, final_specs, manual_pdf_url)


async def run_pipeline_dict(product_name: str) -> dict:
    """Run pipeline and return structured dict for web UI."""
    try:
        result = await _pipeline(product_name)
        if result.startswith("Не удалось"):
            return {"error": result}

        # Re-run to get structured data (or parse from message)
        # Actually we duplicate _pipeline logic here to get the dict directly
        # Simple approach: call _pipeline_data
        return await _pipeline_data(product_name)
    except Exception as e:
        logger.error(f"run_pipeline_dict error: {e}", exc_info=True)
        return {"error": str(e)}


async def _pipeline_data(product_name: str) -> dict:
    """Same as _pipeline but returns dict instead of formatted string."""
    from tools.perplexity import search_perplexity
    from tools.claude_api import extract_specs_from_text, extract_specs_from_image
    from tools.jina import fetch_as_markdown, fetch_as_text
    from tools.sheets import save_specs_to_sheet
    from tools.parsers import (
        parse_specs_json, extract_manufacturer_url, extract_pdf_url,
        parse_manufacturer_page, verify_and_compare,
    )
    from urllib.parse import quote

    perp_response     = await search_perplexity(product_name)
    content           = perp_response.get("choices", [{}])[0].get("message", {}).get("content", "")
    sources           = perp_response.get("citations", []) or []
    perplexity_specs  = parse_specs_json(content) or {}
    official_name     = perplexity_specs.pop("official_name", None) or product_name

    pdf_url           = extract_pdf_url(sources, product_name)
    manufacturer_url  = extract_manufacturer_url(sources)

    pdf_specs = {}
    manufacturer_specs = {}
    manual_pdf_url = None

    if pdf_url:
        try:
            pdf_text = await fetch_as_text(pdf_url)
            if pdf_text:
                pdf_specs = await extract_specs_from_text(pdf_text, product_name, source="pdf") or {}
        except Exception:
            pass

    if manufacturer_url:
        try:
            page_md   = await fetch_as_markdown(manufacturer_url)
            page_data = parse_manufacturer_page(page_md, product_name)
            if page_data.get("official_name") and official_name == product_name:
                official_name = page_data["official_name"]
            if page_data.get("specs_image_url"):
                manufacturer_specs = await extract_specs_from_image(page_data["specs_image_url"], product_name) or {}
            if not manufacturer_specs and page_data.get("page_text_excerpt"):
                manufacturer_specs = await extract_specs_from_text(page_data["page_text_excerpt"], product_name, source="manufacturer") or {}
            if page_data.get("print_pdf_url") and not pdf_specs:
                try:
                    official_pdf_text = await fetch_as_text(page_data["print_pdf_url"])
                    if official_pdf_text:
                        pdf_specs = await extract_specs_from_text(official_pdf_text, product_name, source="official_pdf") or {}
                        if pdf_specs:
                            manual_pdf_url = page_data["print_pdf_url"]
                except Exception:
                    pass
        except Exception:
            pass

    final_specs = verify_and_compare(pdf_specs, manufacturer_specs, perplexity_specs, product_name)

    if final_specs.get("needs_fallback"):
        try:
            bh_url  = f"https://r.jina.ai/https://www.bhphotovideo.com/c/search?Ntt={quote(product_name)}"
            bh_text = await fetch_as_text(bh_url)
            if bh_text:
                fb = await extract_specs_from_text(bh_text, product_name, source="fallback") or {}
                if any(fb.get(f) for f in ["weight_kg", "width_mm", "height_mm", "depth_mm"]):
                    final_specs.update(fb)
                    final_specs["parse_success"] = True
        except Exception:
            pass

    if not final_specs.get("parse_success"):
        return {"error": f"Не удалось найти характеристики для: {product_name}"}

    try:
        await save_specs_to_sheet(product_name, official_name, final_specs, manual_pdf_url)
    except Exception:
        pass

    return {
        "official_name": official_name if official_name != product_name else None,
        "weight_kg":  final_specs.get("weight_kg"),
        "width_mm":   final_specs.get("width_mm"),
        "height_mm":  final_specs.get("height_mm"),
        "depth_mm":   final_specs.get("depth_mm"),
        "confidence": final_specs.get("confidence"),
        "notes":      final_specs.get("notes"),
        "pdf_url":    manual_pdf_url,
    }
