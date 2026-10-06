"""ارزیابی اعتبار یک جفت (کسب‌وکار، شماره) بر پایه همه شواهد موجود."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..domain.enums import BusinessStatus, ConfidenceBand, ObservationStatus, PhoneStatus
from ..domain.models import Business, BusinessPhone, Phone, Source, SourceObservation
from ..geo.gazetteer import get_gazetteer
from ..phones.normalize import parse_phone, phone_quality
from .confidence import ScoreInput, ScoreResult, score_confidence


@dataclass
class PairEvaluation:
    score: ScoreResult
    status: str
    independent_domains: int
    source_count: int
    phone_quality: float
    area_code_match: bool | None
    phone_quality_notes: list[str]


def evaluate_pair(
    session: Session,
    business: Business,
    phone: Phone,
    *,
    settings: Settings | None = None,
) -> PairEvaluation:
    """محاسبه امتیاز و وضعیت یک پیوند (کسب‌وکار ↔ شماره)."""
    s = settings or get_settings()

    observations = (
        session.execute(select(SourceObservation).where(SourceObservation.business_id == business.id))
        .scalars()
        .all()
    )
    phone_obs = [o for o in observations if o.phone_id == phone.id]
    relevant_obs = phone_obs or observations

    source_ids = {o.source_id for o in relevant_obs}
    sources = {
        row.id: row
        for row in session.execute(select(Source).where(Source.id.in_(source_ids or [0]))).scalars()
    }
    reliabilities = [sources[o.source_id].reliability for o in relevant_obs if o.source_id in sources]
    domains = {o.url_domain for o in relevant_obs if o.url_domain}
    newest = max((o.observed_at for o in relevant_obs), default=None)
    spam_flags = ["صفحه اسپم"] if any(o.status == ObservationStatus.SPAM.value for o in observations) else []

    gazetteer = get_gazetteer()
    expected_area = gazetteer.area_code_for_city(business.city_name, business.province_name)
    area_match: bool | None = None
    if phone.area_code and expected_area:
        area_match = phone.area_code == expected_area
    elif phone.type == "MOBILE":
        area_match = True

    parsed = parse_phone(phone.e164) or parse_phone(phone.national)
    quality, notes = phone_quality(parsed, area_code_expected=expected_area)

    score = score_confidence(
        ScoreInput(
            source_reliabilities=reliabilities,
            independent_domains=len(domains),
            observation_count=len(relevant_obs),
            has_name=bool(business.name),
            has_address=bool(business.address),
            has_website=bool(business.website),
            has_owner=bool(business.owner_name),
            location_known=bool(business.city_name or business.province_name),
            area_code_matches_location=area_match,
            phone_quality=quality,
            phone_type=phone.type,
            fake_signals=list(phone.fake_signals or []),
            is_synthetic=bool(phone.is_synthetic),
            relevance_score=business.relevance_score,
            spam_flags=spam_flags,
            newest_observation_at=newest,
            business_type_known=business.business_type not in (None, "UNKNOWN"),
        ),
        s,
    )

    # تعیین وضعیت شماره
    strong_fake = bool(phone.fake_signals) and not phone.is_synthetic
    if score.total < 20 or strong_fake:
        status = PhoneStatus.INVALID.value
    elif business.relevance_score < s.relevance_gate:
        status = PhoneStatus.QUARANTINED.value
    elif score.band in (ConfidenceBand.VERY_HIGH, ConfidenceBand.HIGH):
        status = PhoneStatus.VALID.value
    elif score.band is ConfidenceBand.MEDIUM:
        status = PhoneStatus.PROBABLE.value
    else:
        status = PhoneStatus.UNVERIFIED.value

    if score.total == 0 and not relevant_obs:
        status = PhoneStatus.UNVERIFIED.value

    return PairEvaluation(
        score=score,
        status=status,
        independent_domains=len(domains),
        source_count=len({o.source_id for o in relevant_obs}),
        phone_quality=quality,
        area_code_match=area_match,
        phone_quality_notes=notes + (notes and [] or []),
    )


def apply_pair_evaluation(
    session: Session,
    business: Business,
    link: BusinessPhone,
    evaluation: PairEvaluation,
    *,
    settings: Settings | None = None,
) -> None:
    """اعمال نتیجه ارزیابی روی پیوند، جدول شماره و کسب‌وکار."""
    from datetime import timedelta

    from ..domain.models import utcnow

    s = settings or get_settings()
    link.confidence = evaluation.score.total
    link.status = evaluation.status
    link.source_count = evaluation.source_count

    phone = session.get(Phone, link.phone_id)
    if phone is not None and evaluation.score.total >= (phone.confidence or 0):
        phone.confidence = evaluation.score.total
        phone.confidence_band = evaluation.score.band.value
        phone.status = evaluation.status
        phone.source_count = max(phone.source_count or 0, evaluation.source_count)
        phone.last_validated_at = utcnow()
        phone.validation_count = (phone.validation_count or 0) + 1
        phone.next_check_at = utcnow() + timedelta(days=s.revalidate_after_days)


def recompute_business(session: Session, business: Business, *, settings: Settings | None = None) -> PairEvaluation | None:
    """بازمحاسبه همه پیوندهای یک کسب‌وکار و خلاصه‌سازی روی رکورد کسب‌وکار."""
    s = settings or get_settings()
    links = (
        session.execute(select(BusinessPhone).where(BusinessPhone.business_id == business.id)).scalars().all()
    )
    best: PairEvaluation | None = None
    for link in links:
        phone = session.get(Phone, link.phone_id)
        if phone is None:
            continue
        evaluation = evaluate_pair(session, business, phone, settings=s)
        apply_pair_evaluation(session, business, link, evaluation, settings=s)
        if best is None or evaluation.score.total > best.score.total:
            best = evaluation

    observations = (
        session.execute(select(SourceObservation).where(SourceObservation.business_id == business.id))
        .scalars()
        .all()
    )
    business.source_count = len({o.source_id for o in observations})
    business.independent_source_count = len({o.url_domain for o in observations if o.url_domain})
    business.confidence = best.score.total if best else 0
    business.confidence_band = best.score.band.value if best else ConfidenceBand.INVALID.value
    business.confidence_breakdown = {
        "band_label": best.score.band.label_fa if best else ConfidenceBand.INVALID.label_fa,
        "components": best.score.components if best else {},
        "penalties": best.score.penalties if best else [],
        "reasons": best.score.reasons if best else [],
        "independent_domains": business.independent_source_count,
        "observations": len(observations),
    }
    business.last_validated_at = __import__("numberbank.domain.models", fromlist=["utcnow"]).utcnow()

    if business.relevance_score < s.relevance_gate and business.status == BusinessStatus.ACTIVE.value:
        business.status = BusinessStatus.QUARANTINED.value
    return best
