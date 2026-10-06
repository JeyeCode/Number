"""تشخیص ارتباط واقعی کسب‌وکار با حوزه هدف (Business Relevance)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..domain.enums import BusinessType, CategoryCode
from ..text.normalize import normalize_fa, search_text
from .lexicon import (
    BUSINESS_SIGNALS,
    SPAM_MARKERS,
    category_terms,
    negative_terms,
    role_terms,
)

TERM_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _term_re(term: str) -> re.Pattern[str]:
    key = normalize_fa(term, zwnj="remove")
    if key not in TERM_RE_CACHE:
        # تطبیق با هر جداکننده‌ای بین کلمات (فاصله/نیم‌فاصله/خط تیره)
        parts = [re.escape(p) for p in normalize_fa(term, zwnj="space").split() if p]
        pattern = r"(?<![\w])" + r"[\s\-_\u200c]*".join(parts) + r"(?![\w])"
        TERM_RE_CACHE[key] = re.compile(pattern, re.IGNORECASE)
    return TERM_RE_CACHE[key]


@dataclass
class RelevanceResult:
    score: int
    category_scores: dict[str, float] = field(default_factory=dict)
    role_scores: dict[str, float] = field(default_factory=dict)
    matched_terms: list[str] = field(default_factory=list)
    negative_terms: list[str] = field(default_factory=list)
    spam_flags: list[str] = field(default_factory=list)
    primary_category: str | None = None
    business_type: str = BusinessType.UNKNOWN.value
    reasons: list[str] = field(default_factory=list)

    @property
    def is_relevant(self) -> bool:
        return self.score >= 30

    @property
    def is_spam(self) -> bool:
        return bool(self.spam_flags)


def _match_terms(text: str, terms: list[tuple[str, float]]) -> tuple[float, list[str]]:
    total = 0.0
    matched: list[str] = []
    for term, weight in terms:
        if _term_re(term).search(text):
            total += weight
            matched.append(term)
    return total, matched


def score_relevance(
    *,
    name: str | None = None,
    snippet: str | None = None,
    page_text: str | None = None,
    address: str | None = None,
    query_category: str | None = None,
    query_business_type: str | None = None,
) -> RelevanceResult:
    """امتیاز ارتباط (۰..۱۰۰) با شواهد واژگانی قابل بازبینی."""
    name_t = search_text(name or "")
    body_t = search_text(" ".join(filter(None, [snippet, page_text, address])))
    if not name_t and not body_t:
        return RelevanceResult(score=0, reasons=["بدون متن قابل بررسی"])

    # وزن نام کسب‌وکار بالاتر از متن صفحه است
    cat_scores: dict[str, float] = {}
    matched_all: list[str] = []
    for code in (CategoryCode.DETERGENT.value, CategoryCode.HYGIENE.value,
                 CategoryCode.COSMETIC.value, CategoryCode.CELLULOSE.value):
        terms = category_terms(code)
        name_hit, name_terms = _match_terms(name_t, terms)
        body_hit, body_terms = _match_terms(body_t, terms)
        cat_scores[code] = name_hit * 2.2 + body_hit * 0.8
        matched_all.extend(name_terms)
        matched_all.extend(body_terms)

    role_scores: dict[str, float] = {}
    for code in (
        BusinessType.WHOLESALER.value,
        BusinessType.DISTRIBUTOR.value,
        BusinessType.RETAILER.value,
        BusinessType.MANUFACTURER.value,
    ):
        terms = role_terms(code)
        name_hit, name_terms = _match_terms(name_t, terms)
        body_hit, body_terms = _match_terms(body_t, terms)
        role_scores[code] = name_hit * 2.0 + body_hit * 0.6
        matched_all.extend(name_terms)
        matched_all.extend(body_terms)

    signal_score, signal_terms = _match_terms(body_t, BUSINESS_SIGNALS)
    matched_all.extend(signal_terms)

    neg_score, neg_matched = _match_terms(body_t, negative_terms())
    neg_score_name, neg_name = _match_terms(name_t, negative_terms())
    neg_score += neg_score_name * 1.5
    neg_matched.extend(neg_name)

    spam_score, spam_matched = _match_terms(body_t, SPAM_MARKERS)

    best_cat = max(cat_scores.items(), key=lambda kv: kv[1]) if cat_scores else ("", 0.0)
    primary = best_cat[0] if best_cat[1] > 0 else None

    # انتخاب نوع کسب‌وکار
    best_role = max(role_scores.items(), key=lambda kv: kv[1]) if role_scores else ("", 0.0)
    if query_business_type and best_role[1] <= 0:
        business_type = query_business_type
    elif best_role[1] > 0:
        business_type = best_role[0]
    else:
        business_type = BusinessType.UNKNOWN.value

    raw = (
        min(best_cat[1], 22.0) * 2.2          # شدت تطبیق دسته (سقف ~۴۸)
        + min(signal_score, 12.0) * 1.2        # سیگنال‌های صفحه (سقف ~۱۴)
        + (10.0 if primary else 0.0)
        + (8.0 if name_t and best_cat[1] > 0 else 0.0)
    )
    if query_category and primary == query_category:
        raw += 6.0
    raw -= neg_score * 6.0
    raw -= spam_score * 12.0

    score = int(max(0, min(100, round(raw))))
    reasons: list[str] = []
    if primary:
        reasons.append(f"تطبیق دسته: {CategoryCode(primary).label_fa}")
    if business_type != BusinessType.UNKNOWN.value:
        reasons.append(f"نقش تجاری: {BusinessType(business_type).label_fa}")
    if neg_matched:
        reasons.append("واژگان منفی: " + "، ".join(sorted(set(neg_matched))[:5]))
    if spam_matched:
        reasons.append("نشانه اسپم: " + "، ".join(sorted(set(spam_matched))[:3]))

    return RelevanceResult(
        score=score,
        category_scores={k: round(v, 2) for k, v in cat_scores.items()},
        role_scores={k: round(v, 2) for k, v in role_scores.items()},
        matched_terms=sorted(set(matched_all))[:30],
        negative_terms=sorted(set(neg_matched))[:15],
        spam_flags=sorted(set(spam_matched))[:8],
        primary_category=primary,
        business_type=business_type,
        reasons=reasons,
    )


def is_likely_spam_page(text: str, *, link_count: int = 0, phone_count: int = 1) -> tuple[bool, list[str]]:
    """تشخیص صفحه اسپم/کپی‌برداری‌شده با نشانه‌های ساده و قابل توضیح."""
    flags: list[str] = []
    t = search_text(text or "")[:20000]
    if not t:
        return False, flags
    spam_score, spam_matched = _match_terms(t, SPAM_MARKERS)
    if spam_matched:
        flags.append("واژگان اسپم: " + "، ".join(spam_matched[:3]))
    if spam_score >= 10:
        flags.append("تراکم واژگان اسپم بالا")
    if link_count > 250 and len(t) < 2500:
        flags.append("انبوه لینک با متن کم")
    if phone_count >= 25:
        flags.append(f"تعداد غیرمتعارف شماره در یک صفحه ({phone_count})")
    elif phone_count >= 12 and len(t) < 1200:
        flags.append(f"تراکم بالای شماره با متن کم ({phone_count} شماره)")
    return bool(flags), flags
