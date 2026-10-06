"""خدمات نگهداری: یکتاسازی، بازاعتبارسنجی، پاک‌سازی، بازمحاسبه امتیاز و گزارش‌ها."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select

from ..config import Settings, get_settings
from ..db.repositories import dashboard_stats, recent_events, recent_jobs
from ..db.session import Database, get_database
from ..domain.models import (
    Business,
    BusinessPhone,
    CrawlCache,
    DuplicateRecord,
    JobEvent,
    Phone,
    SearchQuery,
    SourceObservation,
)
from ..logging_setup import get_logger
from ..pipeline.dedup import Deduplicator
from ..pipeline.persist import Persister
from ..pipeline.validation import ValidationService

log = get_logger("service.maintenance")


def run_dedup(batch_size: int = 5000, *, database: Database | None = None,
              settings: Settings | None = None, review_only: bool = False) -> dict[str, Any]:
    settings = settings or get_settings()
    database = database or get_database(settings)
    with database.session() as session:
        report = Deduplicator(session, settings).run(batch_size=batch_size, review_only=review_only)
        if not review_only:
            # شمارش تکراری‌های علامت‌خورده در دفتر تکراری‌ها
            report.details = report.details[:50]
        return {**report.as_dict(), "details": report.details}


def revalidate_due(limit: int = 500, *, database: Database | None = None,
                   settings: Settings | None = None, force: bool = False) -> dict[str, Any]:
    settings = settings or get_settings()
    database = database or get_database(settings)
    with database.session() as session:
        service = ValidationService(session, settings)
        report = service.revalidate_due(limit=limit, force=force)
        return {**report.as_dict(), "changes": report.changes[:50]}


def due_revalidation_count(*, database: Database | None = None, settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    database = database or get_database(settings)
    with database.session() as session:
        return ValidationService(session, settings).due_count()


def recompute_scores(*, database: Database | None = None, settings: Settings | None = None,
                     limit: int | None = None) -> int:
    settings = settings or get_settings()
    database = database or get_database(settings)
    with database.session() as session:
        return Persister(session, settings).recompute_all(limit=limit)


@dataclass
class PurgeReport:
    businesses: int = 0
    phones: int = 0
    observations: int = 0
    links: int = 0

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def purge_synthetic(*, database: Database | None = None, settings: Settings | None = None) -> PurgeReport:
    """حذف کامل داده‌های آزمایشی (Fixture) — برای پاک‌سازی محیط تولید."""
    settings = settings or get_settings()
    database = database or get_database(settings)
    report = PurgeReport()
    with database.session() as session:
        synthetic_business_ids = [
            row.id for row in session.execute(select(Business).where(Business.is_synthetic.is_(True))).scalars()
        ]
        synthetic_phone_ids = [
            row.id for row in session.execute(select(Phone).where(Phone.is_synthetic.is_(True))).scalars()
        ]
        if synthetic_business_ids:
            report.observations += session.query(SourceObservation).filter(
                SourceObservation.business_id.in_(synthetic_business_ids)
            ).delete(synchronize_session=False)
            report.links += session.query(BusinessPhone).filter(
                BusinessPhone.business_id.in_(synthetic_business_ids)
            ).delete(synchronize_session=False)
            session.query(DuplicateRecord).filter(
                DuplicateRecord.canonical_id.in_(synthetic_business_ids)
            ).delete(synchronize_session=False)
            report.businesses = session.query(Business).filter(
                Business.id.in_(synthetic_business_ids)
            ).delete(synchronize_session=False)
        if synthetic_phone_ids:
            session.query(BusinessPhone).filter(
                BusinessPhone.phone_id.in_(synthetic_phone_ids)
            ).delete(synchronize_session=False)
            report.phones = session.query(Phone).filter(
                Phone.id.in_(synthetic_phone_ids)
            ).delete(synchronize_session=False)
        # شواهد باقی‌مانده از منابع آزمایشی
        report.observations += session.query(SourceObservation).filter(
            SourceObservation.is_synthetic.is_(True)
        ).delete(synchronize_session=False)
    log.info("پاک‌سازی داده آزمایشی: %s کسب‌وکار، %s شماره", report.businesses, report.phones)
    return report


def clear_http_cache(*, database: Database | None = None, settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    database = database or get_database(settings)
    with database.session() as session:
        return session.query(CrawlCache).delete(synchronize_session=False)


def reset_query_ledger(*, database: Database | None = None, settings: Settings | None = None) -> int:
    """پاک کردن دفتر پرس‌وجوها (اجرای کامل از نو). داده‌ها حذف نمی‌شوند."""
    settings = settings or get_settings()
    database = database or get_database(settings)
    with database.session() as session:
        session.query(JobEvent).filter(JobEvent.code == "PLAN_READY").delete(synchronize_session=False)
        return session.query(SearchQuery).delete(synchronize_session=False)


def full_stats(*, database: Database | None = None, settings: Settings | None = None,
               include_synthetic: bool = False) -> dict[str, Any]:
    settings = settings or get_settings()
    database = database or get_database(settings)
    with database.session() as session:
        stats = dashboard_stats(session, include_synthetic=include_synthetic)
        stats["jobs_recent"] = [
            {
                "id": j.id, "name": j.name, "status": j.status, "progress": j.progress,
                "businesses_new": j.businesses_new, "phones_new": j.phones_new,
                "errors": j.errors, "started_at": j.started_at.isoformat() if j.started_at else None,
                "finished_at": j.finished_at.isoformat() if j.finished_at else None,
                "message": j.message,
            }
            for j in recent_jobs(session, 10)
        ]
        stats["errors_recent"] = [
            {"id": e.id, "job_id": e.job_id, "code": e.code, "message": e.message,
             "at": e.created_at.isoformat()}
            for e in recent_events(session, 20, level="ERROR")
        ]
        stats["duplicates"]["records"] = int(session.query(DuplicateRecord).count())
        stats["evidence"]["distinct_urls"] = int(
            session.execute(select(func.count(func.distinct(SourceObservation.url)))).scalar() or 0
        )
        stats["revalidation"] = {"due": ValidationService(session, settings).due_count()}
    return stats
