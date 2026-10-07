"""آزمون محافظ‌های یکتاسازی: کسب‌وکارهای هم‌ساختار نباید ادغام شوند."""

from __future__ import annotations

from numberbank.dedup.similarity import BusinessView, compare, distinctive_tokens


def test_distinctive_tokens_drop_structural_words() -> None:
    tokens = distinctive_tokens("شرکت عمده فروشی مواد شوینده بهرامی")
    assert "بهرامی" in tokens
    assert "شرکت" not in tokens and "عمده" not in tokens and "شوینده" not in tokens


def test_same_structure_different_brand_is_not_merged() -> None:
    a = BusinessView(name="شرکت عمده فروشی محصولات بهداشتی حسینی", city_name="نطنز")
    b = BusinessView(name="شرکت عمده فروشی محصولات بهداشتی رحیمی", city_name="نطنز")
    result = compare(a, b)
    assert result.score < 0.62, f"ادغام نادرست: {result.score} / {result.signals}"


def test_exact_duplicate_is_merged() -> None:
    a = BusinessView(name="شرکت پخش مواد شوینده رحیمی", city_name="بافق")
    b = BusinessView(name="شرکت پخش مواد شوینده رحیمی", city_name="بافق")
    assert compare(a, b).score >= 0.80


def test_spelling_variant_with_same_brand_is_merged() -> None:
    a = BusinessView(name="فروشگاه مواد شوینده کاظمی", city_name="خاش")
    b = BusinessView(name="فروشگاه مواد شوينده کاظمي", city_name="خاش")
    assert compare(a, b).score >= 0.80


def test_shared_phone_alone_is_not_enough_without_name_agreement() -> None:
    a = BusinessView(name="شرکت پخش مواد شوینده الف", city_name="خاش", phones=["+985400000001"])
    b = BusinessView(name="بازرگانی لوازم آرایشی ب", city_name="خاش", phones=["+985400000001"])
    result = compare(a, b)
    assert result.score < 0.80, result.signals


def test_kindred_variant_is_flagged_for_review() -> None:
    a = BusinessView(name="بازرگانی پخش لوازم آرایشی و بهداشتی اکبری", city_name="نطنز")
    b = BusinessView(name="پخش لوازم آرایشی اکبری", city_name="نطنز")
    score = compare(a, b).score
    assert 0.62 <= score < 0.80, score
