"""آزمون یکپارچه یکتاسازی: ادغام واقعی، حفظ منابع و پرهیز از ادغام نادرست."""

from __future__ import annotations


def _make_business(session, *, name, city, province, phone_e164, domain=None, source_key="fixture_search_a"):
    from numberbank.dedup.fingerprint import business_fingerprint
    from numberbank.domain.models import Business, BusinessPhone, Phone, Source, SourceObservation
    from numberbank.text.normalize import name_key

    source = session.query(Source).filter_by(key=source_key).one_or_none()
    if source is None:
        source = Source(key=source_key, name=source_key, kind="BUSINESS_DIRECTORY", reliability=0.7)
        session.add(source)
        session.flush()

    phone = session.query(Phone).filter_by(e164=phone_e164).one_or_none()
    if phone is None:
        phone = Phone(e164=phone_e164, national="0" + phone_e164.lstrip("+98"), digits=phone_e164[-10:],
                      type="LANDLINE", status="VERIFIED", confidence=80, confidence_band="HIGH")
        session.add(phone)
        session.flush()

    business = Business(
        fingerprint=business_fingerprint(name=name, city_name=city, domain=domain, phone_e164=phone_e164),
        name=name, name_key=name_key(name), city_name=city, province_name=province,
        domain=domain, website=f"https://{domain}" if domain else None,
        status="ACTIVE", confidence=70, confidence_band="HIGH", source_count=1,
    )
    session.add(business)
    session.flush()
    session.add(BusinessPhone(business_id=business.id, phone_id=phone.id, is_primary=True))
    session.add(SourceObservation(
        business_id=business.id, phone_id=phone.id, source_id=source.id,
        evidence_kind="DIRECTORY_ENTRY", url=f"https://{domain or 'dir.example.ir'}/{business.id}",
        url_hash=f"hash-{business.id}", dedupe_hash=f"dedupe-{business.id}", status="ACCEPTED",
    ))
    session.flush()
    return business


def test_duplicate_records_are_merged_and_all_sources_kept(seeded) -> None:
    """دو رکورد از دو منبع مستقل که یک کسب‌وکار واحدند باید ادغام شوند.

    در عمل دو منبع، نام/شهر/دامنه را با تفاوت‌های جزئی ثبت می‌کنند؛ اینجا رکورد دوم
    فقط دامنه و نشانی را دارد (مثلاً از صفحه سایت شرکت) تا اثری متفاوت بگیرد.
    """
    from sqlalchemy import func, select

    from numberbank.domain.models import Business, DuplicateRecord, SourceObservation
    from numberbank.pipeline.dedup import Deduplicator

    with seeded.session() as session:
        _make_business(session, name="شرکت پخش مواد شوینده رحیمی", city="بافق",
                       province="یزد", phone_e164="+989120000101", domain="detergent-a.example.ir")
        _make_business(session, name="شرکت پخش مواد شوینده رحیمی بافق", city="خاش",
                       province="سیستان و بلوچستان", phone_e164="+989120000101",
                       domain="detergent-a.example.ir")
        report = Deduplicator(session, seeded.settings).run(batch_size=1000)
        assert report.merged == 1

    with seeded.session() as session:
        statuses = sorted(b.status for b in session.execute(select(Business)).scalars())
        # رکورد کانونیکال هرگز MERGED نمی‌شود؛ اگر شواهدش کم باشد به قرنطینه می‌رود
        assert statuses[0] == "MERGED"
        assert statuses[1] in {"ACTIVE", "QUARANTINED"}
        assert session.execute(select(func.count(DuplicateRecord.id))).scalar() == 1
        # شواهد هر دو منبع روی رکورد کانونیکال باقی می‌ماند (هیچ داده‌ای حذف نمی‌شود)
        canonical_id = session.execute(
            select(Business.id).where(Business.status != "MERGED")
        ).scalar_one()
        assert session.execute(
            select(func.count(SourceObservation.id)).where(SourceObservation.business_id == canonical_id)
        ).scalar() == 2


def test_similarly_named_but_distinct_businesses_are_not_merged(seeded) -> None:
    from sqlalchemy import select

    from numberbank.domain.models import Business
    from numberbank.pipeline.dedup import Deduplicator

    with seeded.session() as session:
        _make_business(session, name="شرکت عمده فروشی محصولات بهداشتی حسینی", city="نطنز",
                       province="اصفهان", phone_e164="+989120000201")
        _make_business(session, name="شرکت عمده فروشی محصولات بهداشتی رحیمی", city="نطنز",
                       province="اصفهان", phone_e164="+989120000202")
        _make_business(session, name="بازرگانی پخش لوازم آرایشی و بهداشتی اکبری", city="نطنز",
                       province="اصفهان", phone_e164="+989120000203")
        report = Deduplicator(session, seeded.settings).run(batch_size=1000)
        assert report.merged == 0

    with seeded.session() as session:
        assert sorted(b.status for b in session.execute(select(Business)).scalars()) == ["ACTIVE"] * 3
