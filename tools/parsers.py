import re
import json
import logging
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


def parse_specs_json(text: str) -> Optional[Dict]:
    """3-level JSON parser: direct → strip markdown → regex per field."""
    if not text:
        return None

    # Level 1: Direct JSON block parse
    try:
        json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except Exception:
        pass

    # Level 2: Strip markdown fences and comments, then parse
    try:
        cleaned = re.sub(r'```(?:json)?\n?|\n?```', '', text)
        cleaned = re.sub(r'//[^\n]*', '', cleaned)
        json_match = re.search(r'\{[^{}]*\}', cleaned, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except Exception:
        pass

    # Level 3: Regex field-by-field extraction
    result = {}
    patterns = {
        'weight_kg': r'"?weight_kg"?\s*:\s*([0-9]+\.?[0-9]*)',
        'width_mm':  r'"?width_mm"?\s*:\s*([0-9]+\.?[0-9]*)',
        'height_mm': r'"?height_mm"?\s*:\s*([0-9]+\.?[0-9]*)',
        'depth_mm':  r'"?depth_mm"?\s*:\s*([0-9]+\.?[0-9]*)',
    }
    for field, pattern in patterns.items():
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                result[field] = float(m.group(1))
            except ValueError:
                pass

    return result if result else None


TIER1_DOMAINS = [
    'sony.com', 'blackmagicdesign.com', 'canon.com', 'nikon.com',
    'panasonic.com', 'fujifilm.com', 'leica-camera.com', 'teradek.com',
    'atomos.com', 'sennheiser.com', 'rode.com', 'dji.com', 'gopro.com',
    'arri.com', 'red.com', 'zcam.com', 'sigma-global.com', 'zeiss.com',
    'ronin.com', 'smallrig.com', 'tiffen.com', 'pelican.com',
]

TIER2_DOMAINS = [
    'bhphotovideo.com', 'adorama.com', 'sweetwater.com',
    'filmtools.com', 'kenro.co.uk',
]

PDF_BLOCKLIST = ['ggvideo.com', 'lang-ag.com']

OFFICIAL_PDF_DOMAINS = [
    'documents.blackmagicdesign.com',
    'manuals.sony.net',
    'dl.sony.com',
]


def extract_manufacturer_url(sources: List) -> Optional[str]:
    """Extract best manufacturer URL from sources. Tier 1 preferred, /techspecs first."""
    tier1_priority = []
    tier1_other = []
    tier2 = []

    for source in sources:
        url = source.get('url', '') if isinstance(source, dict) else str(source)
        url_lower = url.lower()

        if any(d in url_lower for d in TIER1_DOMAINS):
            if any(k in url_lower for k in ('techspecs', 'specifications', '/specs')):
                tier1_priority.append(url)
            else:
                tier1_other.append(url)
        elif any(d in url_lower for d in TIER2_DOMAINS):
            tier2.append(url)

    for lst in (tier1_priority, tier1_other, tier2):
        if lst:
            return lst[0]
    return None


def extract_pdf_url(sources: List, product_name: str = '') -> Optional[str]:
    """Extract best PDF URL from sources with priority."""

    # Priority 1: Official manufacturer PDF domains
    for source in sources:
        url = source.get('url', '') if isinstance(source, dict) else str(source)
        if any(d in url.lower() for d in OFFICIAL_PDF_DOMAINS):
            return url

    # Priority 2: Blackmagic techspecs → derive PDF URL
    for source in sources:
        url = source.get('url', '') if isinstance(source, dict) else str(source)
        if 'blackmagicdesign.com' in url and 'techspecs' in url:
            m = re.search(r'/products/([^/]+)/techspecs/([^/?#]+)', url)
            if m:
                slug, model = m.group(1), m.group(2)
                return f'https://www.blackmagicdesign.com/api/print/to-pdf/products/{slug}/techspecs/{model}'

    # Priority 3: Any .pdf URL not in blocklist
    for source in sources:
        url = source.get('url', '') if isinstance(source, dict) else str(source)
        if '.pdf' in url.lower() and not any(b in url.lower() for b in PDF_BLOCKLIST):
            return url

    return None


def parse_manufacturer_page(markdown_text: str, product_name: str) -> Dict:
    """
    Extract from manufacturer page markdown:
    - specs_image_url (Physical Specifications image)
    - print_pdf_url
    - official_name
    - page_text_excerpt
    """
    result = {
        'official_name': None,
        'specs_image_url': None,
        'print_pdf_url': None,
        'page_text_excerpt': '',
    }

    # Physical Specifications image (common on Blackmagic pages)
    img_pattern = (
        r'Physical Specifications[\s\S]{0,400}'
        r'!\[[^\]]*\]\((https://[^)\s]+\.(?:jpg|png|gif|webp)[^)]*)\)'
    )
    img_match = re.search(img_pattern, markdown_text, re.IGNORECASE)
    if img_match:
        result['specs_image_url'] = img_match.group(1)

    # Print / Download PDF link
    pdf_pattern = r'\[(?:Print PDF|Download PDF|PDF)[^\]]*\]\((https://[^)]+)\)'
    pdf_match = re.search(pdf_pattern, markdown_text, re.IGNORECASE)
    if pdf_match:
        result['print_pdf_url'] = pdf_match.group(1)

    # Text excerpt: find Specifications section
    spec_match = re.search(
        r'(?:Specifications?|Technical Specs?|Physical)[^\n]*\n([\s\S]{0,4000})',
        markdown_text, re.IGNORECASE
    )
    if spec_match:
        result['page_text_excerpt'] = spec_match.group(1)[:4000]
    else:
        result['page_text_excerpt'] = markdown_text[:4000]

    # Official product name from first heading
    name_match = re.search(r'^#+\s+(.+)$', markdown_text, re.MULTILINE)
    if name_match:
        result['official_name'] = name_match.group(1).strip()

    return result


def verify_and_compare(
    pdf_specs: Dict,
    manufacturer_specs: Dict,
    perplexity_specs: Dict,
    product_name: str,
) -> Dict:
    """
    Merge specs: PDF > Manufacturer > Perplexity.
    Calculate confidence from source agreement.
    """
    fields = ['weight_kg', 'width_mm', 'height_mm', 'depth_mm']
    sources_ordered = [pdf_specs, manufacturer_specs, perplexity_specs]
    source_names = ['PDF', 'Manufacturer', 'Perplexity']

    final = {}
    field_sources = {}

    for field in fields:
        for specs, name in zip(sources_ordered, source_names):
            val = specs.get(field)
            if val is not None and float(val) > 0:
                final[field] = float(val)
                field_sources[field] = name
                break

    # Cross-source agreement score
    matches = 0
    checks = 0
    for field in fields:
        values = [
            float(s[field]) for s in sources_ordered
            if s.get(field) and float(s[field]) > 0
        ]
        if len(values) >= 2:
            checks += 1
            mn, mx = min(values), max(values)
            if field == 'weight_kg':
                if mx > 0 and (mx - mn) / mx < 0.25:
                    matches += 1
            else:
                if mx - mn < 25:
                    matches += 1

    sources_with_data = [
        name for specs, name in zip(sources_ordered, source_names)
        if any(specs.get(f) for f in fields)
    ]

    if checks > 0:
        score = matches / checks
    elif len(sources_with_data) >= 2:
        score = 0.5
    elif len(sources_with_data) == 1:
        score = 0.3
    else:
        score = 0.0

    if score >= 0.75:
        confidence = 'high'
    elif score >= 0.5:
        confidence = 'medium'
    else:
        confidence = 'low'

    notes_parts = [f"{f.replace('_', ' ')}: {src}" for f, src in field_sources.items()]
    notes = ', '.join(notes_parts) if notes_parts else 'No sources'

    needs_fallback = not any(final.get(f) for f in fields)
    has_weight = bool(final.get('weight_kg'))
    has_dims = sum(1 for f in ['width_mm', 'height_mm', 'depth_mm'] if final.get(f))
    parse_success = has_weight or has_dims >= 2

    return {
        **final,
        'confidence': confidence,
        'notes': notes,
        'needs_fallback': needs_fallback,
        'parse_success': parse_success,
        'sources_used': sources_with_data,
    }


def build_telegram_message(
    product_name: str,
    official_name: Optional[str],
    specs: Dict,
    manual_pdf_url: Optional[str] = None,
) -> str:
    """Format the final Telegram message."""
    lines = []

    name = official_name or product_name
    lines.append(f"*{name}*")
    if official_name and official_name.lower() != product_name.lower():
        lines.append(f"_(запрос: {product_name})_")
    lines.append('')

    if specs.get('weight_kg'):
        lines.append(f"Вес: {specs['weight_kg']} кг")
    if specs.get('width_mm'):
        lines.append(f"Ширина: {specs['width_mm']} мм")
    if specs.get('height_mm'):
        lines.append(f"Высота: {specs['height_mm']} мм")
    if specs.get('depth_mm'):
        lines.append(f"Глубина: {specs['depth_mm']} мм")

    lines.append(f"\nДостоверность: {specs.get('confidence', '—')}")

    if specs.get('notes'):
        lines.append(f"Источники: {specs['notes']}")

    if manual_pdf_url:
        lines.append(f"\nPDF: {manual_pdf_url}")

    return '\n'.join(lines)
