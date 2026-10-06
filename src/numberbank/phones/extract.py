"""استخراج شماره‌های تلفن از متن و HTML، همراه با زمینه و شواهد."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse

from ..text.normalize import collapse_ws, strip_html, to_ascii_digits
from .normalize import (
    PhoneParse,
    parse_phone,
    phone_quality,
    resolve_local_number,
)

# توکن «شماره‌مانند»: دست‌کم ۷ رقم، با جداکننده‌های رایج
NUMBER_TOKEN_RE = re.compile(r"[+0-9][0-9\s\-\u200c\.\(\)]{5,22}[0-9]")
TEL_LINK_RE = re.compile(r"""(?is)href\s*=\s*["']\s*(tel:[^"']+)["']""")
WHATSAPP_RE = re.compile(r"""(?is)(?:wa\.me/|api\.whatsapp\.com/send\?phone=|whatsapp://send\?phone=)(\+?[0-9]{8,15})""")
MOBILE_HINT_RE = re.compile(r"(همراه|موبایل|همراه:\s*|واتساپ|واتس\s*آپ|whatsapp|mobile)", re.IGNORECASE)
LANDLINE_HINT_RE = re.compile(r"(تلفن|تلفن\s*ثابت|دفتر|فکس|نمابر|tel|phone)", re.IGNORECASE)

ROLE_TERMS_RE = re.compile(
    r"(عمده\s*فروشی|عمده\s*فروش|خرده\s*فروشی|خرده\s*فروش|پخش|توزیع|فروش|نمایندگی|"
    r"کارخانه|تولید|بازرگانی|واردات|صادرات|انبار|فروشگاه|شرکت|مدیرعامل|مدیر\s*فروش)",
    re.IGNORECASE,
)

# برچسب‌هایی که نشان می‌دهند عدد بعدی «شماره تلفن» نیست
NON_PHONE_LABEL_RE = re.compile(
    r"(کد\s*ملی|کدملی|کد\s*پستی|کدپستی|شماره\s*کارت|شماره\s*حساب|شبا|شناسه\s*ملی|"
    r"سریال|شماره\s*سند|شماره\s*فاکتور|شماره\s*پلاک|پلاک\s*خودرو|کد\s*اقتصادی|"
    r"کد\s*رهگیری|بارکد|کد\s*کالا|شماره\s*قرارداد|مبلغ|قیمت|تومان|ریال|درصد|"
    r"سال\s*|تاریخ|کد\s*شناسه|کد\s*بورس|شماره\s*چک|شماره\s*بارنامه)",
    re.IGNORECASE,
)
PHONE_LABEL_RE = re.compile(
    r"(تلفن|تلفن\s*ثابت|تلفن\s*همراه|همراه|موبایل|دفتر|فکس|نمابر|tel|phone|mobile|واتساپ|"
    r"whatsapp|تماس|ارتباط|پیش‌شماره|۰۲۱|شعبه)",
    re.IGNORECASE,
)


@dataclass
class RejectedNumber:
    """عددی که به‌عنوان شماره تلفن رد شد — برای شفافیت و آمار."""

    raw: str
    reason: str
    snippet: str = ""


@dataclass
class PhoneCandidate:
    """کاندید شماره با زمینه — ورودی مرحله امتیازدهی."""

    raw: str
    parse: PhoneParse
    start: int = 0
    end: int = 0
    snippet: str = ""
    kind: str = "text"  # text | tel_link | whatsapp
    nearby_role_terms: list[str] = field(default_factory=list)
    context_hint: str | None = None
    quality: float = 0.5
    quality_notes: list[str] = field(default_factory=list)
    inferred: bool = False

    @property
    def e164(self) -> str:
        return self.parse.e164

    @property
    def national(self) -> str:
        return self.parse.national

    @property
    def is_synthetic(self) -> bool:
        return any("مصنوعی" in n for n in self.quality_notes)


def snippet_around(text: str, start: int, end: int, window: int = 90) -> str:
    left = max(0, start - window)
    right = min(len(text), end + window)
    return collapse_ws(text[left:right])


_DIGIT_ONLY_RE = re.compile(r"\D")


def _split_token_region(raw: str, base_offset: int) -> list[tuple[str, int, int]]:
    """تقسیم یک ناحیه متنی به شماره‌های مستقل.

    الگوی توکن‌یابی عمداً «سخاوتمند» است تا شماره‌های قالب‌دار با فاصله/خط تیره
    (مثل «۰۲۱ ۳۳۳۳ ۴۴۵۵») را کامل بگیرد؛ اما همین سخاوت باعث می‌شد چند شماره
    پشت‌سرهم در یک توکن بیایند («09120000000 09120000001»). این تابع بسته به
    تعداد ارقام، ناحیه را به شماره‌های جداگانه می‌شکند:

    * گروه‌هایی که جمع ارقامشان کمتر از ۸ است ادامه یک شماره‌اند.
    * وقتی جمع ارقام به ۸ رسید و گروه بعدی خودش یک شماره کامل (≥۸ رقم) است،
      شماره فعلی بسته و شماره جدید آغاز می‌شود.
    """
    parts = [p for p in re.split(r"([\s\u200c]+)", raw) if p != ""]
    pieces: list[tuple[str, int]] = []
    cursor = base_offset
    for part in parts:
        if part.strip():
            pieces.append((part, cursor))
        cursor += len(part)
    if len(pieces) <= 1:
        return [(raw, base_offset, base_offset + len(raw))]

    groups: list[list[tuple[str, int]]] = [[]]
    for piece, offset in pieces:
        digits = len(_DIGIT_ONLY_RE.sub("", piece))
        pending = sum(len(_DIGIT_ONLY_RE.sub("", p)) for p, _ in groups[-1])
        if groups[-1] and pending >= 8 and digits >= 8:
            groups.append([])
        groups[-1].append((piece, offset))

    out: list[tuple[str, int, int]] = []
    for group in groups:
        if not group:
            continue
        text_value = " ".join(p for p, _ in group)
        start = group[0][1]
        out.append((text_value, start, group[-1][1] + len(group[-1][0])))
    return out


def _iter_number_tokens(text: str):
    """پیمایش توکن‌های شماره‌مانند روی متنی که ارقام آن ASCII شده است."""
    ascii_text = to_ascii_digits(text)
    for m in NUMBER_TOKEN_RE.finditer(ascii_text):
        raw = m.group(0)
        if "\n" in raw or "\r" in raw:
            # شماره‌های چندخطی معمولاً چند عدد جداگانه‌اند
            for piece in re.split(r"[\r\n]+", raw):
                if piece.strip():
                    yield piece, m.start(), m.start() + len(piece)
            continue
        for piece, start, end in _split_token_region(raw, m.start()):
            yield piece, start, end


def _context_hint(text: str, start: int, end: int) -> str | None:
    window = text[max(0, start - 60) : min(len(text), end + 30)]
    if MOBILE_HINT_RE.search(window):
        return "mobile"
    if LANDLINE_HINT_RE.search(window):
        return "landline"
    return None


def _looks_bare_international_or_local(raw: str) -> tuple[bool, bool]:
    """آیا عدد با صفر/کد کشور نوشته شده و آیا جداکننده دارد؟"""
    cleaned_raw = to_ascii_digits(raw).strip()
    has_prefix = cleaned_raw.startswith("0") or cleaned_raw.startswith("+") or cleaned_raw.startswith("0098")
    has_separator = bool(re.search(r"[\s\-\.\(\)\u200c]", cleaned_raw))
    return has_prefix, has_separator


def find_phones(
    text: str,
    *,
    default_area_code: str | None = None,
    allow_local: bool = True,
    max_results: int = 200,
    collect_rejects: list[RejectedNumber] | None = None,
) -> list[PhoneCandidate]:
    """استخراج کاندیدهای شماره از متن ساده با رد کردن اعداد نامرتبط."""
    if not text:
        return []
    out: list[PhoneCandidate] = []
    seen: set[str] = set()

    def reject(raw: str, reason: str, start: int, end: int) -> None:
        if collect_rejects is not None:
            collect_rejects.append(RejectedNumber(raw=raw.strip(), reason=reason,
                                                   snippet=snippet_around(text, start, end, 40)))

    for raw, start, end in _iter_number_tokens(text):
        # ۱) رد بر اساس برچسب‌های غیرتلفنی مجاور (کد ملی/پستی/کارت/مبلغ/...)
        #    پنجره برچسب فقط تا «آخرین عدد قبلی» بررسی می‌شود تا برچسب عدد دیگر به این عدد نسبت داده نشود.
        label_window = text[max(0, start - 32) : start]
        last_digit = max((i for i, ch in enumerate(label_window) if ch.isdigit() or ch in "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩"), default=-1)
        label_window = label_window[last_digit + 1 :]
        if NON_PHONE_LABEL_RE.search(label_window):
            reject(raw, "برچسب غیرتلفنی (کد ملی/پستی/کارت/مبلغ)", start, end)
            continue

        digits_only = re.sub(r"\D", "", to_ascii_digits(raw))
        if len(digits_only) > 14:
            reject(raw, "طول بیش از حد (احتمالاً چند عدد چسبیده)", start, end)
            continue

        parse = parse_phone(raw)
        inferred = False
        if parse is None:
            reject(raw, "غیرقابل تجزیه", start, end)
            continue

        need_local = (not parse.valid_format) and "محلی" in (parse.reason or "")
        if need_local:
            if not (allow_local and default_area_code):
                reject(raw, "شماره محلی بدون زمینه شهر", start, end)
                continue
            resolved = resolve_local_number(re.sub(r"\D", "", raw)[-8:], default_area_code)
            if resolved is None:
                reject(raw, "عدم امکان تکمیل پیش‌شماره", start, end)
                continue
            parse = resolved
            inferred = True
        elif not parse.valid_format:
            reject(raw, parse.reason or "قالب نامعتبر", start, end)
            continue

        if not parse.e164 or parse.type == "SERVICE":
            reject(raw, "شماره خدماتی/عمومی", start, end)
            continue

        # ۲) حفاظت از خطای «کد پستی/شناسه» ای که ساختار شماره ثابت دارد:
        #    عدد ۱۰ رقمی بدون صفر ابتدایی، بدون جداکننده و بدون زمینه تلفنی
        if not inferred:
            has_prefix, has_separator = _looks_bare_international_or_local(raw)
            if (
                parse.type == "LANDLINE"
                and not has_prefix
                and not has_separator
                and len(digits_only) == 10
                and not PHONE_LABEL_RE.search(label_window)
            ):
                reject(raw, "عدد ۱۰ رقمی بدون صفر ابتدایی و بدون زمینه تلفنی", start, end)
                continue

        key = parse.e164
        if key in seen:
            continue
        seen.add(key)

        hint = _context_hint(text, start, end)
        kind = "text"
        if parse.type == "MOBILE" and hint == "mobile":
            kind = "mobile_hinted"
        quality, notes = phone_quality(parse)
        if inferred:
            quality = min(quality, 0.6)
        snippet = snippet_around(text, start, end)
        roles = sorted({collapse_ws(m.group(0)) for m in ROLE_TERMS_RE.finditer(snippet)})
        out.append(
            PhoneCandidate(
                raw=raw.strip(),
                parse=parse,
                start=start,
                end=end,
                snippet=snippet,
                kind=kind,
                nearby_role_terms=roles[:6],
                context_hint=hint,
                quality=quality,
                quality_notes=notes,
                inferred=inferred,
            )
        )
        if len(out) >= max_results:
            break
    return out


def find_phones_in_html(html: str, *, default_area_code: str | None = None) -> list[PhoneCandidate]:
    """استخراج شماره از HTML: لینک‌های tel/واتساپ (اطمینان بالا) + متن صفحه."""
    if not html:
        return []
    out: list[PhoneCandidate] = []
    seen: set[str] = set()

    # ۱) لینک‌های tel: — قوی‌ترین شاهد
    for m in TEL_LINK_RE.finditer(html):
        target = unquote(m.group(1))[4:]
        parse = parse_phone(target)
        if parse and parse.e164 and parse.type != "SERVICE" and parse.valid_format:
            if parse.e164 in seen:
                continue
            seen.add(parse.e164)
            quality, notes = phone_quality(parse, )
            out.append(
                PhoneCandidate(
                    raw=target,
                    parse=parse,
                    kind="tel_link",
                    snippet=f'href="tel:{target}"',
                    quality=min(1.0, quality + 0.1),
                    quality_notes=notes,
                )
            )

    # ۲) لینک‌های واتساپ → شماره موبایل تجاری (فقط عمومی)
    for m in WHATSAPP_RE.finditer(html):
        parse = parse_phone(m.group(1))
        if parse and parse.is_mobile and parse.valid_format:
            if parse.e164 in seen:
                continue
            seen.add(parse.e164)
            quality, notes = phone_quality(parse)
            out.append(
                PhoneCandidate(
                    raw=m.group(1),
                    parse=parse,
                    kind="whatsapp",
                    snippet=collapse_ws(m.group(0)),
                    quality=quality,
                    quality_notes=notes,
                )
            )

    # ۳) متن صفحه
    text = strip_html(html)
    for cand in find_phones(text, default_area_code=default_area_code):
        if cand.e164 in seen:
            continue
        seen.add(cand.e164)
        out.append(cand)

    return out


def tel_link_numbers(html: str) -> list[str]:
    return [unquote(m.group(1))[4:] for m in TEL_LINK_RE.finditer(html or "")]


def urls_domain_list(url: str) -> str | None:
    try:
        return urlparse(url).hostname
    except ValueError:
        return None
