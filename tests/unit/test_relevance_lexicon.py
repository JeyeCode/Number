"""آزمون ارتباط‌سنجی و واژگان دسته‌بندی/نقش (بدون وابستگی به شبکه)."""

from __future__ import annotations

import pytest

from numberbank.relevance.scorer import score_relevance


@pytest.mark.parametrize(
    "text,expected_category,expected_type",
    [
        ("شرکت پخش مواد شوینده و بهداشتی البرز", "DETERGENT", "DISTRIBUTOR"),
        ("پخش لوازم آرایشی زاهدان", "COSMETIC", "DISTRIBUTOR"),
        ("فروشگاه دستمال کاغذی سلولزی", "CELLULOSE", "RETAILER"),
        ("عمده فروشی مواد شوینده بهرامی", "DETERGENT", "WHOLESALER"),
        ("بازرگانی عمده فروشی محصولات بهداشتی شریفی", "HYGIENE", "WHOLESALER"),
    ],
)
def test_relevant_businesses_scored(text: str, expected_category: str, expected_type: str) -> None:
    result = score_relevance(name=text, page_text=text)
    assert result.score >= 30
    assert result.primary_category == expected_category, result.reasons
    assert result.business_type == expected_type


@pytest.mark.parametrize(
    "text",
    [
        "استخدام منشی خانم در شرکت",
        "پخش زنده فوتبال امشب",
        "خرید بلیط قطار",
        "آموزش نصب ویندوز",
    ],
)
def test_irrelevant_texts_rejected(text: str) -> None:
    result = score_relevance(name=text, page_text=text)
    assert result.score < 30


def test_category_lexicon_extends_from_reference_file(tmp_path) -> None:
    """واژگان JSON باید *افزوده* شود، نه جایگزین واژگان پیش‌فرض."""
    from numberbank.relevance import lexicon

    default_terms = lexicon.category_terms("DETERGENT")
    assert default_terms, "واژگان پیش‌فرض نباید خالی باشد"
    assert any(term == "شوینده" for term, _ in default_terms)
