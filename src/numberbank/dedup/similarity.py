"""معیار شباهت جفتی کسب‌وکارها — قاعده‌محور و قابل توضیح."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from ..relevance.lexicon import GENERIC_BUSINESS_TOKENS, GENERIC_HOSTS
from ..text.normalize import name_key, normalize_fa, registrable_domain


@dataclass
class BusinessView:
    """نمای سبک یک کسب‌وکار برای مقایسه (بدون وابستگی به ORM)."""

    id: int | None = None
    name: str | None = None
    name_key: str | None = None
    city_name: str | None = None
    address: str | None = None
    domain: str | None = None
    phones: list[str] = field(default_factory=list)


@dataclass
class PairScore:
    score: float
    signals: dict[str, float]
    reasons: list[str]


_SPLIT_RE = re.compile(r"[\s\u200c\u200f\-_.،,()/\\]+")


def distinctive_tokens(name: str | None) -> set[str]:
    """واژه‌های متمایزکننده نام (بدون واژه‌های ساختاری/نقشی/دسته‌ای).

    «شرکت عمده فروشی مواد شوینده بهرامی» ← {«بهرامی»}
    این تفکیک از ادغام نادرست کسب‌وکارهای هم‌ساختار جلوگیری می‌کند.
    """
    if not name:
        return set()
    text = normalize_fa(name, zwnj="remove").lower()
    out: set[str] = set()
    for token in _SPLIT_RE.split(text):
        token = token.strip()
        if len(token) < 3 or token.isdigit():
            continue
        if token in GENERIC_BUSINESS_TOKENS:
            continue
        out.add(token)
    return out


def core_name_similarity(a: str | None, b: str | None) -> float | None:
    """شباهت واژه‌های متمایزکننده؛ ``None`` یعنی دست‌کم یک طرف واژه متمایز ندارد."""
    ta, tb = distinctive_tokens(a), distinctive_tokens(b)
    if not ta or not tb:
        return None
    return fuzz.token_set_ratio(" ".join(sorted(ta)), " ".join(sorted(tb))) / 100.0


def _nk(view: BusinessView) -> str:
    return view.name_key or name_key(view.name)


def _reg(domain: str | None) -> str | None:
    if not domain:
        return None
    reg = registrable_domain(domain)
    if reg and reg in GENERIC_HOSTS:
        return None
    return reg


def compare(a: BusinessView, b: BusinessView) -> PairScore:
    """محاسبه امتیاز شباهت ۰..۱ با دلایل — محافظه‌کارانه و مستند.

    اصول:
    ۱) نام‌های هم‌ساختار («شرکت پخش مواد شوینده الف» و «... ب») به‌خودی‌خود
       یکسان شمرده نمی‌شوند؛ واژه‌های متمایزکننده باید هم‌خوانی داشته باشند.
    ۲) ادغام خودکار تنها با «لنگر» معتبر ممکن است: شماره مشترک، دامنه یکسان،
       نشانی تقریباً یکسان، یا نام متمایز یکسان در همان شهر.
    ۳) هیچ ادغامی داده را حذف نمی‌کند؛ رکورد ادغام‌شده فقط MERGED می‌شود.
    """
    signals: dict[str, float] = {}
    reasons: list[str] = []

    shared_phones = set(a.phones) & set(b.phones)
    if shared_phones:
        signals["phone"] = 1.0
        reasons.append(f"شماره مشترک: {len(shared_phones)}")

    reg_a, reg_b = _reg(a.domain), _reg(b.domain)
    if reg_a and reg_a == reg_b:
        signals["domain"] = 1.0
        reasons.append("دامنه یکسان")

    nk_a, nk_b = _nk(a), _nk(b)
    name_sim = fuzz.token_set_ratio(nk_a, nk_b) / 100.0 if (nk_a and nk_b) else 0.0
    core_sim = core_name_similarity(a.name, b.name)
    signals["name"] = name_sim
    if core_sim is not None:
        signals["name_core"] = round(core_sim, 4)
        if core_sim < 0.75:
            # واژه‌های متمایزکننده ناهمخوان ⇒ سقف شباهت نام
            name_sim = min(name_sim, 0.35 + 0.64 * core_sim)
            reasons.append(f"واژه متمایز ناهمخوان (شباهت هسته {core_sim:.2f})")
        signals["name"] = round(name_sim, 4)

    if a.address and b.address:
        addr_sim = fuzz.token_set_ratio(name_key(a.address), name_key(b.address)) / 100.0
    else:
        addr_sim = 0.0
    signals["address"] = round(addr_sim, 4)

    same_city = bool(
        a.city_name and b.city_name and name_key(a.city_name) == name_key(b.city_name)
    )
    signals["city"] = 1.0 if same_city else 0.0
    same_city_unknown = not a.city_name or not b.city_name

    # ---- لنگرهای ادغام: بدون یکی از این‌ها امتیاز از آستانه بازبینی فراتر نمی‌رود ----
    exact_name = bool(nk_a and nk_a == nk_b)
    anchor = bool(
        shared_phones
        or signals.get("domain")
        or addr_sim >= 0.85
        or (exact_name and (same_city or same_city_unknown))
        or (core_sim is not None and core_sim >= 0.88 and (same_city or same_city_unknown))
    )
    if not anchor:
        reasons.append("بدون لنگر هویتی — فقط بازبینی انسانی")

    score = 0.0
    # قاعده ۱: شماره مشترک — تنها با تأیید نام یا نشانی (نه صرفاً شماره مشترک)
    if shared_phones and (name_sim >= 0.72 or addr_sim >= 0.70 or exact_name):
        score = max(score, 0.90)
        reasons.append("شماره مشترک + نام/نشانی هم‌خوان")
    # قاعده ۲: دامنه یکسان (سایت اختصاصی)
    if signals.get("domain"):
        score = max(score, 0.85)
    # قاعده ۳: نام بسیار مشابه در همان شهر (فقط وقتی هسته نام هم هم‌خوان باشد)
    if (same_city or same_city_unknown) and name_sim >= 0.90 and (core_sim is None or core_sim >= 0.80):
        score = max(score, 0.88)
    if (same_city or same_city_unknown) and name_sim >= 0.80 and (core_sim is None or core_sim >= 0.70):
        score = max(score, 0.70)
    # قاعده ۳.۵: هسته نام کاملاً یکسان اما عنوان کوتاه‌تر/بلندتر (بازبینی انسانی)
    if (same_city or same_city_unknown) and core_sim is not None and core_sim >= 0.95 and name_sim >= 0.50:
        score = max(score, 0.66)
    # قاعده ۴: نام مشابه + نشانی مشابه
    if name_sim >= 0.95 and addr_sim >= 0.85:
        score = max(score, 0.92)
    # امتیاز پایه ترکیبی
    score = max(score, 0.45 * name_sim + 0.25 * addr_sim + 0.20 * signals["city"])

    if not anchor:
        score = min(score, 0.55)

    return PairScore(score=round(min(1.0, score), 4), signals=signals, reasons=reasons)


def name_similarity(a: str | None, b: str | None) -> float:
    return fuzz.token_set_ratio(name_key(a), name_key(b)) / 100.0


def phone_similarity(a: list[str], b: list[str]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    if sa & sb:
        return 1.0
    # شباهت شماره‌های هم‌خانواده (اختلاف یک رقم — احتمال غلط تایپی)
    best = 0.0
    for x in sa:
        for y in sb:
            if len(x) == len(y):
                diff = sum(1 for c1, c2 in zip(x, y, strict=True) if c1 != c2)
                if diff == 1:
                    best = max(best, 0.6)
    return best
