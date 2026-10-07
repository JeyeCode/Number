"""تبدیل نتایج منابع به کاندیدهای یکسان (شامل منابع داده‌ساخت‌یافته مانند OSM)."""

from __future__ import annotations

from typing import Any

from ..extract.page import BusinessCandidate, extract_from_search_result
from ..geo.gazetteer import get_gazetteer
from ..phones.extract import find_phones
from ..relevance.scorer import score_relevance
from ..sources.base import SearchResultItem


def candidate_from_result_item(
    item: SearchResultItem,
    *,
    query_context: dict[str, Any] | None = None,
    source_reliability: float = 0.5,
    source_kind: str = "BUSINESS_DIRECTORY",
) -> list[BusinessCandidate]:
    """ساخت کاندید از یک نتیجه؛ اگر منبع داده ساخت‌یافته بدهد، مستقیماً استفاده می‌شود."""
    ctx = query_context or {}
    extra = item.extra or {}

    if extra.get("structured"):
        cand = extra.get("candidate")
        if isinstance(cand, BusinessCandidate):
            return [cand]
        return [_from_structured_fields(item, ctx, source_kind)]

    return extract_from_search_result(
        title=item.title or "",
        snippet=item.snippet or "",
        url=item.url,
        query_context=ctx,
    )


def _from_structured_fields(
    item: SearchResultItem, ctx: dict[str, Any], source_kind: str
) -> BusinessCandidate:
    """ساخت کاندید از فیلدهای ساخت‌یافته (مثل تگ‌های OSM) — بدون حدس‌زدن."""
    extra = item.extra
    name = extra.get("name") or item.title
    city = extra.get("city") or ctx.get("city")
    province = extra.get("province") or ctx.get("province")
    area_code = get_gazetteer().area_code_for_city(city, province)

    phones = []
    for raw in [extra.get("phone")] + list(extra.get("phones") or []):
        if raw:
            phones.extend(find_phones(str(raw), default_area_code=area_code, allow_local=False))
    if not phones:
        phones = find_phones(item.snippet or "", default_area_code=area_code)

    relevance = score_relevance(
        name=name,
        snippet=item.snippet,
        address=extra.get("address"),
        query_category=ctx.get("category"),
        query_business_type=ctx.get("business_type"),
    )
    return BusinessCandidate(
        name=name,
        phones=phones,
        address=extra.get("address"),
        website=extra.get("website"),
        owner_name=None,
        province=province,
        county=extra.get("county") or ctx.get("county"),
        city=city,
        location_method="structured" if city else "query_context",
        location_confidence=80 if city else 40,
        source_url=item.url,
        evidence_kind="DIRECTORY_ENTRY" if source_kind == "OPEN_DATA" else "PAGE_TEXT",
        snippet=item.snippet,
        confidence_hint=78,
        business_type=relevance.business_type,
        primary_category=relevance.primary_category or ctx.get("category"),
        relevance=relevance,
        raw={"structured": extra},
        query_category=ctx.get("category"),
        query_business_type=ctx.get("business_type"),
        query_city=ctx.get("city"),
        query_province=ctx.get("province"),
    )
