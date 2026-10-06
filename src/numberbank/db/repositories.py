"""پرس‌وجوهای آماده برای کسب‌وکارها، شماره‌ها، شواهد و Jobها (لایه دسترسی داده)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, and_, func, or_, select, true
from sqlalchemy.orm import Session

from ..domain.models import (
    Business,
    BusinessPhone,
    JobEvent,
    Location,
    Phone,
    SearchHistory,
    SearchJob,
    SearchQuery,
    Source,
    SourceObservation,
    ValidationResult,
)


@dataclass
class BusinessFilters:
    provinces: list[str] | None = None
    cities: list[str] | None = None
    categories: list[str] | None = None
    business_types: list[str] | None = None
    min_confidence: int | None = None
    max_confidence: int | None = None
    bands: list[str] | None = None
    phone_status: list[str] | None = None
    statuses: list[str] | None = None
    discovered_after: datetime | None = None
    discovered_before: datetime | None = None
    validated_after: datetime | None = None
    has_phone: bool | None = None
    include_synthetic: bool = False
    search: str | None = None
    source_key: str | None = None
    limit: int = 100
    offset: int = 0
    order_by: str = "confidence_desc"


def _business_query(session: Session, f: BusinessFilters) -> Select:
    stmt = select(Business)
    conditions = []
    if not f.include_synthetic:
        conditions.append(Business.is_synthetic.is_(False))
    if f.provinces:
        conditions.append(Business.province_name.in_(f.provinces))
    if f.cities:
        conditions.append(Business.city_name.in_(f.cities))
    if f.categories:
        conditions.append(Business.primary_category.in_(f.categories))
    if f.business_types:
        conditions.append(Business.business_type.in_(f.business_types))
    if f.min_confidence is not None:
        conditions.append(Business.confidence >= f.min_confidence)
    if f.max_confidence is not None:
        conditions.append(Business.confidence <= f.max_confidence)
    if f.bands:
        conditions.append(Business.confidence_band.in_(f.bands))
    if f.statuses:
        conditions.append(Business.status.in_(f.statuses))
    else:
        conditions.append(Business.status.in_(["ACTIVE", "QUARANTINED"]))
    if f.discovered_after:
        conditions.append(Business.discovered_at >= f.discovered_after)
    if f.discovered_before:
        conditions.append(Business.discovered_at <= f.discovered_before)
    if f.validated_after:
        conditions.append(Business.last_validated_at >= f.validated_after)
    if f.phone_status:
        sub = select(BusinessPhone.business_id).where(BusinessPhone.status.in_(f.phone_status))
        conditions.append(Business.id.in_(sub))
    if f.has_phone is True:
        conditions.append(Business.id.in_(select(BusinessPhone.business_id)))
    elif f.has_phone is False:
        conditions.append(~Business.id.in_(select(BusinessPhone.business_id)))
    if f.search:
        like = f"%{f.search.strip()}%"
        conditions.append(
            or_(Business.name.like(like), Business.address.like(like), Business.city_name.like(like),
                Business.owner_name.like(like))
        )
    if f.source_key:
        sub = (
            select(SourceObservation.business_id)
            .join(Source, Source.id == SourceObservation.source_id)
            .where(Source.key == f.source_key)
        )
        conditions.append(Business.id.in_(sub))
    if conditions:
        stmt = stmt.where(and_(*conditions))
    if f.order_by == "confidence_desc":
        stmt = stmt.order_by(Business.confidence.desc(), Business.id.asc())
    elif f.order_by == "newest":
        stmt = stmt.order_by(Business.discovered_at.desc(), Business.id.desc())
    elif f.order_by == "name":
        stmt = stmt.order_by(Business.name.asc())
    return stmt


def list_businesses(session: Session, filters: BusinessFilters) -> list[Business]:
    stmt = _business_query(session, filters).limit(min(filters.limit, 5000)).offset(filters.offset)
    return list(session.execute(stmt).scalars())


def count_businesses(session: Session, filters: BusinessFilters) -> int:
    sub = _business_query(session, filters).with_only_columns(Business.id).subquery()
    return int(session.execute(select(func.count()).select_from(sub)).scalar() or 0)


def businesses_for_export(session: Session, filters: BusinessFilters):
    """پیمایش جریانی رکوردها همراه با شماره‌ها (برای Export بدون بارگذاری همه در حافظه)."""
    stmt = _business_query(session, filters)
    batch = 500
    offset = filters.offset
    while True:
        rows = list(session.execute(stmt.limit(batch).offset(offset)).scalars())
        if not rows:
            break
        phone_map = phones_for_businesses(session, [b.id for b in rows])
        for business in rows:
            yield business, phone_map.get(business.id, [])
        offset += batch


def phones_for_businesses(session: Session, business_ids: list[int]) -> dict[int, list[Phone]]:
    if not business_ids:
        return {}
    rows = session.execute(
        select(BusinessPhone.business_id, Phone)
        .join(Phone, Phone.id == BusinessPhone.phone_id)
        .where(BusinessPhone.business_id.in_(business_ids))
        .order_by(BusinessPhone.is_primary.desc(), Phone.confidence.desc())
    ).all()
    out: dict[int, list[Phone]] = {}
    for business_id, phone in rows:
        out.setdefault(business_id, []).append(phone)
    return out


def business_detail(session: Session, business_id: int) -> dict[str, Any] | None:
    business = session.get(Business, business_id)
    if business is None:
        return None
    phones = phones_for_businesses(session, [business_id]).get(business_id, [])
    links = {
        link.phone_id: link
        for link in session.execute(
            select(BusinessPhone).where(BusinessPhone.business_id == business_id)
        ).scalars()
    }
    observations = list(
        session.execute(
            select(SourceObservation, Source)
            .join(Source, Source.id == SourceObservation.source_id)
            .where(SourceObservation.business_id == business_id)
            .order_by(SourceObservation.observed_at.desc())
            .limit(200)
        ).all()
    )
    validations = []
    if phones:
        validations = list(
            session.execute(
                select(ValidationResult)
                .where(ValidationResult.phone_id.in_([p.id for p in phones]))
                .order_by(ValidationResult.checked_at.desc())
                .limit(50)
            ).scalars()
        )
    return {
        "business": business,
        "phones": [
            {"phone": p, "link": links.get(p.id)} for p in phones
        ],
        "evidence": [{"observation": o, "source": s} for o, s in observations],
        "validations": validations,
    }


# --------------------------------------------------------------------------- #
# آمار داشبورد
# --------------------------------------------------------------------------- #
def dashboard_stats(session: Session, *, include_synthetic: bool = False) -> dict[str, Any]:
    def business_cond(*extra):
        conds = [] if include_synthetic else [Business.is_synthetic.is_(False)]
        conds.extend(extra)
        return and_(*conds) if conds else true()

    total_businesses = int(
        session.execute(select(func.count(Business.id)).where(business_cond())).scalar() or 0
    )
    active = int(
        session.execute(
            select(func.count(Business.id)).where(business_cond(Business.status == "ACTIVE"))
        ).scalar()
        or 0
    )
    quarantined = int(
        session.execute(
            select(func.count(Business.id)).where(business_cond(Business.status == "QUARANTINED"))
        ).scalar()
        or 0
    )
    high_confidence = int(
        session.execute(
            select(func.count(Business.id)).where(
                business_cond(Business.confidence_band.in_(["VERY_HIGH", "HIGH"]), Business.status == "ACTIVE")
            )
        ).scalar()
        or 0
    )
    new_24h = int(
        session.execute(
            select(func.count(Business.id)).where(
                business_cond(Business.discovered_at >= datetime.utcnow() - timedelta(hours=24))
            )
        ).scalar()
        or 0
    )
    phone_conds = [] if include_synthetic else [Phone.is_synthetic.is_(False)]
    phones_total = int(
        session.execute(select(func.count(Phone.id)).where(and_(*phone_conds) if phone_conds else true())).scalar() or 0
    )
    by_status = dict(
        session.execute(
            select(Phone.status, func.count(Phone.id))
            .where(and_(*phone_conds) if phone_conds else true())
            .group_by(Phone.status)
        ).all()
    )
    by_band = dict(
        session.execute(
            select(Business.confidence_band, func.count(Business.id))
            .where(business_cond())
            .group_by(Business.confidence_band)
        ).all()
    )
    by_category = dict(
        session.execute(
            select(Business.primary_category, func.count(Business.id))
            .where(business_cond(Business.status == "ACTIVE"))
            .group_by(Business.primary_category)
        ).all()
    )
    by_type = dict(
        session.execute(
            select(Business.business_type, func.count(Business.id))
            .where(business_cond(Business.status == "ACTIVE"))
            .group_by(Business.business_type)
        ).all()
    )
    cities_with_data = int(
        session.execute(
            select(func.count(func.distinct(Business.city_name))).where(business_cond())
        ).scalar()
        or 0
    )
    total_cities = int(session.execute(select(func.count(Location.id)).where(Location.kind == "city")).scalar() or 0)
    total_provinces = int(
        session.execute(select(func.count(Location.id)).where(Location.kind == "province")).scalar() or 0
    )
    queries_total = int(session.execute(select(func.count(SearchQuery.id))).scalar() or 0)
    queries_done = int(
        session.execute(select(func.count(SearchQuery.id)).where(SearchQuery.state == "DONE")).scalar() or 0
    )
    jobs_active = int(
        session.execute(
            select(func.count(SearchJob.id)).where(SearchJob.status.in_(["RUNNING", "PENDING", "PAUSED"]))
        ).scalar()
        or 0
    )
    duplicates = int(session.execute(select(func.count(func.distinct(Business.fingerprint)))).scalar() or 0)
    from ..domain.models import DuplicateRecord

    duplicate_merges = int(session.execute(select(func.count(DuplicateRecord.id))).scalar() or 0)
    observations = int(session.execute(select(func.count(SourceObservation.id))).scalar() or 0)
    errors = int(
        session.execute(select(func.count(JobEvent.id)).where(JobEvent.level == "ERROR")).scalar() or 0
    )
    sources_count = int(session.execute(select(func.count(Source.id))).scalar() or 0)
    source_usage = [
        {"key": key, "name": name, "observations": int(count)}
        for key, name, count in session.execute(
            select(Source.key, Source.name, func.count(SourceObservation.id))
            .join(SourceObservation, SourceObservation.source_id == Source.id, isouter=True)
            .group_by(Source.key, Source.name)
            .order_by(func.count(SourceObservation.id).desc())
        ).all()
    ]
    top_cities = [
        {"city": city, "count": int(count)}
        for city, count in session.execute(
            select(Business.city_name, func.count(Business.id))
            .where(business_cond(Business.city_name.is_not(None)))
            .group_by(Business.city_name)
            .order_by(func.count(Business.id).desc())
            .limit(15)
        ).all()
    ]
    by_province = [
        {"province": prov, "count": int(count)}
        for prov, count in session.execute(
            select(Business.province_name, func.count(Business.id))
            .where(business_cond(Business.province_name.is_not(None)))
            .group_by(Business.province_name)
            .order_by(func.count(Business.id).desc())
        ).all()
    ]

    return {
        "businesses": {
            "total": total_businesses,
            "active": active,
            "quarantined": quarantined,
            "high_confidence": high_confidence,
            "new_last_24h": new_24h,
            "unique_fingerprints": duplicates,
        },
        "phones": {
            "total": phones_total,
            "valid": int(by_status.get("VALID", 0)),
            "probable": int(by_status.get("PROBABLE", 0)),
            "unverified": int(by_status.get("UNVERIFIED", 0)),
            "invalid": int(by_status.get("INVALID", 0)),
            "quarantined": int(by_status.get("QUARANTINED", 0)),
            "by_status": {k: int(v) for k, v in by_status.items()},
        },
        "bands": {k: int(v) for k, v in by_band.items()},
        "categories": {k or "UNKNOWN": int(v) for k, v in by_category.items()},
        "business_types": {k or "UNKNOWN": int(v) for k, v in by_type.items()},
        "locations": {
            "cities_total": total_cities,
            "cities_with_data": cities_with_data,
            "cities_remaining": max(0, total_cities - cities_with_data),
            "coverage_percent": round(100.0 * cities_with_data / total_cities, 2) if total_cities else 0.0,
            "provinces_total": total_provinces,
        },
        "queries": {
            "total": queries_total,
            "done": queries_done,
            "pending": max(0, queries_total - queries_done),
            "progress_percent": round(100.0 * queries_done / queries_total, 2) if queries_total else 0.0,
        },
        "duplicates": {"merged_records": duplicate_merges},
        "evidence": {"observations": observations},
        "sources": {"count": sources_count, "usage": source_usage},
        "jobs": {"active": jobs_active},
        "errors": errors,
        "top_cities": top_cities,
        "by_province": by_province,
    }


def recent_jobs(session: Session, limit: int = 20) -> list[SearchJob]:
    return list(
        session.execute(select(SearchJob).order_by(SearchJob.id.desc()).limit(limit)).scalars()
    )


def recent_events(session: Session, limit: int = 50, level: str | None = None) -> list[JobEvent]:
    stmt = select(JobEvent).order_by(JobEvent.id.desc()).limit(limit)
    if level:
        stmt = stmt.where(JobEvent.level == level)
    return list(session.execute(stmt).scalars())


def job_detail(session: Session, job_id: int) -> dict[str, Any] | None:
    job = session.get(SearchJob, job_id)
    if job is None:
        return None
    events = list(
        session.execute(
            select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.id.desc()).limit(200)
        ).scalars()
    )
    history = list(
        session.execute(
            select(SearchHistory).where(SearchHistory.job_id == job_id)
            .order_by(SearchHistory.id.desc()).limit(100)
        ).scalars()
    )
    queries = list(
        session.execute(
            select(SearchQuery).where(SearchQuery.job_id == job_id)
            .order_by(SearchQuery.id.desc()).limit(100)
        ).scalars()
    )
    return {"job": job, "events": events, "history": history, "queries": queries}
