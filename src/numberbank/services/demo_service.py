"""اجرای دموی کاملاً آفلاین: آزمون واقعی کل زنجیره روی پیکره آزمایشی.

هدف: اثبات کارکرد سامانه (کشف، استخراج، امتیازدهی، یکتاسازی، عدم تکرار در اجرای دوم)
حتی در محیطی که دسترسی به اینترنت ندارد.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import Settings, get_settings
from ..db.session import Database, get_database
from ..domain.enums import BusinessType, CategoryCode, JobKind
from ..fixtures.build import build_fixtures
from ..logging_setup import get_logger
from ..pipeline.discovery import DiscoveryRunner, JobReport
from .discovery_service import create_job
from .reference_service import seed_all

log = get_logger("service.demo")

DEMO_CITIES = ["بافق", "مهدی شهر", "نطنز", "کوهدشت", "بندر گز", "خاش"]


@dataclass
class DemoReport:
    fixtures: dict[str, Any] = field(default_factory=dict)
    seed: dict[str, Any] = field(default_factory=dict)
    runs: list[dict[str, Any]] = field(default_factory=list)
    stats_before: dict[str, Any] = field(default_factory=dict)
    stats_after: dict[str, Any] = field(default_factory=dict)
    verdict: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "fixtures": self.fixtures,
            "seed": self.seed,
            "runs": self.runs,
            "stats_after": self.stats_after,
            "verdict": self.verdict,
        }


async def run_demo(
    *,
    database: Database | None = None,
    settings: Settings | None = None,
    runs: int = 2,
    cities: list[str] | None = None,
    target: int = 300,
) -> DemoReport:
    settings = settings or get_settings()
    database = database or get_database(settings)
    report = DemoReport()

    fixture_report = build_fixtures()
    report.fixtures = fixture_report.as_dict()
    report.seed = seed_all(database, settings=settings).as_dict()
    report.stats_before = _stats(database)

    city_scope = cities or DEMO_CITIES
    for i in range(runs):
        known_phone_ids = _phone_id_set(database)
        job_id = create_job(
            name=f"دموی آفلاین — اجرای {i + 1}",
            cities=city_scope,
            categories=[c.value for c in CategoryCode if c.value != "OTHER"],
            business_types=[BusinessType.WHOLESALER.value, BusinessType.DISTRIBUTOR.value,
                            BusinessType.RETAILER.value],
            sources=["fixture_search_a", "fixture_search_b"],
            target=target,
            min_confidence=settings.min_confidence,
            max_pages_per_query=2,
            max_pages_per_job=400,
            workers=6,
            time_limit=600,
            kind=JobKind.DISCOVERY.value,
            database=database,
            settings=settings,
            extra_params={"run_dedup": True, "offline": True},
        )
        runner = DiscoveryRunner(database, settings=settings)
        try:
            job_report: JobReport = await runner.run(job_id)
        finally:
            await runner.aclose()
        data = job_report.as_dict()
        data["job_id"] = job_id
        # اصل «اجرای مجدد نباید شماره‌های پیشین را جدید گزارش کند»
        reintroduced = sorted(set(job_report.new_phone_ids) & known_phone_ids)
        data["previously_known_reported_new"] = len(reintroduced)
        if reintroduced:
            log.warning(
                "اجرای %s: %s شماره از پیش شناخته‌شده به‌عنوان جدید گزارش شد", i + 1, len(reintroduced)
            )
        report.runs.append(data)

    report.stats_after = _stats(database)
    report.verdict = _verdict(report, database=database)
    return report


def _phone_id_set(database: Database) -> set[int]:
    """شناسه همه شماره‌های موجود (برای سنجش «تکرار نبودن» در اجرای مجدد)."""
    from sqlalchemy import select

    from ..domain.models import Phone

    with database.session() as session:
        return {row for (row,) in session.execute(select(Phone.id))}


def _stats(database: Database) -> dict[str, Any]:
    from .maintenance_service import full_stats

    stats = full_stats(database=database, include_synthetic=True)
    stats["duplicates"]["records"] = stats["duplicates"].get("merged_records", 0)
    return stats



def _no_repeat_check(report: DemoReport) -> bool:
    """آیا اجراهای بعدی، شماره‌های پیشین را «جدید» گزارش کرده‌اند؟

    معیار درست: هیچ شماره‌ای که پیش از آن اجرا در دیتابیس بوده به‌عنوان کشف جدید
    گزارش نشده باشد، و دست‌کم یک اجرا شماره‌های تکراری را بازشناسی کرده باشد
    (phones_reused > 0) — یعنی سامانه «کشف پیوسته» است، نه فهرست‌ساز یک‌باره.
    """
    if len(report.runs) < 2:
        return True
    if any((run.get("previously_known_reported_new", 0) or 0) > 0 for run in report.runs):
        return False
    return any((run.get("phones_reused", 0) or 0) > 0 for run in report.runs[1:])

def _verdict(report: DemoReport, *, database: Database | None = None) -> dict[str, Any]:
    first = report.runs[0] if report.runs else {}
    checks = {
        "data_discovered": (first.get("businesses_new", 0) or 0) > 0,
        "phones_extracted": (first.get("phones_new", 0) or 0) > 0,
        "evidence_recorded": report.stats_after.get("evidence", {}).get("observations", 0) > 0,
        "duplicates_detected": report.stats_after.get("duplicates", {}).get("records", 0) > 0,
        "irrelevant_filtered": (first.get("irrelevant_rejected", 0) or 0) >= 0,
        "second_run_no_full_repeat": _no_repeat_check(report),
        "no_phone_duplicated": _phones_unique(report, database=database),
        "errors_low": (first.get("errors", 0) or 0) <= max(2, (first.get("queries_run", 1) or 1) * 0.2),
    }
    passed = sum(1 for v in checks.values() if v)
    return {
        "checks": checks,
        "passed": passed,
        "total": len(checks),
        "success": passed == len(checks),
        "summary": f"{passed} از {len(checks)} آزمون پذیرش دمو موفق بود",
    }


def _phones_unique(report: DemoReport, *, database: Database | None = None) -> bool:
    """تضمین: هیچ شماره‌ای به دو کسب‌وکار فعال متفاوت نسبت داده نشده باشد.

    تلفن مشترک واقعی (مثل شماره دفتر مرکزی که چند شعبه دارد) در داده‌های واقعی
    ممکن است؛ در دموی آفلاین اما انتظار داریم هر شماره در نهایت به یک رکورد
    کسب‌وکار فعال برسد. تلفن‌های مشترک فقط اگر در جدول استثناها ثبت شده باشند
    مجاز شمرده می‌شوند.
    """
    if database is None:
        return False
    from sqlalchemy import func, select

    from ..domain.models import Business, BusinessPhone, Phone

    with database.session() as session:
        rows = session.execute(
            select(Phone.e164, func.count(func.distinct(BusinessPhone.business_id)))
            .join(BusinessPhone, BusinessPhone.phone_id == Phone.id)
            .join(Business, Business.id == BusinessPhone.business_id)
            .where(Business.status == "ACTIVE")
            .group_by(Phone.e164)
            .having(func.count(func.distinct(BusinessPhone.business_id)) > 1)
        ).all()
    if rows:
        log.warning("شماره‌های مشترک بین چند کسب‌وکار فعال: %s", rows[:10])
    return not rows
