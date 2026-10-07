"""آزمون استخراج: صفحه دایرکتوری باید چند کاندید جدا بسازد (نه یک بلوک غول‌آسا)."""

from __future__ import annotations

from numberbank.extract.page import extract_from_page
from numberbank.phones.extract import find_phones_in_html

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


def test_each_directory_entry_becomes_its_own_candidate() -> None:
    candidates = extract_from_page(
        PAGE,
        "https://directory.example.ir/خاش/detergent",
        query_context={"city": "خاش", "province": "سیستان و بلوچستان", "category": "DETERGENT"},
    )
    assert len(candidates) == 2, [c.name for c in candidates]
    by_phone = {c.phones[0].e164: c for c in candidates}
    assert set(by_phone) == {"+98543720001", "+98543720002"}
    for candidate in candidates:
        phones = {p.e164 for p in candidate.phones}
        assert len(phones) == 2, phones  # ثابت + همراه، همه از همان کسب‌وکار
        assert candidate.evidence_kind == "DIRECTORY_ENTRY"
    # هیچ شماره‌ای نباید به کسب‌وکار دیگر سرایت کند
    assert {p.e164 for p in by_phone["+98543720001"].phones} == {"+98543720001", "+989121110001"}
    assert {p.e164 for p in by_phone["+98543720002"].phones} == {"+98543720002", "+989121110002"}


def test_adjacent_numbers_are_not_merged_into_one_token() -> None:
    phones = {p.e164 for p in find_phones_in_html("<p>09120000000 09120000001</p>")}
    assert phones == {"+989120000000", "+989120000001"}


def test_formatted_multi_group_number_stays_single() -> None:
    phones = [p.e164 for p in find_phones_in_html("<p>تلفن: 021 3333 4455</p>")]
    assert phones == ["+982133334455"]


def test_persian_digits_are_extracted() -> None:
    phones = {p.e164 for p in find_phones_in_html("<p>همراه: ۰۹۱۲۳۴۵۶۷۸۹</p>")}
    assert phones == {"+989123456789"}


def test_postcode_like_number_is_not_treated_as_phone() -> None:
    phones = {p.e164 for p in find_phones_in_html("<p>کد پستی: 1234567890 تلفن: ۰۲۱-۳۳۳۳۴۴۵۵</p>")}
    assert phones == {"+982133334455"}


def test_spam_page_is_flagged() -> None:
    spam = "<html><body><h1>لیست شماره تلفن‌های خوش‌شانس</h1><p>"
    spam += " ".join(f"0912000{i:04d}" for i in range(40))
    spam += "</p></body></html>" * 1
    candidates = extract_from_page(spam, "https://spam.example.ir/list")
    assert candidates, "صفحه اسپم باید برای بازبینی ثبت شود"
    assert any(c.is_spam for c in candidates)
