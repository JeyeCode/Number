"""آزمون موتور شماره: نرمال‌سازی، پیش‌شماره، شماره خدماتی و تشخیص جعلی."""

from __future__ import annotations

import pytest

from numberbank.phones.normalize import parse_phone, phone_quality, resolve_local_number
from numberbank.phones.patterns import is_mobile_prefix_known, is_synthetic_number


@pytest.mark.parametrize(
    "raw,expected_e164,expected_type",
    [
        ("+982133334455", "+982133334455", "LANDLINE"),
        ("021-33334455", "+982133334455", "LANDLINE"),
        ("054-3720001", "+98543720001", "LANDLINE"),
        ("05138401234", "+985138401234", "LANDLINE"),
        ("09123456789", "+989123456789", "MOBILE"),
        ("۰۹۱۲۳۴۵۶۷۸۹", "+989123456789", "MOBILE"),
        ("+98 912 345 6789", "+989123456789", "MOBILE"),
    ],
)
def test_parse_known_numbers(raw: str, expected_e164: str, expected_type: str) -> None:
    parsed = parse_phone(raw)
    assert parsed is not None
    assert parsed.e164 == expected_e164
    assert parsed.type == expected_type
    assert parsed.valid_format is True


def test_quality_reflects_structure_not_source() -> None:
    mobile = parse_phone("09123456789")
    landline = parse_phone("+982133334455")
    assert phone_quality(mobile)[0] >= 0.8
    assert phone_quality(landline)[0] > phone_quality(mobile)[0]


def test_local_number_inference_accepts_seven_and_eight_digit_subscribers() -> None:
    seven = resolve_local_number("3720001", "054")
    eight = resolve_local_number("37200012", "054")
    assert seven is not None and eight is not None
    assert seven.e164 == "+98543720001"
    assert "استنباط" in (seven.reason or "")
    # پیش‌شماره ناشناخته هرگز حدس زده نمی‌شود
    assert resolve_local_number("3720001", "999") is None


def test_service_numbers_are_not_business_numbers() -> None:
    parsed = parse_phone("110")
    assert parsed is not None
    assert parsed.type == "SERVICE"
    assert phone_quality(parsed)[0] <= 0.05


@pytest.mark.parametrize("raw", ["1234567890123", "0000000000"])
def test_impossible_numbers_are_rejected(raw: str) -> None:
    parsed = parse_phone(raw)
    assert parsed is None or phone_quality(parsed)[0] < 0.5


def test_repeated_digits_penalized() -> None:
    parsed = parse_phone("1111111111")
    assert parsed is not None
    assert phone_quality(parsed)[0] < 0.5


def test_mobile_prefix_and_synthetic_blocks() -> None:
    assert is_mobile_prefix_known("09123456789") is True
    assert is_mobile_prefix_known("00123456789") is False
    assert is_synthetic_number("091200000101") is True
    assert is_synthetic_number("09121234567") is False
