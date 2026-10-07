"""API و داشبورد وب (FastAPI + Jinja2).

نکته محیط اجرا: سرور روی 0.0.0.0 گوش می‌دهد و همه درخواست‌های سمت مرورگر
با URLهای نسبی انجام می‌شود تا در محیط‌های Preview/پروکسی هم کار کند.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .. import __version__
from ..config import get_settings
from ..db.repositories import (
    BusinessFilters,
    business_detail,
    count_businesses,
    job_detail,
    list_businesses,
    phones_for_businesses,
)
from ..db.session import get_database
from ..domain.enums import BusinessType, CategoryCode, ConfidenceBand
from ..logging_setup import get_logger, setup_logging
from ..services.discovery_service import create_job, request_cancel

log = get_logger("api")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

app = FastAPI(title="NumberBank API", version=__version__,
              description="کشف، اعتبارسنجی و مدیریت شماره‌های تماس تجاری (ایران)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # API عمومی و بدون احراز هویت؛ در استقرار واقعی محدود کنید
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
if (WEB_DIR / "static").exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")


@app.on_event("startup")
def _startup() -> None:
    settings = get_settings()
    setup_logging(settings)
    get_database(settings).create_all()
    log.info("NumberBank API آماده است")


def _filters_from_query(
    province: list[str] | None = Query(None),
    city: list[str] | None = Query(None),
    category: list[str] | None = Query(None),
    business_type: list[str] | None = Query(None),
    band: list[str] | None = Query(None),
    phone_status: list[str] | None = Query(None),
    min_confidence: int | None = Query(None),
    search: str | None = Query(None),
    include_synthetic: bool = Query(False),
    limit: int = Query(50, le=5000),
    offset: int = Query(0, ge=0),
    order_by: str = Query("confidence_desc"),
) -> BusinessFilters:
    return BusinessFilters(
        provinces=province,
        cities=city,
        categories=[c.upper() for c in category] if category else None,
        business_types=[b.upper() for b in business_type] if business_type else None,
        bands=[b.upper() for b in band] if band else None,
        phone_status=[s.upper() for s in phone_status] if phone_status else None,
        min_confidence=min_confidence,
        search=search,
        include_synthetic=include_synthetic,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def _business_row(business, phones) -> dict[str, Any]:
    return {
        "id": business.id,
        "name": business.name,
        "owner_name": business.owner_name,
        "province": business.province_name,
        "county": business.county_name,
        "city": business.city_name,
        "address": business.address,
        "phones": [
            {"id": p.id, "number": p.national, "e164": p.e164, "type": p.type, "status": p.status,
             "confidence": p.confidence, "band": p.confidence_band, "synthetic": p.is_synthetic}
            for p in phones
        ],
        "website": business.website,
        "business_type": business.business_type,
        "business_type_label": (
            BusinessType(business.business_type).label_fa
            if business.business_type in BusinessType._value2member_map_
            else business.business_type
        ),
        "primary_category": business.primary_category,
        "category_label": (
            CategoryCode(business.primary_category).label_fa
            if business.primary_category in CategoryCode._value2member_map_
            else business.primary_category
        ),
        "relevance_score": business.relevance_score,
        "confidence": business.confidence,
        "confidence_band": business.confidence_band,
        "band_label": (
            ConfidenceBand(business.confidence_band).label_fa
            if business.confidence_band in ConfidenceBand._value2member_map_
            else business.confidence_band
        ),
        "status": business.status,
        "source_count": business.source_count,
        "independent_source_count": business.independent_source_count,
        "discovered_at": business.discovered_at.isoformat() if business.discovered_at else None,
        "last_validated_at": business.last_validated_at.isoformat() if business.last_validated_at else None,
        "is_synthetic": business.is_synthetic,
        "field_status": business.field_status or {},
    }


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health() -> dict:
    settings = get_settings()
    database = get_database(settings)
    return {
        "status": "ok" if database.healthcheck() else "degraded",
        "version": __version__,
        "database": "sqlite" if settings.is_sqlite else "postgresql",
        "time": datetime.utcnow().isoformat(),
    }


@app.get("/api/stats")
def api_stats(include_synthetic: bool = Query(False)) -> dict:
    from ..services.maintenance_service import full_stats

    return full_stats(include_synthetic=include_synthetic)


@app.get("/api/businesses")
def api_businesses(filters: BusinessFilters = None) -> dict:  # type: ignore[assignment]
    raise HTTPException(status_code=400, detail="از /api/v1/businesses استفاده کنید")


@app.get("/api/v1/businesses")
def api_v1_businesses(
    request: Request,
    province: list[str] | None = Query(None),
    city: list[str] | None = Query(None),
    category: list[str] | None = Query(None),
    business_type: list[str] | None = Query(None),
    band: list[str] | None = Query(None),
    phone_status: list[str] | None = Query(None),
    min_confidence: int | None = Query(None, ge=0, le=100),
    search: str | None = Query(None),
    include_synthetic: bool = Query(False),
    limit: int = Query(50, le=5000),
    offset: int = Query(0, ge=0),
    order_by: str = Query("confidence_desc"),
) -> dict:
    filters = _filters_from_query(
        province, city, category, business_type, band, phone_status, min_confidence,
        search, include_synthetic, limit, offset, order_by,
    )
    database = get_database()
    with database.session() as session:
        businesses = list_businesses(session, filters)
        total = count_businesses(session, filters)
        phones = phones_for_businesses(session, [b.id for b in businesses])
        items = [_business_row(b, phones.get(b.id, [])) for b in businesses]
    return {"total": total, "count": len(items), "limit": limit, "offset": offset, "items": items}


@app.get("/api/v1/businesses/{business_id}")
def api_business_detail(business_id: int, include_evidence: bool = Query(True)) -> dict:
    database = get_database()
    with database.session() as session:
        detail = business_detail(session, business_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="کسب‌وکار یافت نشد")
    business = detail["business"]
    payload = _business_row(business, [p["phone"] for p in detail["phones"]])
    payload["confidence_breakdown"] = business.confidence_breakdown
    payload["relevance_evidence"] = business.relevance_evidence
    payload["phone_links"] = [
        {
            "phone_id": item["phone"].id,
            "is_primary": item["link"].is_primary if item["link"] else False,
            "link_confidence": item["link"].confidence if item["link"] else None,
            "link_status": item["link"].status if item["link"] else None,
        }
        for item in detail["phones"]
    ]
    if include_evidence:
        payload["evidence"] = [
            {
                "url": item["observation"].url,
                "source": item["source"].name,
                "source_key": item["source"].key,
                "reliability": item["source"].reliability,
                "kind": item["observation"].evidence_kind,
                "observed_at": item["observation"].observed_at.isoformat()
                if item["observation"].observed_at else None,
                "snippet": item["observation"].snippet,
                "extracted": item["observation"].extracted,
                "status": item["observation"].status,
            }
            for item in detail["evidence"]
        ]
        payload["validation_history"] = [
            {
                "checked_at": v.checked_at.isoformat() if v.checked_at else None,
                "band": v.band,
                "status": v.result,
                "confidence": v.confidence,
                "method": v.method,
            }
            for v in detail["validations"]
        ]
    return payload


@app.get("/api/v1/phones")
def api_phones(
    status: list[str] | None = Query(None),
    phone_type: str | None = Query(None, alias="type"),
    min_confidence: int | None = Query(None),
    include_synthetic: bool = Query(False),
    limit: int = Query(100, le=5000),
    offset: int = Query(0, ge=0),
) -> dict:
    from sqlalchemy import func, select

    from ..domain.models import Phone

    database = get_database()
    with database.session() as session:
        stmt = select(Phone)
        count_stmt = select(func.count(Phone.id))
        from sqlalchemy import and_

        conds = []
        if not include_synthetic:
            conds.append(Phone.is_synthetic.is_(False))
        if status:
            conds.append(Phone.status.in_([s.upper() for s in status]))
        if phone_type:
            conds.append(Phone.type == phone_type.upper())
        if min_confidence:
            conds.append(Phone.confidence >= min_confidence)
        if conds:
            stmt = stmt.where(and_(*conds))
            count_stmt = count_stmt.where(and_(*conds))
        rows = session.execute(stmt.order_by(Phone.confidence.desc()).limit(limit).offset(offset)).scalars()
        total = int(session.execute(count_stmt).scalar() or 0)
        items = [
            {
                "id": p.id, "number": p.national, "e164": p.e164, "type": p.type,
                "area_code": p.area_code, "status": p.status, "confidence": p.confidence,
                "band": p.confidence_band, "source_count": p.source_count,
                "fake_signals": p.fake_signals, "is_synthetic": p.is_synthetic,
                "last_validated_at": p.last_validated_at.isoformat() if p.last_validated_at else None,
            }
            for p in rows
        ]
    return {"total": total, "items": items}


class CreateJobRequest(BaseModel):
    name: str | None = None
    provinces: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    counties: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    business_types: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    target: int = 500
    min_confidence: int = 70
    max_pages_per_query: int = 2
    workers: int = 4
    time_limit: int = 900
    limit_cities: int | None = None
    offline: bool = False


@app.post("/api/v1/jobs")
async def api_create_job(payload: CreateJobRequest, run_now: bool = Query(False)) -> dict:
    settings = get_settings()
    if payload.offline:
        settings = settings.model_copy(update={"offline": True})
    job_id = create_job(
        name=payload.name,
        provinces=payload.provinces,
        cities=payload.cities,
        counties=payload.counties,
        categories=[c.upper() for c in payload.categories],
        business_types=[b.upper() for b in payload.business_types],
        sources=payload.sources,
        target=payload.target,
        min_confidence=payload.min_confidence,
        max_pages_per_query=payload.max_pages_per_query,
        workers=payload.workers,
        time_limit=payload.time_limit,
        limit_cities=payload.limit_cities,
        settings=settings,
    )
    result: dict[str, Any] = {"job_id": job_id, "status": "PENDING"}
    if run_now:
        result["note"] = "برای اجرا: numberbank run-job <id> یا numberbank worker --once"
    return result


@app.get("/api/v1/jobs")
def api_jobs(limit: int = Query(20, le=200)) -> dict:
    database = get_database()
    with database.session() as session:
        from ..domain.models import SearchJob

        jobs = session.query(SearchJob).order_by(SearchJob.id.desc()).limit(limit).all()
        return {
            "items": [
                {
                    "id": j.id, "name": j.name, "status": j.status, "progress": j.progress,
                    "target": j.target, "businesses_new": j.businesses_new, "phones_new": j.phones_new,
                    "duplicates": j.duplicates_found, "errors": j.errors,
                    "queries": {"total": j.total_queries, "done": j.done_queries},
                    "message": j.message,
                    "started_at": j.started_at.isoformat() if j.started_at else None,
                    "finished_at": j.finished_at.isoformat() if j.finished_at else None,
                }
                for j in jobs
            ]
        }


@app.get("/api/v1/jobs/{job_id}")
def api_job_detail(job_id: int) -> dict:
    database = get_database()
    with database.session() as session:
        detail = job_detail(session, job_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Job یافت نشد")
    job = detail["job"]
    return {
        "job": {
            "id": job.id, "name": job.name, "status": job.status, "progress": job.progress,
            "params": job.params, "target": job.target,
            "businesses_new": job.businesses_new, "phones_new": job.phones_new,
            "duplicates_found": job.duplicates_found, "irrelevant_rejected": job.irrelevant_rejected,
            "errors": job.errors, "message": job.message,
        },
        "events": [
            {"level": e.level, "code": e.code, "message": e.message, "at": e.created_at.isoformat(),
             "context": e.context}
            for e in detail["events"]
        ],
        "history": [
            {"source_key": h.source_key, "query": h.query_text, "status": h.status, "results": h.results,
             "new_results": h.new_results, "errors": h.errors, "duration_ms": h.duration_ms,
             "at": h.started_at.isoformat() if h.started_at else None}
            for h in detail["history"]
        ],
    }


@app.post("/api/v1/jobs/{job_id}/cancel")
def api_cancel_job(job_id: int) -> dict:
    if not request_cancel(job_id):
        raise HTTPException(status_code=404, detail="Job یافت نشد")
    return {"job_id": job_id, "status": "cancel_requested"}


@app.get("/api/v1/coverage")
def api_coverage(province: list[str] | None = Query(None)) -> dict:
    from ..pipeline.planner import QueryPlanner

    database = get_database()
    with database.session() as session:
        return QueryPlanner(session, get_settings()).coverage_report(provinces=province)


@app.get("/api/v1/sources")
def api_sources() -> dict:
    """فهرست منابع با وضعیت دسترسی و تعداد شواهد ثبت‌شده در دیتابیس."""
    from sqlalchemy import func, select

    from ..domain.models import Source as SourceRow
    from ..domain.models import SourceObservation
    from ..sources.registry import SourceRegistry

    summary = SourceRegistry().summary()
    database = get_database()
    with database.session() as session:
        usage = {
            key: int(count)
            for key, count in session.execute(
                select(SourceRow.key, func.count(SourceObservation.id))
                .join(SourceObservation, SourceObservation.source_id == SourceRow.id, isouter=True)
                .group_by(SourceRow.key)
            ).all()
        }
    items = []
    for src in summary.get("sources", []):
        items.append(
            {
                **src,
                "enabled": bool(src.get("available")),
                "observations": usage.get(src.get("key"), 0),
                "note": src.get("reason") or ("در دسترس" if src.get("available") else "غیرفعال"),
            }
        )
    return {**summary, "items": items}


@app.get("/api/v1/queries")
def api_queries(limit: int = Query(100, le=1000), state: str | None = Query(None),
                city: str | None = Query(None)) -> dict:
    from sqlalchemy import select

    from ..domain.models import SearchQuery

    database = get_database()
    with database.session() as session:
        stmt = select(SearchQuery).order_by(SearchQuery.id.desc()).limit(limit)
        if state:
            stmt = stmt.where(SearchQuery.state == state.upper())
        if city:
            stmt = stmt.where(SearchQuery.city_name == city)
        rows = session.execute(stmt).scalars()
        return {
            "items": [
                {
                    "id": q.id, "source": q.source_key, "query": q.query_text, "city": q.city_name,
                    "category": q.category, "state": q.state, "depth": q.depth,
                    "runs": q.run_count, "results": q.results_seen, "new_results": q.new_results,
                    "failures": q.failures,
                    "last_run_at": q.last_run_at.isoformat() if q.last_run_at else None,
                    "next_run_at": q.next_run_at.isoformat() if q.next_run_at else None,
                }
                for q in rows
            ]
        }


@app.get("/api/v1/locations")
def api_locations(province: str | None = Query(None), limit: int = Query(200, le=5000)) -> dict:
    from sqlalchemy import select

    from ..domain.models import Location

    database = get_database()
    with database.session() as session:
        provinces = [
            {"id": p.id, "name": p.name, "area_codes": p.area_codes}
            for p in session.execute(select(Location).where(Location.kind == "province")).scalars()
        ]
        if province:
            prov = session.execute(
                select(Location).where(Location.kind == "province", Location.name == province)
            ).scalar_one_or_none()
            query = select(Location).where(Location.kind == "city")
            if prov is not None:
                query = query.where(Location.province_id == prov.id)
            cities = [c.name for c in session.execute(query.limit(limit)).scalars()]
        else:
            cities = [c.name for c in session.execute(
                select(Location).where(Location.kind == "city").limit(limit)
            ).scalars()]
    return {"provinces": provinces, "cities": cities}


@app.get("/api/v1/export")
def api_export(
    fmt: str = Query("csv", pattern="^(csv|xlsx|json)$"),
    province: list[str] | None = Query(None),
    city: list[str] | None = Query(None),
    category: list[str] | None = Query(None),
    min_confidence: int | None = Query(None),
    search: str | None = Query(None),
    include_synthetic: bool = Query(False),
    limit: int = Query(50000, le=500000),
) -> FileResponse:
    from ..services.export_service import export_businesses

    filters = BusinessFilters(
        provinces=province, cities=city,
        categories=[c.upper() for c in category] if category else None,
        min_confidence=min_confidence, search=search, include_synthetic=include_synthetic, limit=limit,
    )
    report = export_businesses(filters, fmt=fmt)
    return FileResponse(report.path, filename=Path(report.path).name)


@app.get("/api/v1/dedup")
def api_dedup(limit: int = Query(50, le=500)) -> dict:
    from sqlalchemy import select

    from ..domain.models import DuplicateRecord

    database = get_database()
    with database.session() as session:
        rows = session.execute(
            select(DuplicateRecord).order_by(DuplicateRecord.id.desc()).limit(limit)
        ).scalars()
        items = [
            {"canonical_id": r.canonical_id, "merged_id": r.merged_id, "score": r.score,
             "reason": r.reason, "signals": r.signals,
             "at": r.created_at.isoformat() if r.created_at else None}
            for r in rows
        ]
    return {"items": items}


# --------------------------------------------------------------------------- #
# داشبورد وب
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "dashboard.html", {"version": __version__, "active": "dashboard"})


@app.get("/businesses", response_class=HTMLResponse)
def businesses_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "businesses.html", {"version": __version__, "active": "businesses"})


@app.get("/businesses/{business_id}", response_class=HTMLResponse)
def business_page(request: Request, business_id: int) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "business_detail.html", {"version": __version__, "business_id": business_id}
    )


@app.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "jobs.html", {"version": __version__, "active": "jobs"})


@app.get("/coverage", response_class=HTMLResponse)
def coverage_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "coverage.html", {"version": __version__, "active": "coverage"})


@app.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "sources.html", {"version": __version__, "active": "sources"})


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:  # pragma: no cover
    log.exception("خطای پیش‌بینی‌نشده در API: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "خطای داخلی سرور", "error": str(exc)[:300]})


def create_app() -> FastAPI:
    return app


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    settings = get_settings()
    uvicorn.run("numberbank.api.app:app", host=settings.host, port=settings.port)
