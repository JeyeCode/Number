"""آزمون فرهنگ جغرافیایی: تطبیق چندکلمه‌ای، نام‌های کوتاه و سازگاری استان/شهرستان."""

from __future__ import annotations

from numberbank.geo.gazetteer import get_gazetteer


def test_multiword_city_matches_spaced_and_unspaced_text() -> None:
    gz = get_gazetteer()
    assert gz.locate("بندر گز").city == "بندرگز"
    assert gz.locate("استان گلستان، شهر بندرگز").city == "بندرگز"
    assert gz.locate("مهدی شهر، سمنان").city in {"مهدی شهر", "مهدیشهر"}


def test_three_letter_city_is_matched_standalone() -> None:
    gz = get_gazetteer()
    match = gz.locate("خاش، بلوار معلم، پلاک 132")
    assert match.city == "خاش"
    assert match.province == "سیستان و بلوچستان"


def test_city_inside_longer_word_is_not_matched() -> None:
    gz = get_gazetteer()
    assert gz.locate("خاشک بیجار").city != "خاش"


def test_ambiguous_province_word_needs_context() -> None:
    gz = get_gazetteer()
    assert gz.locate("میدان مرکزی").province is None
    assert gz.locate("استان مرکزی، اراک").province == "مرکزی"


def test_province_county_city_are_consistent() -> None:
    gz = get_gazetteer()
    match = gz.locate("استان اصفهان — شهرستان نطنز")
    assert (match.city, match.county, match.province) == ("نطنز", "نطنز", "اصفهان")


def test_area_code_lookup_uses_province_prefix() -> None:
    gz = get_gazetteer()
    assert gz.area_code_for_city("نطنز", "اصفهان") == "031"
    assert gz.area_code_for_city("خاش", "سیستان و بلوچستان") == "054"


def test_reference_dataset_has_full_hierarchy() -> None:
    gz = get_gazetteer()
    assert gz.province_count >= 31
    assert gz.county_count >= 300
    assert gz.city_count >= 500
