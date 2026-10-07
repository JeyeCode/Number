"""موتور امتیاز اعتبار (Confidence Score) — شفاف، قابل توضیح و قابل تنظیم."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..config import Settings, get_settings
from ..domain.enums import ConfidenceBand, band_for_score

# جریمه‌ها (کسر از امتیاز نهایی)
PENALTY_FAKE_PATTERN = 40
PENALTY_AREA_MISMATCH = 25
PENALTY_WEAK_SOURCES_ONLY = 10
PENALTY_SPAM_PAGE = 30
PENALTY_STALE = 10
PENALTY_UNKNOWN_LOCATION = 5
PENALTY_INCONSISTENT_NAME = 15


@dataclass
class ScoreInput:
    """ورودی امتیازدهی — همه مقادیر از شواهد واقعی می‌آیند (بدون حدس)."""

    source_reliabilities: list[float] = field(default_factory=list)
    independent_domains: int = 0
    observation_count: int = 0

    has_name: bool = False
    has_address: bool = False
    has_website: bool = False
    has_owner: bool = False
    location_known: bool = False
    area_code_matches_location: bool | None = None

    phone_quality: float = 0.5
    phone_type: str = "UNKNOWN"
    fake_signals: list[str] = field(default_factory=list)
    is_synthetic: bool = False

    relevance_score: int = 0
    spam_flags: list[str] = field(default_factory=list)
    newest_observation_at: datetime | None = None
    business_type_known: bool = False


@dataclass
class ScoreResult:
    total: int
    band: ConfidenceBand
    components: dict[str, float]
    penalties: list[dict[str, object]]
    reasons: list[str]

    def as_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "band": self.band.value,
            "band_label": self.band.label_fa,
            "components": self.components,
            "penalties": self.penalties,
            "reasons": self.reasons,
        }


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def score_confidence(data: ScoreInput, settings: Settings | None = None) -> ScoreResult:
    """محاسبه امتیاز اعتبار ۰..۱۰۰ با ریز اجزا و جریمه‌های قابل بازبینی."""
    s = settings or get_settings()
    reasons: list[str] = []
    penalties: list[dict[str, object]] = []

    # ۱) اعتبار منابع
    if data.source_reliabilities:
        best = max(data.source_reliabilities)
        avg = sum(data.source_reliabilities) / len(data.source_reliabilities)
        source_score = _clamp((best * 0.7 + avg * 0.3) * 100)
        reasons.append(f"بهترین منبع با اعتبار {best:.2f}")
    else:
        source_score = 0.0
        reasons.append("بدون منبع شناخته‌شده")

    # ۲) تأیید چندمنبعی مستقل
    if data.independent_domains >= 3:
        corroboration = 100.0
    elif data.independent_domains == 2:
        corroboration = 70.0
    elif data.independent_domains == 1:
        corroboration = 40.0
    else:
        corroboration = 0.0
    if data.observation_count > data.independent_domains:
        corroboration = _clamp(corroboration + min(10.0, (data.observation_count - data.independent_domains) * 2))
    if data.independent_domains:
        reasons.append(f"{data.independent_domains} منبع مستقل تأییدکننده")

    # ۳) کامل بودن فیلدها
    fields = 0.0
    if data.has_name:
        fields += 34.0
    if data.has_address:
        fields += 22.0
    if data.has_website:
        fields += 12.0
    if data.has_owner:
        fields += 6.0
    if data.location_known:
        fields += 16.0
    if data.business_type_known:
        fields += 10.0
    fields_score = _clamp(fields)

    # ۴) کیفیت شماره
    phone_score = _clamp(data.phone_quality * 100)
    if data.phone_type == "MOBILE":
        phone_score = _clamp(phone_score - 5)  # موبایل کمی کمتر از ثابت دفتر
    if data.area_code_matches_location is True:
        phone_score = _clamp(phone_score + 6)
        reasons.append("پیش‌شماره با شهر کسب‌وکار سازگار است")
    if data.is_synthetic:
        reasons.append("شماره در بلوک مصنوعی تست قرار دارد")

    total = (
        s.weight_source * source_score
        + s.weight_corroboration * corroboration
        + s.weight_fields * fields_score
        + s.weight_phone * phone_score
    )

    # جریمه‌ها
    if data.fake_signals and not data.is_synthetic:
        total -= PENALTY_FAKE_PATTERN
        penalties.append({"code": "FAKE_PATTERN", "amount": PENALTY_FAKE_PATTERN,
                          "detail": "، ".join(data.fake_signals[:4])})
    if data.area_code_matches_location is False:
        total -= PENALTY_AREA_MISMATCH
        penalties.append({"code": "AREA_MISMATCH", "amount": PENALTY_AREA_MISMATCH,
                          "detail": "پیش‌شماره با استان/شهر کسب‌وکار ناسازگار است"})
    if data.source_reliabilities and max(data.source_reliabilities) < 0.55:
        total -= PENALTY_WEAK_SOURCES_ONLY
        penalties.append({"code": "WEAK_SOURCES", "amount": PENALTY_WEAK_SOURCES_ONLY,
                          "detail": "تنها منابع کم‌اعتبار مشاهده شده‌اند"})
    if data.spam_flags:
        total -= PENALTY_SPAM_PAGE
        penalties.append({"code": "SPAM_PAGE", "amount": PENALTY_SPAM_PAGE,
                          "detail": "، ".join(data.spam_flags[:3])})
    if data.newest_observation_at is None:
        total -= PENALTY_STALE
        penalties.append({"code": "NO_DATE", "amount": PENALTY_STALE, "detail": "بدون تاریخ مشاهده"})
    else:
        age = datetime.utcnow() - data.newest_observation_at
        if age > timedelta(days=730):
            total -= PENALTY_STALE
            penalties.append({"code": "STALE", "amount": PENALTY_STALE,
                              "detail": f"آخرین مشاهده {age.days} روز پیش"})
    if not data.location_known:
        total -= PENALTY_UNKNOWN_LOCATION
        penalties.append({"code": "UNKNOWN_LOCATION", "amount": PENALTY_UNKNOWN_LOCATION,
                          "detail": "موقعیت جغرافیایی نامشخص"})

    # دروازه ارتباط: کسب‌وکار بی‌ربط هرگز امتیاز بالا نمی‌گیرد
    if data.relevance_score < s.relevance_gate:
        cap = s.relevance_gate_confidence_cap
        if total > cap:
            penalties.append({"code": "RELEVANCE_GATE", "amount": int(total - cap),
                              "detail": f"ارتباط موضوعی پایین ({data.relevance_score})"})
            total = cap
        reasons.append(f"ارتباط موضوعی پایین ({data.relevance_score}) ⇒ سقف امتیاز {cap}")

    total = int(round(_clamp(total)))
    return ScoreResult(
        total=total,
        band=band_for_score(total),
        components={
            "source": round(source_score, 2),
            "corroboration": round(corroboration, 2),
            "fields": round(fields_score, 2),
            "phone": round(phone_score, 2),
        },
        penalties=penalties,
        reasons=reasons,
    )


def confidence_band_label(score: int) -> str:
    return band_for_score(score).label_fa
