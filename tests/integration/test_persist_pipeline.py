"""آزمون یکپارچه لایه ذخیره‌سازی: شواهد، یکتایی شماره و عدم انتساب متقابل."""

from __future__ import annotations

from numberbank.extract.page import BusinessCandidate
from numberbank.phones.extract import find_phones

PAGE = (
    "<html><body><main class='listing'>"
    "<div class='biz-item'><h3 class='biz-name'>شرکت عمده فروشی مواد شوینده الف</h3>"
    "<p>تلفن: 054-3720001</p><p>همراه: 09121110001</p>"
    "<p>آدرس: خاش، خیابان بازار، پلاک 1</p></div>"
    "<div class='biz-item'><h3 class='biz-name'>شرکت عمده فروشی مواد شوینده ب</h3>"
    "<p>تلفن: 054-3720002</p><p>همراه: 09121110002</p>"
    "<p>آدرس: خاش، خیابان بازار، پلاک 2</p></div>"
    "</main></body></html>"
)


def _candidates():
    from numberbank.extract.page import extract_from_page

    return extract_from_page(
        PAGE,
        "https://directory.example.ir/خاش/detergent",
        query_context={"city": "خاش", "province": "سیستان و بلوچستان", "category": "DETERGENT"},
    )


def test_candidates_are_persisted_once_with_evidence(seeded) -> None:
    from numberbank.pipeline.persist import Persister

    with seeded.session() as session:
        persister = Persister(session, settings=seeded.settings)
        stats = persister.upsert_candidates(_candidates(), source_key="fixture_search_a")
        assert stats.businesses_new == 2
        assert stats.phones_new == 4
        assert stats.observations >= 4

    with seeded.session() as session:
        from sqlalchemy import func, select

        from numberbank.domain.models import Business, BusinessPhone, Phone, SourceObservation

        businesses = session.execute(select(func.count(Business.id))).scalar()
        phones = session.execute(select(func.count(Phone.id))).scalar()
        links = session.execute(select(func.count(BusinessPhone.id))).scalar()
        observations = session.execute(select(func.count(SourceObservation.id))).scalar()
        assert (businesses, phones, links) == (2, 4, 4)
        assert observations == 4

        # هر شماره فقط به کسب‌وکار خودش وصل است
        rows = session.execute(
            select(Phone.e164, Business.name)
            .join(BusinessPhone, BusinessPhone.phone_id == Phone.id)
            .join(Business, Business.id == BusinessPhone.business_id)
        ).all()
        mapping = {e164: name for e164, name in rows}
        assert mapping["+98543720001"].endswith("الف")
        assert mapping["+989121110002"].endswith("ب")


def test_repeat_persistence_is_idempotent(seeded) -> None:
    from numberbank.pipeline.persist import Persister

    candidates = _candidates()
    with seeded.session() as session:
        Persister(session, settings=seeded.settings).upsert_candidates(candidates, source_key="fixture_search_a")
    with seeded.session() as session:
        stats = Persister(session, settings=seeded.settings).upsert_candidates(
            candidates, source_key="fixture_search_a"
        )
    assert stats.businesses_new == 0
    assert stats.phones_new == 0
    assert stats.businesses_duplicate == 2
    assert stats.observations_duplicate >= 4
    assert stats.observations == 0

    with seeded.session() as session:
        from sqlalchemy import func, select

        from numberbank.domain.models import Business, Phone, SourceObservation

        assert session.execute(select(func.count(Business.id))).scalar() == 2
        assert session.execute(select(func.count(Phone.id))).scalar() == 4
        # شواهد تکراری دوباره ثبت نمی‌شوند (تحقیق «اجرای مجدد = نتیجه تکراری»)
        assert session.execute(select(func.count(SourceObservation.id))).scalar() == 4


def test_irrelevant_candidate_is_recorded_but_not_stored_as_business(seeded) -> None:
    from numberbank.pipeline.persist import Persister
    from numberbank.relevance.scorer import score_relevance

    text = "استخدام منشی خانم در شرکت بازرگانی"
    phones = find_phones("تلفن: 021-33334455")
    candidate = BusinessCandidate(
        name="استخدام منشی خانم",
        phones=phones,
        address="تهران",
        city="تهران",
        province="تهران",
        source_url="https://example.ir/jobs",
        relevance=score_relevance(name="استخدام منشی خانم", page_text=text),
    )
    with seeded.session() as session:
        stats = Persister(session, settings=seeded.settings).upsert_candidates(
            [candidate], source_key="fixture_search_a"
        )
    assert stats.rejected_irrelevant == 1
    with seeded.session() as session:
        from sqlalchemy import func, select

        from numberbank.domain.models import Business, Phone, SourceObservation

        assert session.execute(select(func.count(Business.id))).scalar() == 0
        assert session.execute(select(func.count(Phone.id))).scalar() == 0
        assert session.execute(select(func.count(SourceObservation.id))).scalar() == 1
