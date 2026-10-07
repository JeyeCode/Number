"""آزمون پذیرش سرتاسری: اجرای واقعی دموی آفلاین و بررسی معیارهای اجباری.

این آزمون «نتیجه واقعی» تولید می‌کند (نه mock): دو اجرای کامل روی پیکره آزمایشی،
سپس بررسی می‌شود که داده کشف شده، شواهد ثبت شده، تکراری‌ها ادغام شده، متن‌های
نامرتبط رد شده و اجرای دوم شماره‌های پیشین را «جدید» گزارش نکرده باشد.
"""

from __future__ import annotations

import pytest

from numberbank.services.demo_service import run_demo


@pytest.mark.slow
def test_offline_demo_meets_all_acceptance_criteria(seeded) -> None:
    import asyncio

    report = asyncio.run(
        run_demo(database=seeded, settings=seeded.settings, runs=2,
                 cities=["بافق", "خاش"], target=60)
    )
    verdict = report.verdict

    # هر معیار جداگانه گزارش می‌شود تا خطا قابل تشخیص باشد
    for name, ok in verdict["checks"].items():
        assert ok, (
            f"معیار «{name}» برآورده نشد | "
            f"runs={[{k: r.get(k) for k in ('businesses_new', 'phones_new', 'errors')} for r in report.runs]} | "
            f"stats={ {k: report.stats_after.get(k) for k in ('businesses', 'phones', 'duplicates')} }"
        )
    assert verdict["success"] is True, verdict

    first, second = report.runs[0], report.runs[1]
    assert first["businesses_new"] > 0
    assert first["phones_new"] > 0
    assert first["errors"] == 0
    # اجرای دوم ممکن است صفحه‌های بررسی‌نشده تازه‌ای بیابد (کشف پیوسته)، اما هرگز
    # نباید شماره‌ای را که پیش‌تر در دیتابیس بوده «جدید» گزارش کند.
    assert second["previously_known_reported_new"] == 0
    assert first["previously_known_reported_new"] == 0
    assert second["phones_reused"] > 0, "اجرای دوم باید شماره‌های تکراری را بازشناسد"

    stats = report.stats_after
    assert stats["businesses"]["active"] > 0
    assert stats["phones"]["total"] > 0
    assert stats["evidence"]["observations"] > 0
    assert stats["duplicates"]["merged_records"] > 0
    assert stats["locations"]["cities_with_data"] >= 1


@pytest.mark.slow
def test_second_run_does_not_create_duplicate_phone_rows(seeded) -> None:
    import asyncio

    from sqlalchemy import func, select

    from numberbank.domain.models import Business, BusinessPhone, Phone, SourceObservation

    asyncio.run(run_demo(database=seeded, settings=seeded.settings, runs=2,
                         cities=["بافق"], target=40))

    with seeded.session() as session:
        duplicates = session.execute(
            select(Phone.e164, func.count(Phone.id)).group_by(Phone.e164).having(func.count(Phone.id) > 1)
        ).all()
        assert duplicates == []

        # هر شماره به یک کسب‌وکار فعال وصل است (تلفن مشترک غیرواقعی ممنوع)
        shared = session.execute(
            select(Phone.e164, func.count(func.distinct(BusinessPhone.business_id)))
            .join(BusinessPhone, BusinessPhone.phone_id == Phone.id)
            .join(Business, Business.id == BusinessPhone.business_id)
            .where(Business.status == "ACTIVE")
            .group_by(Phone.e164)
            .having(func.count(func.distinct(BusinessPhone.business_id)) > 1)
        ).all()
        assert shared == []

        # هر رکورد کسب‌وکار دست‌کم یک شاهد دارد (اصل «بدون شاهد، بدون رکورد»)
        businesses_without_evidence = session.execute(
            select(func.count(Business.id)).where(
                ~Business.id.in_(select(SourceObservation.business_id))
            )
        ).scalar()
        assert businesses_without_evidence == 0
