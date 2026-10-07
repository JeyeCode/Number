"""هم‌آهنگی Jobهای کشف داده (ساخت، اجرا، توقف، گزارش)."""

from __future__ import annotations

from typing import Any

from ..config import Settings, get_settings
from ..db.session import Database, get_database
from ..domain.enums import BusinessType, CategoryCode, JobKind, JobStatus
from ..domain.models import JobEvent, SearchJob, utcnow
from ..logging_setup import get_logger
from ..pipeline.discovery import DiscoveryRunner, JobReport

log = get_logger("service.discovery")


def create_job(
    *,
    name: str | None = None,
    provinces: list[str] | None = None,
    counties: list[str] | None = None,
    cities: list[str] | None = None,
    categories: list[str] | None = None,
    business_types: list[str] | None = None,
    sources: list[str] | None = None,
    target: int | None = None,
    min_confidence: int | None = None,
    max_pages_per_query: int | None = None,
    max_pages_per_job: int | None = None,
    workers: int | None = None,
    time_limit: int | None = None,
    limit_cities: int | None = None,
    fetch_candidate_pages: bool | None = None,
    kind: str = JobKind.DISCOVERY.value,
    database: Database | None = None,
    settings: Settings | None = None,
    extra_params: dict[str, Any] | None = None,
) -> int:
    settings = settings or get_settings()
    database = database or get_database(settings)

    params: dict[str, Any] = {
        "provinces": provinces or [],
        "counties": counties or [],
        "cities": cities or [],
        "categories": categories or [
            CategoryCode.DETERGENT.value, CategoryCode.HYGIENE.value,
            CategoryCode.COSMETIC.value, CategoryCode.CELLULOSE.value,
        ],
        "business_types": business_types or [
            BusinessType.WHOLESALER.value, BusinessType.DISTRIBUTOR.value,
            BusinessType.RETAILER.value, BusinessType.MANUFACTURER.value,
        ],
        "sources": sources or [],
        "target": target if target is not None else settings.default_target,
        "min_confidence": min_confidence if min_confidence is not None else settings.min_confidence,
        "max_pages_per_query": max_pages_per_query or settings.max_pages_per_query,
        "max_pages_per_job": max_pages_per_job or settings.max_pages_per_job,
        "workers": workers or settings.workers,
        "time_limit": time_limit or settings.job_time_limit,
        "limit_cities": limit_cities,
        "fetch_candidate_pages": (
            settings.fetch_candidate_pages if fetch_candidate_pages is None else fetch_candidate_pages
        ),
        "run_dedup": True,
    }
    if extra_params:
        params.update(extra_params)

    label = name or _auto_name(params)
    with database.session() as session:
        job = SearchJob(
            name=label,
            kind=kind,
            status=JobStatus.PENDING.value,
            params=params,
            target=int(params["target"]),
            min_confidence=int(params["min_confidence"]),
        )
        session.add(job)
        session.flush()
        session.add(
            JobEvent(
                job_id=job.id,
                level="INFO",
                code="JOB_CREATED",
                message=f"Job «{label}» ایجاد شد",
                context={"params": params},
            )
        )
        job_id = job.id
    log.info("Job #%s ایجاد شد: %s", job_id, label)
    return job_id


def _auto_name(params: dict) -> str:
    scope_bits = []
    if params.get("provinces"):
        scope_bits.append("، ".join(params["provinces"]))
    if params.get("cities"):
        scope_bits.append("، ".join(params["cities"])[:80])
    if not scope_bits:
        scope_bits.append("سراسر کشور")
    cats = params.get("categories") or []
    cat_label = "، ".join(CategoryCode(c).label_fa for c in cats[:3]) if cats else "همه دسته‌ها"
    return f"کشف {cat_label} در {scope_bits[0]}"


async def run_job(job_id: int, *, database: Database | None = None, settings: Settings | None = None,
                  plan_only: bool = False) -> JobReport:
    settings = settings or get_settings()
    database = database or get_database(settings)
    runner = DiscoveryRunner(database, settings=settings)
    try:
        return await runner.run(job_id, plan_only=plan_only)
    finally:
        await runner.aclose()


def request_cancel(job_id: int, *, database: Database | None = None,
                   settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    database = database or get_database(settings)
    with database.session() as session:
        job = session.get(SearchJob, job_id)
        if job is None:
            return False
        job.cancel_requested = True
        session.add(JobEvent(job_id=job_id, level="WARNING", code="CANCEL_REQUESTED",
                             message="درخواست توقف Job ثبت شد"))
        return True


def mark_stale_jobs(database: Database | None = None, settings: Settings | None = None,
                    *, stale_minutes: int = 30) -> int:
    """Jobهایی که بی‌دلیل در حالت RUNNING مانده‌اند را علامت‌دار می‌کند."""
    from datetime import timedelta

    settings = settings or get_settings()
    database = database or get_database(settings)
    cutoff = utcnow() - timedelta(minutes=stale_minutes)
    count = 0
    with database.session() as session:
        jobs = session.query(SearchJob).filter(SearchJob.status == JobStatus.RUNNING.value).all()
        for job in jobs:
            heartbeat = job.heartbeat_at or job.started_at
            if heartbeat and heartbeat < cutoff:
                job.status = JobStatus.FAILED.value
                job.message = (job.message or "") + " | Job رهاشده تشخیص داده شد (بدون Heartbeat)"
                session.add(JobEvent(job_id=job.id, level="ERROR", code="JOB_STALE",
                                     message="Job بدون Heartbeat رها شده بود"))
                count += 1
    return count


def job_status(job_id: int, *, database: Database | None = None,
               settings: Settings | None = None) -> dict[str, Any] | None:
    settings = settings or get_settings()
    database = database or get_database(settings)
    with database.session() as session:
        job = session.get(SearchJob, job_id)
        if job is None:
            return None
        return {
            "id": job.id,
            "name": job.name,
            "status": job.status,
            "progress": job.progress,
            "target": job.target,
            "queries": {"total": job.total_queries, "done": job.done_queries},
            "businesses_new": job.businesses_new,
            "phones_new": job.phones_new,
            "duplicates": job.duplicates_found,
            "errors": job.errors,
            "message": job.message,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        }
