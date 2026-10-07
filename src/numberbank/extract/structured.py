"""استخراج داده ساخت‌یافته: JSON-LD، Microdata و موتور تشخیص نشانی."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin

from ..geo.gazetteer import get_gazetteer
from ..text.htmlparse import HTMLParser
from ..text.normalize import collapse_ws, normalize_fa, search_text

ORG_TYPES = {
    "organization", "localbusiness", "store", "corporation", "wholesaler", "distributor",
    "manufacturer", "pharmacy", "medicalbusiness", "healthandbeautybusiness", "beautysalon",
    "foodestablishment", "onlinestore", "professionalservice", "ngo", "company", "retail",
}

ADDRESS_MARKERS = (
    "آدرس", "نشانی", "خیابان", "بلوار", "کوچه", "پلاک", "میدان", "جاده", "بزرگراه",
    "شهرک", "استان", "شهرستان", "فاز", "طبقه",
)

OWNER_PATTERNS = [
    re.compile(r"(?:مدیر\s*عامل|مدیرعامل|مالک|صاحب)\s*[:：]?\s*([\u0600-\u06FF\s]{3,45})"),
    re.compile(r"(?:مسئول\s*فروش|مدیر\s*فروش)\s*[:：]?\s*([\u0600-\u06FF\s]{3,45})"),
]

PAGE_NAME_SELECTORS = [
    "h1", "header h1", ".site-title", ".brand", "[itemprop=name]", "meta[property='og:site_name']",
]


def extract_json_ld(html: str) -> list[dict[str, Any]]:
    """همه بلوک‌های JSON-LD صفحه را به‌صورت مسطح برمی‌گرداند."""
    out: list[dict[str, Any]] = []
    if not html:
        return out
    for m in re.finditer(
        r'(?is)<script[^>]+type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>', html
    ):
        raw = m.group(1).strip()
        if not raw:
            continue
        raw = re.sub(r",\s*([}\]])", r"\1", raw)  # کاماهای اضافی رایج
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        out.extend(_flatten_jsonld(data))
    return out


def _flatten_jsonld(data: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(data, list):
        for item in data:
            found.extend(_flatten_jsonld(item))
    elif isinstance(data, dict):
        if "@graph" in data:
            found.extend(_flatten_jsonld(data["@graph"]))
        found.append(data)
    return found


def _type_matches(node: dict[str, Any]) -> bool:
    t = node.get("@type") or node.get("type") or ""
    if isinstance(t, list):
        types = [str(x).lower() for x in t]
    else:
        types = [str(t).lower()]
    return any(any(k in tt for k in ORG_TYPES) for tt in types)


def org_from_jsonld(node: dict[str, Any]) -> dict[str, Any] | None:
    """تبدیل گره JSON-LD به فیلدهای کسب‌وکار (فقط داده‌های موجود؛ بدون حدس)."""
    if not _type_matches(node):
        return None
    name = node.get("name") or node.get("legalName")
    if isinstance(name, dict):
        name = name.get("name")
    phones: list[str] = []
    for key in ("telephone", "phone", "contactPoint"):
        val = node.get(key)
        if isinstance(val, str):
            phones.append(val)
        elif isinstance(val, list):
            for v in val:
                if isinstance(v, str):
                    phones.append(v)
                elif isinstance(v, dict):
                    for k2 in ("telephone", "phone"):
                        if isinstance(v.get(k2), str):
                            phones.append(v[k2])
        elif isinstance(val, dict):
            for k2 in ("telephone", "phone"):
                if isinstance(val.get(k2), str):
                    phones.append(val[k2])

    address = node.get("address")
    address_text = None
    if isinstance(address, dict):
        parts = [
            address.get("streetAddress"),
            address.get("addressLocality"),
            address.get("addressRegion"),
            address.get("postalCode"),
        ]
        address_text = collapse_ws(" ، ".join(str(p) for p in parts if p)) or None
    elif isinstance(address, str):
        address_text = collapse_ws(address)

    owner = None
    for key in ("founder", "owner", "employee"):
        val = node.get(key)
        if isinstance(val, dict) and isinstance(val.get("name"), str):
            owner = val["name"]
            break
        if isinstance(val, str):
            owner = val
            break

    same_as = node.get("sameAs") or []
    if isinstance(same_as, str):
        same_as = [same_as]

    return {
        "name": collapse_ws(str(name)) if name else None,
        "phones": [collapse_ws(p) for p in phones if p],
        "address": address_text,
        "website": node.get("url") if isinstance(node.get("url"), str) else None,
        "owner_name": owner,
        "social_links": [s for s in same_as if isinstance(s, str)][:8],
        "category": node.get("@type"),
        "opening_hours": node.get("openingHours"),
        "jsonld": {k: v for k, v in node.items() if k in
                   ("@type", "name", "telephone", "address", "url", "sameAs", "openingHours")},
    }


def extract_owner_name(text: str) -> str | None:
    """استخراج نام مالک/مسئول **فقط** اگر در صفحه به‌صورت عمومی منتشر شده باشد."""
    if not text:
        return None
    for pattern in OWNER_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        candidate = collapse_ws(m.group(1))
        words = candidate.split()
        if 1 <= len(words) <= 4 and not any(ch.isdigit() for ch in candidate):
            return candidate
    return None


def looks_like_address(text: str) -> bool:
    if not text or len(text) < 8:
        return False
    return any(marker in text for marker in ADDRESS_MARKERS)


def extract_address_from_text(text: str, window: int = 240) -> str | None:
    """استخراج نشانی از متن (بازگرداندن «یافت‌نشده» در صورت عدم وجود نشانه)."""
    if not text:
        return None
    norm = collapse_ws(text)
    for marker in ("آدرس", "نشانی", "ادرس"):
        idx = norm.find(marker)
        if idx >= 0:
            segment = norm[idx + len(marker) : idx + len(marker) + window]
            segment = re.split(r"(?:تلفن|همراه|موبایل|فکس|نمابر|وب\s*سایت|ایمیل|ساعات|کد\s*پستی)", segment)[0]
            segment = segment.lstrip(" :：-،.")
            if len(segment) >= 8:
                return collapse_ws(segment)[:300]
    if looks_like_address(norm):
        for marker in ADDRESS_MARKERS:
            idx = norm.find(marker)
            if idx >= 0:
                start = max(0, idx - 40)
                segment = norm[start : start + window]
                if len(segment) >= 10:
                    return collapse_ws(segment)[:300]
    return None


def locate_text(text: str, *, hint_city: str | None = None, hint_province: str | None = None):
    return get_gazetteer().locate(text, hint_city=hint_city, hint_province=hint_province)


def meta_content(tree: HTMLParser, *, name: str | None = None, prop: str | None = None) -> str | None:
    if name:
        node = tree.css_first(f"meta[name='{name}']")
    else:
        node = tree.css_first(f"meta[property='{prop}']")
    if node is None:
        return None
    val = node.attributes.get("content")
    return collapse_ws(val) if val else None


def page_title(tree: HTMLParser) -> str | None:
    for selector in PAGE_NAME_SELECTORS:
        node = tree.css_first(selector)
        if node is None:
            continue
        if node.tag == "meta":
            val = node.attributes.get("content")
            if val:
                return collapse_ws(val)
        else:
            txt = collapse_ws(node.text(deep=False) or node.text())
            if txt:
                return txt[:200]
    if tree.root is not None:
        t = tree.css_first("title")
        if t is not None and t.text():
            title = collapse_ws(t.text())
            title = re.split(r"[|\-–—:]", title)[0].strip()
            return title[:200] or None
    return None


def absolute_url(base: str, href: str | None) -> str | None:
    if not href:
        return None
    href = href.strip()
    if href.startswith(("javascript:", "mailto:", "#")):
        return None
    try:
        return urljoin(base, href)
    except ValueError:
        return None


def normalize_text_for_search(text: str) -> str:
    return search_text(collapse_ws(normalize_fa(text or "")))
