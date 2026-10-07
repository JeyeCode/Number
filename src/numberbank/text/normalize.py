"""نرمال‌سازی متن فارسی — پایه همه مقایسه‌ها، تطبیق‌ها و یکتاسازی‌ها."""

from __future__ import annotations

import html as html_lib
import re
import unicodedata

# --------------------------------------------------------------------------- #
# ارقام
# --------------------------------------------------------------------------- #
PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
_ASCII = "0123456789"
_DIGIT_MAP = {ord(p): a for p, a in zip(PERSIAN_DIGITS, _ASCII, strict=True)}
_DIGIT_MAP.update({ord(a): a2 for a, a2 in zip(ARABIC_DIGITS, _ASCII, strict=True)})

ZWNJ = "\u200c"
ZERO_WIDTH = "\u200b\u200c\u200d\u200e\u200f\ufeff"
ARABIC_DIACRITICS = "\u064b\u064c\u064d\u064e\u064f\u0650\u0651\u0652\u0653\u0654\u0655\u0670"

# حروف عربی → فارسی
CHAR_MAP = {
    "ي": "ی",
    "ى": "ی",
    "ﻯ": "ی",
    "ك": "ک",
    "ﻙ": "ک",
    "ۀ": "ه",
    "ة": "ه",
    "ؤ": "و",
    "إ": "ا",
    "أ": "ا",
    "ٱ": "ا",
    "ﻻ": "لا",
    "ئ": "ی",
}

# حذف صفات حقوقی در تشخیص شباهت نام
LEGAL_SUFFIXES = [
    "سهامی خاص",
    "سهامی عام",
    "با مسئولیت محدود",
    "مسئولیت محدود",
    "تعاونی",
    "خصوصی",
    "محدود",
    "شرکت",
    "موسسه",
    "مؤسسه",
    "بازرگانی",
    "تجارت",
    "گروه",
    "صنایع",
    "کارخانه",
]

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\u0600-\u06FF]+", re.UNICODE)


def to_ascii_digits(text: str) -> str:
    """تبدیل ارقام فارسی/عربی به ASCII — **طول رشته حفظ می‌شود** (لازم برای نگاشت موقعیت‌ها)."""
    return text.translate(_DIGIT_MAP)


def has_persian(text: str) -> bool:
    return bool(re.search(r"[\u0600-\u06FF]", text or ""))


def strip_html(raw: str) -> str:
    """حذف تگ‌ها/اسکریپت/استایل و تبدیل به متن ساده."""
    if not raw:
        return ""
    raw = re.sub(r"(?is)<(script|style|noscript|svg|template)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?is)<!--.*?-->", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?i)</(p|div|li|tr|h[1-6])>", "\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return html_lib.unescape(raw)


def collapse_ws(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").replace("\u00a0", " ")).strip()


def remove_zero_width(text: str) -> str:
    return "".join(ch for ch in text if ch not in ZERO_WIDTH)


def normalize_fa(text: str, *, zwnj: str = "keep", digits: bool = True, strip: bool = True) -> str:
    """نرمال‌سازی استاندارد فارسی.

    zwnj: keep | space | remove
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = "".join(CHAR_MAP.get(ch, ch) for ch in text)
    text = "".join(ch for ch in text if ch not in ARABIC_DIACRITICS)
    text = text.replace("\u0640", "")  # کشیده
    if zwnj == "space":
        text = text.replace(ZWNJ, " ")
    elif zwnj == "remove":
        text = text.replace(ZWNJ, "")
    else:
        text = remove_zero_width(text.replace(ZWNJ, "\x00")).replace("\x00", ZWNJ)
    if digits:
        text = to_ascii_digits(text)
    text = text.replace("\u00a0", " ")
    return collapse_ws(text) if strip else text


def name_key(text: str | None) -> str:
    """کلید تطبیق نام کسب‌وکار: بدون فاصله/نیم‌فاصله/نقطه، بدون صفات حقوقی."""
    if not text:
        return ""
    s = normalize_fa(text, zwnj="remove")
    s = to_ascii_digits(s).lower()
    s = _PUNCT_RE.sub("", s)
    for suffix in sorted(LEGAL_SUFFIXES, key=len, reverse=True):
        suffix_key = _PUNCT_RE.sub("", normalize_fa(suffix, zwnj="remove"))
        if s.endswith(suffix_key) and len(s) - len(suffix_key) >= 3:
            s = s[: -len(suffix_key)]
            break
    return s.strip()


def light_key(text: str | None) -> str:
    """کلید سبک (بدون حذف صفات حقوقی) برای جستجوی فازی محافظه‌کارانه."""
    if not text:
        return ""
    s = normalize_fa(text, zwnj="remove").lower()
    return _PUNCT_RE.sub("", s)


def search_text(text: str | None) -> str:
    """متن آماده جستجو (نیم‌فاصله → فاصله)."""
    return normalize_fa(text or "", zwnj="space")


def url_hash_key(url: str) -> str:
    from hashlib import sha1

    return sha1((url or "").strip().lower().encode("utf-8")).hexdigest()


def domain_of(url: str | None) -> str | None:
    """دامنه اصلی (بدون www) از URL."""
    if not url:
        return None
    from urllib.parse import urlparse

    raw = url.strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "http://" + raw.lstrip("/")
    try:
        host = urlparse(raw).hostname or ""
    except ValueError:
        return None
    host = host.lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host or None


def registrable_domain(url: str | None) -> str | None:
    """دامنه ثبت‌شده (تقریب سبک، بدون وابستگی به tldextract)."""
    host = domain_of(url)
    if not host:
        return None
    if re.fullmatch(r"[\d\.]+", host):
        return host
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    two_level = {"co", "com", "net", "org", "gov", "ac", "sch", "id", "or", "ne"}
    if parts[-2] in two_level and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def truncate(text: str | None, limit: int = 500) -> str | None:
    if text is None:
        return None
    text = text if len(text) <= limit else text[: limit - 1] + "…"
    return text


def digits_only(text: str | None) -> str:
    return re.sub(r"\D", "", to_ascii_digits(text or ""))
