"""اعتبارسنجی شماره‌ها: بازمحاسبه امتیاز از شواهد، تاریخچه تغییرات و زمان‌بندی بازبینی."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..domain.enums import PhoneStatus, ValidationMethod
from ..domain.models import BusinessPhone, Phone, ValidationHistory, ValidationResult, utcnow
from ..logging_setup import get_logger
from ..phones.normalize import parse_phone, phone_quality
from ..phones.patterns import is_synthetic_number
from ..scoring.pair import evaluate_pair, recompute_business

log = get_logger("validation")


@dataclass
class ValidationReport:
    phones_checked: int = 0
    channels_checked: int = 0
    promoted: int = 0
    demoted: int = 0
    marked_invalid: int = 0
    quarantined: int = 0
    unchanged: int = 0
    changes: list[dict] = field(default_factory=list)
    duration_ms: int = 0

    def as_dict(self) -> dict:
        return {
            "phones_checked": self.phones_checked,
            "promoted": self.promoted,
            "demoted": self.demoted,
            "marked_invalid": self.marked_invalid,
            "quarantined": self.quarantined,
            "unchanged": self.unchanged,
        }


class ValidationService:
    """بازاعتبارسنجی دوره‌ای و ساختاری — کاملاً مبتنی بر شواهد، بدون تماس با شماره‌ها."""

    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------ #
    def validate_phone_structure(self, phone: Phone) -> None:
        """اعتبارسنجی ساختاری (قالب، پیش‌شماره، الگوهای جعلی)."""
        parsed = parse_phone(phone.e164) or parse_phone(phone.national)
        if parsed is None:
            phone.status = PhoneStatus.INVALID.value
            phone.invalid_reason = "قابل تجزیه نیست"
            return
        quality, notes = phone_quality(parsed)
        phone.fake_signals = list(parsed.fake_signals)
        phone.is_synthetic = is_synthetic_number(parsed.national)
        if not parsed.valid_format and not phone.is_synthetic:
            phone.status = PhoneStatus.INVALID.value
            phone.invalid_reason = parsed.reason or "قالب نامعتبر"
        else:
            phone.invalid_reason = None
        phone.confidence = int(round(quality * 100)) if phone.confidence == 0 else phone.confidence

    # ------------------------------------------------------------------ #
    def revalidate_due(self, *, limit: int = 500, job_id: int | None = None,
                       force: bool = False) -> ValidationReport:
        started = time.monotonic()
        report = ValidationReport()
        query = select(Phone)
        if not force:
            query = query.where(
                (Phone.next_check_at.is_(None)) | (Phone.next_check_at <= datetime.utcnow())
            )
        phones = self.session.execute(query.order_by(Phone.confidence.desc()).limit(limit)).scalars().all()

        for phone in phones:
            report.phones_checked += 1
            previous_band = phone.confidence_band
            previous_status = phone.status
            previous_conf = phone.confidence

            self.validate_phone_structure(phone)

            links = (
                self.session.execute(select(BusinessPhone).where(BusinessPhone.phone_id == phone.id))
                .scalars()
                .all()
            )
            if not links:
                phone.status = phone.status if phone.status == PhoneStatus.INVALID.value else PhoneStatus.UNVERIFIED.value
                phone.next_check_at = utcnow() + timedelta(days=self.settings.revalidate_after_days)
            best = 0
            for link in links:
                business = link.business
                if business is None:
                    continue
                evaluation = evaluate_pair(self.session, business, phone, settings=self.settings)
                report.channels_checked += 1
                link.confidence = evaluation.score.total
                link.status = evaluation.status
                if evaluation.score.total > best:
                    best = evaluation.score.total
                    phone.confidence = evaluation.score.total
                    phone.confidence_band = evaluation.score.band.value
                    phone.status = evaluation.status
                recompute_business(self.session, business, settings=self.settings)

            phone.last_validated_at = utcnow()
            phone.validation_count = (phone.validation_count or 0) + 1
            phone.next_check_at = utcnow() + timedelta(days=self.settings.revalidate_after_days)

            changed = (previous_band != phone.confidence_band) or (previous_status != phone.status)
            self.session.add(
                ValidationResult(
                    phone_id=phone.id,
                    method=ValidationMethod.FULL.value,
                    result=phone.status,
                    band=phone.confidence_band,
                    confidence=phone.confidence,
                    breakdown={"previous": {"band": previous_band, "status": previous_status,
                                            "confidence": previous_conf}},
                    signals={"type": phone.type, "area_code": phone.area_code,
                             "fake_signals": phone.fake_signals, "source_count": phone.source_count},
                    notes=None if not changed else "تغییر وضعیت در بازاعتبارسنجی",
                    checked_at=utcnow(),
                )
            )
            self.session.add(
                ValidationHistory(
                    phone_id=phone.id,
                    previous_band=previous_band,
                    new_band=phone.confidence_band,
                    previous_status=previous_status,
                    new_status=phone.status,
                    previous_confidence=previous_conf,
                    new_confidence=phone.confidence,
                    changed=changed,
                    reason="بازاعتبارسنجی دوره‌ای",
                )
            )

            if changed:
                report.changes.append(
                    {"phone": phone.e164, "from": previous_band, "to": phone.confidence_band,
                     "status": phone.status}
                )
                if phone.status == PhoneStatus.INVALID.value:
                    report.marked_invalid += 1
                elif phone.status == PhoneStatus.QUARANTINED.value:
                    report.quarantined += 1
                elif _rank(phone.confidence_band) > _rank(previous_band):
                    report.promoted += 1
                else:
                    report.demoted += 1
            else:
                report.unchanged += 1

        self.session.flush()
        report.duration_ms = int((time.monotonic() - started) * 1000)
        log.info(
            "بازاعتبارسنجی: %s شماره بررسی شد (تغییر=%s، نامعتبر=%s)",
            report.phones_checked, len(report.changes), report.marked_invalid,
        )
        return report

    # ------------------------------------------------------------------ #
    def due_count(self) -> int:
        from sqlalchemy import func

        return int(
            self.session.execute(
                select(func.count(Phone.id)).where(
                    (Phone.next_check_at.is_(None)) | (Phone.next_check_at <= datetime.utcnow())
                )
            ).scalar()
            or 0
        )


_BAND_ORDER = {"INVALID": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "VERY_HIGH": 4}


def _rank(band: str | None) -> int:
    return _BAND_ORDER.get(band or "INVALID", 0)
