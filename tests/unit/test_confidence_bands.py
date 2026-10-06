"""آزمون امتیازدهی اعتبار: باندها، هم‌راستایی منابع و جریمه داده جعلی."""

from __future__ import annotations

from numberbank.domain.enums import ConfidenceBand
from numberbank.scoring.confidence import ScoreInput, score_confidence


def test_bands_are_monotonic_with_evidence() -> None:
    # توجه: دروازه ارتباط (relevance) عمداً امتیاز را سقف می‌زند؛ اینجا فرض می‌کنیم
    # کسب‌وکار مرتبط شناسایی شده است تا صرفاً رفتار هم‌راستایی شواهد سنجیده شود.
    weak = score_confidence(ScoreInput(source_reliabilities=[0.4], phone_quality=0.4))
    strong = score_confidence(
        ScoreInput(
            relevance_score=70,
            source_reliabilities=[0.9, 0.8, 0.7],
            independent_domains=3,
            observation_count=6,
            has_name=True,
            has_address=True,
            has_website=True,
            location_known=True,
            area_code_matches_location=True,
            phone_quality=1.0,
            phone_type="LANDLINE",
        )
    )
    assert strong.total > weak.total
    assert strong.band in (ConfidenceBand.HIGH, ConfidenceBand.VERY_HIGH)
    assert weak.band in (ConfidenceBand.LOW, ConfidenceBand.INVALID)


def test_fake_signals_cap_the_score() -> None:
    result = score_confidence(
        ScoreInput(
            relevance_score=70,
            source_reliabilities=[0.9],
            has_name=True,
            has_address=True,
            location_known=True,
            phone_quality=0.1,
            fake_signals=["دنباله تکراری"],
        )
    )
    assert result.total <= 45
    assert any(p["code"] == "FAKE_PATTERN" for p in result.penalties)


def test_breakdown_components_are_reported() -> None:
    result = score_confidence(
        ScoreInput(relevance_score=70, source_reliabilities=[0.8], has_name=True, has_address=True,
                   location_known=True, phone_quality=0.9, phone_type="MOBILE",
                   observation_count=2, independent_domains=2)
    )
    assert set(result.components) >= {"source", "corroboration", "fields", "phone"}
    assert result.reasons
