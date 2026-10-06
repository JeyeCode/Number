"""نرمال‌سازی و اعتبارسنجی ساختاری شماره تلفن ایران (بدون هیچ حدس‌زدنی)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..text.normalize import to_ascii_digits
from .patterns import (
    AREA_CODE_TO_PROVINCE,
    FAKE_SUBSCRIBERS,
    MOBILE_RE,
    REPEATED_DIGIT_RE,
    SERVICE_NUMBERS,
    SERVICE_PREFIXES,
    SYNTHETIC_SUBSCRIBER_PREFIX,
    VALID_AREA_CODES,
    VALID_MOBILE_SECOND_DIGITS,
)

SEPARATORS_RE = re.compile(r"[\s\-\.\(\)\[\]\u200c\u00a0_]+")
DIGIT_RUN_RE = re.compile(r"[0-9]+")


@dataclass(frozen=True)
class PhoneParse:
    """نتیجه تجزیه یک شماره — همه فیلدها قطعی و قابل بازبینی هستند."""

    raw: str
    national: str  # 0XXXXXXXXXX
    e164: str  # +98XXXXXXXXXX
    type: str  # LANDLINE | MOBILE | SERVICE | UNKNOWN
    area_code: str | None = None
    subscriber: str | None = None
    formatted: str | None = None
    valid_format: bool = True
    reason: str | None = None
    fake_signals: tuple[str, ...] = field(default_factory=tuple)

    @property
    def digits(self) -> str:
        return self.national.lstrip("0")

    @property
    def is_mobile(self) -> bool:
        return self.type == "MOBILE"

    @property
    def province(self) -> str | None:
        return AREA_CODE_TO_PROVINCE.get(self.area_code or "")


def _clean(raw: str) -> str:
    cleaned = SEPARATORS_RE.sub("", to_ascii_digits(raw or "")).strip()
    return cleaned


# نگاشت ارقام انگلیسی/فارسی که ممکن است در نسخه‌های دیگر متن ظاهر شود
_KNOWN_SEQUENCES = {
    "0123456789", "1234567890", "9876543210", "0987654321",
    "1111111111", "2222222222", "3333333333", "4444444444", "5555555555",
    "6666666666", "7777777777", "8888888888", "9999999999", "0000000000",
}


def fake_signals_for(national: str) -> list[str]:
    """نشانه‌های **قطعی** جعلی‌بودن شماره (زمینه اعمال جریمه سنگین)."""
    signals: list[str] = []
    digits = national.lstrip("0")
    if not digits:
        return ["خالی"]
    if len(set(digits)) == 1 and len(digits) >= 7:
        signals.append("همه ارقام یکسان")
    elif len(set(digits)) <= 2 and len(digits) >= 8:
        signals.append("تنوع ارقام بسیار کم")
    if REPEATED_DIGIT_RE.search(digits):
        signals.append("تکرار یک رقم بیش از ۴ بار متوالی")
    if digits in _KNOWN_SEQUENCES or ("0" + digits) in _KNOWN_SEQUENCES:
        signals.append("دنباله متوالی کامل")
    sub = digits[1:] if digits.startswith("9") else (digits[2:] if len(digits) > 9 else digits)
    if sub in FAKE_SUBSCRIBERS or digits in FAKE_SUBSCRIBERS:
        signals.append("شماره نمونه/تکراری")
    if set(digits) == {"0"}:
        signals.append("تمام صفر")
    return signals


def soft_signals_for(national: str) -> list[str]:
    """نشانه‌های ضعیف (فقط اطلاعی/کاهش جزئی کیفیت، بدون رد کردن)."""
    signals: list[str] = []
    digits = national.lstrip("0")
    if not digits:
        return signals
    if digits.endswith("0000"):
        signals.append("چهار صفر انتهایی")
    if digits.endswith("00000"):
        signals.append("پنج صفر انتهایی")
    for seq in ("0123456789", "9876543210", "12345678", "87654321"):
        if seq in digits:
            signals.append("دنباله عددی طولانی در شماره")
            break
    if SYNT_PREFIX_CHECK(digits):
        signals.append("بلوک شماره مصنوعی (تست)")
    return signals


def SYNT_PREFIX_CHECK(digits: str) -> bool:  # noqa: N802 - سازگاری با استفاده در همان ماژول
    """شماره در بلوک مصنوعی تست است؟ (هفت رقم آخر با ۰۰۰ آغاز شود)"""
    return len(digits) >= 9 and digits[-7:].startswith(SYNTHETIC_SUBSCRIBER_PREFIX)


def parse_phone(raw: str) -> PhoneParse | None:
    """تجزیه شماره؛ در صورت عدم امکان، ``None`` برمی‌گرداند (هیچ حدسی زده نمی‌شود)."""
    cleaned = _clean(raw)
    if not cleaned:
        return None

    has_plus = cleaned.startswith("+") or "+" in (raw or "")
    cleaned = cleaned.lstrip("+")

    # حذف پیشوند کشور
    if cleaned.startswith("0098"):
        cleaned = cleaned[4:]
    elif cleaned.startswith("98") and (has_plus or len(cleaned) >= 12):
        cleaned = cleaned[2:]
    elif cleaned.startswith("0"):
        cleaned = cleaned[1:]

    # اکنون cleaned باید شماره ملی بدون صفر ابتدایی باشد
    if not cleaned.isdigit():
        return None

    # --- موبایل: 10 رقم و شروع با 9 ---
    if len(cleaned) == 10 and cleaned.startswith("9"):
        national = "0" + cleaned
        if not MOBILE_RE.match(national):
            return PhoneParse(
                raw=raw,
                national=national,
                e164="+98" + cleaned,
                type="MOBILE",
                subscriber=cleaned[1:],
                formatted=f"{national[:4]} {national[4:7]} {national[7:]}",
                valid_format=False,
                reason="پیشوند موبایل ناشناخته",
                fake_signals=tuple(fake_signals_for(national)),
            )
        known = national[2] in VALID_MOBILE_SECOND_DIGITS
        return PhoneParse(
            raw=raw,
            national=national,
            e164="+98" + cleaned,
            type="MOBILE",
            area_code=None,
            subscriber=cleaned[1:],
            formatted=f"{national[:4]} {national[4:7]} {national[7:]}",
            valid_format=known,
            reason=None if known else "پیشوند موبایل غیراستاندارد",
            fake_signals=tuple(fake_signals_for(national)),
        )

    # --- ثابت: 10 رقم = 0 + کد ۲ رقمی + ۸ رقم ---
    if len(cleaned) == 10:
        area = "0" + cleaned[:2]
        subscriber = cleaned[2:]
        if area in VALID_AREA_CODES:
            return PhoneParse(
                raw=raw,
                national="0" + cleaned,
                e164="+98" + cleaned,
                type="LANDLINE",
                area_code=area,
                subscriber=subscriber,
                formatted=f"{area} {subscriber[:4]} {subscriber[4:]}",
                valid_format=True,
                fake_signals=tuple(fake_signals_for("0" + cleaned)),
            )
        return PhoneParse(
            raw=raw,
            national="0" + cleaned,
            e164="+98" + cleaned,
            type="UNKNOWN",
            subscriber=cleaned[2:],
            valid_format=False,
            reason="کد پیش‌شماره نامعتبر",
            fake_signals=tuple(fake_signals_for("0" + cleaned)),
        )

    # --- ثابت با مشترک ۷ رقمی: 0 + کد ۲ رقمی + ۷ رقم (نگارش رایج در شهرهای کوچک) ---
    if len(cleaned) == 9:
        area = "0" + cleaned[:2]
        if area in VALID_AREA_CODES:
            return PhoneParse(
                raw=raw,
                national="0" + cleaned,
                e164="+98" + cleaned,
                type="LANDLINE",
                area_code=area,
                subscriber=cleaned[2:],
                formatted=f"{area} {cleaned[2:]}",
                valid_format=True,
                reason="طول مشترک ۷ رقم (نگارش غیراستاندارد اما رایج)",
                fake_signals=tuple(fake_signals_for("0" + cleaned)),
            )
        return PhoneParse(
            raw=raw,
            national="0" + cleaned,
            e164="+98" + cleaned,
            type="UNKNOWN",
            valid_format=False,
            reason="کد پیش‌شماره نامعتبر",
            fake_signals=tuple(fake_signals_for("0" + cleaned)),
        )

    # --- شماره محلی ۸ رقمی: نیازمند زمینه شهر (استنباطی) ---
    if len(cleaned) == 8:
        return PhoneParse(
            raw=raw,
            national="" ,
            e164="",
            type="LANDLINE",
            subscriber=cleaned,
            valid_format=False,
            reason="شماره محلی بدون پیش‌شماره (نیازمند زمینه شهر)",
        )

    # --- شماره کوتاه خدماتی ---
    if len(cleaned) <= 6 and cleaned in SERVICE_NUMBERS:
        return PhoneParse(
            raw=raw,
            national=cleaned,
            e164=cleaned,
            type="SERVICE",
            valid_format=False,
            reason="شماره خدماتی/عمومی",
        )

    return PhoneParse(
        raw=raw,
        national="0" + cleaned if len(cleaned) == 11 and not cleaned.startswith("0") else cleaned,
        e164="+98" + cleaned,
        type="UNKNOWN",
        valid_format=False,
        reason=f"طول نامعتبر ({len(cleaned)} رقم)",
    )


def resolve_local_number(local_digits: str, area_code: str | None) -> PhoneParse | None:
    """تبدیل شماره محلی با استفاده از پیش‌شماره شهر — با علامت‌گذاری صریح «استنباط‌شده».

    شماره‌های مشترک ایران در عمل ۷ یا ۸ رقم‌اند (نگارش ۷ رقمی رایج اما غیراستاندارد است)،
    بنابراین هر دو پذیرفته و در فیلد دلیل تصریح می‌شوند.
    """
    if not area_code or len(local_digits) not in (7, 8):
        return None
    area = area_code if area_code.startswith("0") else "0" + area_code
    if area not in VALID_AREA_CODES:
        return None
    national = area + local_digits
    return PhoneParse(
        raw=local_digits,
        national=national,
        e164="+98" + national[1:],
        type="LANDLINE",
        area_code=area,
        subscriber=local_digits,
        formatted=f"{area} {local_digits[:4]} {local_digits[4:]}",
        valid_format=True,
        reason=(
            "پیش‌شماره از شهر زمینه استنباط شده است"
            + ("" if len(local_digits) == 8 else " (مشترک ۷ رقمی)")
        ),
        fake_signals=tuple(fake_signals_for(national)),
    )


def is_service_number(national: str) -> bool:
    digits = national.lstrip("0")
    return digits in SERVICE_NUMBERS or any(digits.startswith(p) for p in SERVICE_PREFIXES)


def phone_quality(parse: PhoneParse | None, *, area_code_expected: str | None = None) -> tuple[float, list[str]]:
    """کیفیت ساختاری شماره در بازه ۰..۱ به‌همراه دلایل (قابل بازبینی)."""
    if parse is None:
        return 0.0, ["عدم امکان تجزیه"]
    notes: list[str] = []
    score = 1.0
    if not parse.valid_format:
        score -= 0.55
        notes.append(parse.reason or "قالب نامعتبر")
    if parse.fake_signals:
        score -= min(0.75, 0.35 * len(parse.fake_signals))
        notes.extend(parse.fake_signals)
    soft = soft_signals_for(parse.national)
    if soft:
        score -= min(0.25, 0.08 * len(soft))
        notes.extend(soft)
    if parse.type == "SERVICE":
        score -= 0.8
        notes.append("شماره خدماتی")
    if parse.type == "UNKNOWN":
        score -= 0.35
    if parse.area_code and area_code_expected and parse.area_code != area_code_expected:
        score -= 0.35
        notes.append("ناسازگاری پیش‌شماره با شهر")
    if parse.reason and "استنباط" in (parse.reason or ""):
        score -= 0.15
        notes.append(parse.reason)
    return max(0.0, min(1.0, score)), notes


def comparable_key(national: str) -> str:
    """کلید مقایسه شماره‌ها (۱۰ رقم آخر)."""
    digits = re.sub(r"\D", "", to_ascii_digits(national or ""))
    if digits.startswith("98") and len(digits) >= 12:
        digits = digits[2:]
    if digits.startswith("0"):
        digits = digits[1:]
    return digits[-10:] if len(digits) >= 10 else digits
