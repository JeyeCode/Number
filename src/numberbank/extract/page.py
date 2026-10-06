"""استخراج کاندید کسب‌وکار از صفحات وب و نتایج جستجو."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from ..geo.gazetteer import get_gazetteer
from ..logging_setup import get_logger
from ..phones.extract import PhoneCandidate, find_phones, find_phones_in_html
from ..relevance.lexicon import GENERIC_HOSTS
from ..relevance.scorer import RelevanceResult, is_likely_spam_page, score_relevance
from ..text.htmlparse import HTMLParser, Node
from ..text.normalize import collapse_ws, name_key, registrable_domain, strip_html, to_ascii_digits
from .structured import (
    absolute_url,
    extract_address_from_text,
    extract_json_ld,
    extract_owner_name,
    locate_text,
    meta_content,
    org_from_jsonld,
    page_title,
)

log = get_logger("extract")

ENTRY_SELECTORS = [
    "article", "li", "tr", "table",
    "div[class*=item]", "div[class*=card]", "div[class*=company]", "div[class*=business]",
    "div[class*=result]", "div[class*=listing]", "div[class*=ads]", "div[class*=ad-]",
    "div[class*=company]", "section",
]

HEADING_SELECTORS = ["h1", "h2", "h3", "h4", "h5", "h6", "strong", "b", ".title", ".name", "dt"]
SOCIAL_HOSTS = ("instagram.com", "t.me", "telegram.me", "facebook.com", "twitter.com", "x.com",
                "linkedin.com", "aparat.com", "youtube.com", "wa.me")
FAST_PHONE_RE = re.compile(r"(?:0\d{2}[\s\-\.]?\d{7,8}|\+?98\d{10}|09\d{9})")


@dataclass
class BusinessCandidate:
    """کاندید کسب‌وکار استخراج‌شده از یک صفحه/نتیجه (قبل از ذخیره‌سازی)."""

    name: str | None
    phones: list[PhoneCandidate] = field(default_factory=list)
    address: str | None = None
    website: str | None = None
    owner_name: str | None = None
    social_links: list[str] = field(default_factory=list)
    province: str | None = None
    county: str | None = None
    city: str | None = None
    location_method: str = "unknown"
    location_confidence: int = 0
    source_url: str | None = None
    evidence_kind: str = "PAGE_TEXT"
    snippet: str | None = None
    confidence_hint: int = 50
    business_type: str = "UNKNOWN"
    primary_category: str | None = None
    relevance: RelevanceResult | None = None
    page_text_excerpt: str | None = None
    is_spam: bool = False
    spam_flags: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    # زمینه پرس‌وجو (برای انتساب دسته/نوع در نبود سیگنال متن)
    query_category: str | None = None
    query_business_type: str | None = None
    query_city: str | None = None
    query_province: str | None = None

    @property
    def domain(self) -> str | None:
        return registrable_domain(self.website or self.source_url)

    @property
    def local_area_code(self) -> str | None:
        return get_gazetteer().area_code_for_city(self.city, self.province)

    def phone_values(self) -> list[str]:
        return [p.e164 for p in self.phones if p.e164]

    def signature(self) -> str:
        raw = f"{name_key(self.name)}|{self.city or ''}|{','.join(sorted(self.phone_values()))}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _clean_name(value: str | None) -> str | None:
    if not value:
        return None
    text = collapse_ws(value)
    text = re.sub(r"^[\s\-–—:،\.]+|[\s\-–—:،\.]+$", "", text)
    text = re.sub(r"^(نام|عنوان|نام شرکت|نام کسب\s*وکار)\s*[:：]\s*", "", text)
    if not text or len(text) < 3 or len(text) > 200:
        return None
    digits = re.sub(r"\D", "", to_ascii_digits(text))
    if len(digits) >= 7 and len(digits) >= len(text) * 0.6:
        return None
    if FAST_PHONE_RE.search(to_ascii_digits(text)):
        return None
    return text


def _node_text(node: Node, limit: int = 1200) -> str:
    try:
        text = node.text(separator=" ", strip=True)
    except TypeError:  # سازگاری نسخه‌ها
        text = node.text()
    return collapse_ws(text)[:limit]


def _node_heading(node: Node) -> str | None:
    for selector in HEADING_SELECTORS:
        for el in node.css(selector):
            text = _node_text(el, 200)
            name = _clean_name(text)
            if name:
                return name
    return None


def _node_links(node: Node, base_url: str) -> tuple[str | None, list[str]]:
    website: str | None = None
    socials: list[str] = []
    for a in node.css("a[href]"):
        href = a.attributes.get("href")
        url = absolute_url(base_url, href)
        if not url:
            continue
        dom = registrable_domain(url)
        if not dom:
            continue
        if dom in GENERIC_HOSTS or any(s in url for s in SOCIAL_HOSTS):
            if any(s in url for s in SOCIAL_HOSTS):
                socials.append(url)
            continue
        if website is None:
            website = url
    return website, socials[:5]


def _gather_phones_from_text(text: str, default_area_code: str | None) -> list[PhoneCandidate]:
    return find_phones(text, default_area_code=default_area_code, allow_local=bool(default_area_code))


def _build_candidate(
    *,
    name: str | None,
    phones: list[PhoneCandidate],
    text: str,
    url: str,
    base_url: str,
    evidence_kind: str,
    confidence_hint: int,
    node: Node | None = None,
    query_context: dict[str, Any] | None = None,
    snippet: str | None = None,
    phone_count: int = 1,
) -> BusinessCandidate | None:
    ctx = query_context or {}
    if not name and not phones:
        return None

    address = extract_address_from_text(text)
    website, socials = (None, [])
    if node is not None:
        website, socials = _node_links(node, base_url)

    loc = locate_text(
        " ".join(filter(None, [name, address, text[:600]])),
        hint_city=ctx.get("city"),
        hint_province=ctx.get("province"),
    )
    area_code = get_gazetteer().area_code_for_city(loc.city or ctx.get("city"), loc.province or ctx.get("province"))

    # شماره‌های محلی که با پیش‌شماره شهر تکمیل شده‌اند
    resolved: list[PhoneCandidate] = []
    seen: set[str] = set()
    for p in phones:
        if p.e164 and p.e164 not in seen:
            resolved.append(p)
            seen.add(p.e164)
    if not resolved and text:
        for p in _gather_phones_from_text(text, area_code):
            if p.e164 not in seen:
                resolved.append(p)
                seen.add(p.e164)

    owner = extract_owner_name(text)
    relevance = score_relevance(
        name=name,
        snippet=snippet,
        page_text=text[:4000],
        address=address,
        query_category=ctx.get("category"),
        query_business_type=ctx.get("business_type"),
    )
    is_spam, spam_flags = is_likely_spam_page(text, phone_count=phone_count)

    return BusinessCandidate(
        name=name,
        phones=resolved,
        address=address,
        website=website,
        owner_name=owner,
        social_links=socials,
        province=loc.province or ctx.get("province"),
        county=loc.county,
        city=loc.city or ctx.get("city"),
        location_method=loc.method,
        location_confidence=loc.confidence,
        source_url=url,
        evidence_kind=evidence_kind,
        snippet=(snippet or text)[:500] if (snippet or text) else None,
        confidence_hint=confidence_hint,
        business_type=relevance.business_type,
        primary_category=relevance.primary_category or ctx.get("category"),
        relevance=relevance,
        page_text_excerpt=text[:1500] or None,
        is_spam=is_spam,
        spam_flags=spam_flags,
        query_category=ctx.get("category"),
        query_business_type=ctx.get("business_type"),
        query_city=ctx.get("city"),
        query_province=ctx.get("province"),
    )


def extract_from_search_result(
    *,
    title: str,
    snippet: str,
    url: str,
    query_context: dict[str, Any] | None = None,
) -> list[BusinessCandidate]:
    """استخراج کاندید از نتیجه جستجو (عنوان + چکیده) — Evidence از نوع SEARCH_SNIPPET."""
    ctx = query_context or {}
    text = collapse_ws(f"{title} . {snippet}")
    area_code = get_gazetteer().area_code_for_city(ctx.get("city"), ctx.get("province"))
    phones = _gather_phones_from_text(text, area_code)
    if not phones:
        return []
    name = _clean_name(title)
    candidate = _build_candidate(
        name=name,
        phones=phones,
        text=text,
        url=url,
        base_url=url,
        evidence_kind="SEARCH_SNIPPET",
        confidence_hint=45,
        query_context=ctx,
        snippet=snippet[:500],
    )
    return [candidate] if candidate else []


def extract_from_page(
    html: str,
    url: str,
    *,
    query_context: dict[str, Any] | None = None,
    max_candidates: int = 25,
    source_kind: str = "BUSINESS_DIRECTORY",
    directory_selector: str | None = None,
) -> list[BusinessCandidate]:
    """استخراج همه کاندیدهای یک صفحه: JSON-LD → بلوک‌های دایرکتوری → سطح صفحه."""
    if not html:
        return []
    ctx = query_context or {}
    tree = HTMLParser(html)
    text_preview = collapse_ws(strip_html(html))[:6000]
    base_url = url
    out: list[BusinessCandidate] = []
    seen_sigs: set[str] = set()

    def add(cand: BusinessCandidate | None) -> None:
        if cand is None:
            return
        sig = cand.signature()
        if sig in seen_sigs:
            return
        seen_sigs.add(sig)
        out.append(cand)

    boot_area_code = get_gazetteer().area_code_for_city(ctx.get("city"), ctx.get("province"))

    # ---------- ۱) JSON-LD ----------
    jsonld_count = 0
    for node in extract_json_ld(html):
        org = org_from_jsonld(node)
        if not org:
            continue
        phones: list[PhoneCandidate] = []
        for raw_phone in org.get("phones", []):
            phones.extend(find_phones(raw_phone, default_area_code=boot_area_code, allow_local=False))
        if not phones and not org.get("name"):
            continue
        cand = _build_candidate(
            name=_clean_name(org.get("name")),
            phones=phones,
            text=collapse_ws(" ".join(filter(None, [org.get("name"), org.get("address")]))),
            url=url,
            base_url=base_url,
            evidence_kind="JSONLD",
            confidence_hint=85,
            query_context=ctx,
        )
        if cand:
            cand.website = cand.website or org.get("website")
            cand.owner_name = cand.owner_name or org.get("owner_name")
            cand.social_links = cand.social_links or org.get("social_links") or []
            cand.raw["jsonld"] = org.get("jsonld")
            if cand.address is None and org.get("address"):
                cand.address = org["address"]
            if not cand.city:
                cand.city = ctx.get("city")
            add(cand)
            jsonld_count += 1
    if jsonld_count:
        log.debug("از %s: %s گره JSON-LD استخراج شد", url, jsonld_count)

    # ---------- ۲) بلوک‌های دایرکتوری/لیست ----------
    selectors = [directory_selector] if directory_selector else []
    selectors.extend(ENTRY_SELECTORS)
    entries: list[tuple[Node, str]] = []
    seen_texts: set[str] = set()
    for selector in selectors:
        try:
            nodes = tree.css(selector)
        except Exception:  # pragma: no cover - انتخابگر نامعتبر از پیکربندی کاربر
            continue
        for node in nodes:
            t = _node_text(node, 900)
            if not (12 <= len(t) <= 900):
                continue
            ascii_t = to_ascii_digits(t)
            if not FAST_PHONE_RE.search(ascii_t):
                continue
            key = t[:160]
            if key in seen_texts:
                continue
            seen_texts.add(key)
            entries.append((node, t))
        if len(entries) >= max_candidates * 3:
            break

    for node, node_text in entries:
        phones = find_phones_in_html(node.html or "", default_area_code=boot_area_code)
        if not phones:
            continue
        name = _node_heading(node)
        cand = _build_candidate(
            name=name,
            phones=phones,
            text=node_text,
            url=url,
            base_url=base_url,
            evidence_kind="DIRECTORY_ENTRY",
            confidence_hint=70,
            node=node,
            query_context=ctx,
            phone_count=len(phones),
        )
        add(cand)
        if len(out) >= max_candidates:
            break

    # ---------- ۳) سطح صفحه (سایت اختصاصی کسب‌وکار) ----------
    if not out or (len(out) == 1 and out[0].name is None):
        title = page_title(tree)
        og_site = meta_content(tree, prop="og:site_name")
        description = meta_content(tree, prop="og:description") or meta_content(tree, name="description")
        page_phones = find_phones_in_html(html, default_area_code=boot_area_code)
        if page_phones:
            name = _clean_name(og_site) or _clean_name(title)
            cand = _build_candidate(
                name=name,
                phones=page_phones[:8],
                text=text_preview,
                url=url,
                base_url=base_url,
                evidence_kind="PAGE_TEXT",
                confidence_hint=60,
                node=tree.body,
                query_context=ctx,
                snippet=description,
                phone_count=len(page_phones),
            )
            add(cand)

    return out
