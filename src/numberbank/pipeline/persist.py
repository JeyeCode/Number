"""ذخیره‌سازی کاندیدها با یکتاسازی، ثبت Evidence و محاسبه امتیاز اعتبار."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..dedup.fingerprint import business_fingerprint, observation_fingerprint
from ..domain.enums import (
    BusinessStatus,
    ConfidenceBand,
    FieldStatus,
    ObservationStatus,
    PhoneStatus,
)
from ..domain.models import (
    Business,
    BusinessPhone,
    Location,
    Phone,
    Source,
    SourceObservation,
    ValidationHistory,
    ValidationResult,
    utcnow,
)
from ..extract.page import BusinessCandidate
from ..logging_setup import get_logger
from ..phones.normalize import phone_quality
from ..phones.patterns import is_synthetic_number
from ..text.normalize import name_key, registrable_domain, truncate

log = get_logger("persist")


@dataclass
class PersistStats:
    candidates: int = 0
    businesses_new: int = 0
    businesses_updated: int = 0
    businesses_duplicate: int = 0
    phones_new: int = 0
    phones_reused: int = 0
    observations: int = 0
    observations_duplicate: int = 0
    rejected_irrelevant: int = 0
    rejected_spam: int = 0
    conflicts: int = 0
    synthetic: int = 0
    new_business_ids: list[int] = field(default_factory=list)
    new_phone_ids: list[int] = field(default_factory=list)


class Persister:
    """تبدیل کاندیدهای استخراج‌شده به رکوردهای پایدار — با ثبت کامل شواهد."""

    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._source_cache: dict[str, Source] = {}
        self._phone_cache: dict[str, Phone] = {}
        self._business_cache: dict[str, Business] = {}
        self._seen_observations: set[str] = set()
        self._location_index: dict[tuple[str, str], int] | None = None

    # ------------------------------------------------------------------ #
    def _locations(self) -> dict[tuple[str, str], int]:
        """نمایه یک‌باره مکان‌ها: (نوع، کلید نام) ← شناسه.

        نام‌های تکراری بین استان‌ها (مثل «رودبار») با کلید استان‌دار هم ذخیره
        می‌شوند تا انتساب شناسه اشتباه نشود.
        """
        if self._location_index is not None:
            return self._location_index
        index: dict[tuple[str, str], int] = {}
        rows = self.session.execute(select(Location)).scalars().all()
        provinces = {r.id: r.name for r in rows if r.kind == "province"}
        counties = {r.id: (r.name, provinces.get(r.province_id)) for r in rows if r.kind == "county"}
        for row in rows:
            index[(row.kind, name_key(row.name))] = row.id
            if row.kind == "province":
                continue
            province_name = provinces.get(row.province_id)
            if row.kind == "county":
                index[("county", f"{name_key(row.name)}|{name_key(province_name)}")] = row.id
            if row.kind == "city":
                county = counties.get(row.county_id)
                index[("city", f"{name_key(row.name)}|{name_key(county[0] if county else '')}")] = row.id
                index[("city", f"{name_key(row.name)}|{name_key(province_name)}")] = row.id
        self._location_index = index
        return index

    def _link_location(self, business: Business, cand: BusinessCandidate) -> bool:
        """اتصال رکورد به استان/شهرستان/شهر در جدول مکان‌ها (بدون حدس نام)."""
        if business.city_id is not None or not cand.city:
            return False
        index = self._locations()
        province_key = name_key(cand.province)
        county_key = name_key(cand.county)
        city_key = name_key(cand.city)
        city_id = (
            index.get(("city", f"{city_key}|{county_key}"))
            or index.get(("city", f"{city_key}|{province_key}"))
            or index.get(("city", city_key))
        )
        if city_id is None:
            return False
        business.city_id = city_id
        business.county_id = business.county_id or index.get(("county", f"{county_key}|{province_key}")) \
            or index.get(("county", county_key))
        business.province_id = business.province_id or index.get(("province", province_key))
        return True

    # ------------------------------------------------------------------ #
    def _get_source(self, source_key: str) -> Source:
        if source_key in self._source_cache:
            return self._source_cache[source_key]
        source = self.session.execute(select(Source).where(Source.key == source_key)).scalar_one_or_none()
        if source is None:
            source = Source(key=source_key, name=source_key, kind="SEARCH_ENGINE", reliability=0.5)
            self.session.add(source)
            self.session.flush()
        self._source_cache[source_key] = source
        return source

    # ------------------------------------------------------------------ #
    def _find_business_for_candidate(
        self, cand: BusinessCandidate, *, phone_ids: list[int], fp: str
    ) -> tuple[Business | None, bool]:
        """یافتن کسب‌وکار موجود (اثرانگشت، سپس شماره مشترک با نام مشابه)."""
        if fp in self._business_cache:
            return self._business_cache[fp], False
        existing = self.session.execute(select(Business).where(Business.fingerprint == fp)).scalar_one_or_none()
        if existing is not None:
            self._business_cache[fp] = existing
            return existing, False

        # تطبیق دوم: کسب‌وکاری که یکی از این شماره‌ها را دارد و نامش شبیه است
        if phone_ids and cand.name:
            rows = (
                self.session.execute(
                    select(Business)
                    .join(BusinessPhone, BusinessPhone.business_id == Business.id)
                    .where(BusinessPhone.phone_id.in_(phone_ids))
                    .limit(20)
                )
                .scalars()
                .all()
            )
            cand_key = name_key(cand.name)
            for row in rows:
                if not row.name_key:
                    continue
                if row.name_key == cand_key or (
                    cand.city and row.city_name and name_key(row.city_name) == name_key(cand.city)
                    and (cand_key in row.name_key or row.name_key in cand_key)
                ):
                    self._business_cache[fp] = row
                    return row, False
        return None, True

    # ------------------------------------------------------------------ #
    def _upsert_phone(self, cand: BusinessCandidate, phone_cand) -> tuple[Phone, bool]:
        parse = phone_cand.parse
        e164 = parse.e164
        cached = self._phone_cache.get(e164)
        if cached is not None:
            return cached, False
        row = self.session.execute(select(Phone).where(Phone.e164 == e164)).scalar_one_or_none()
        quality, notes = phone_quality(parse)
        synthetic = is_synthetic_number(parse.national)
        is_new = row is None
        if row is None:
            row = Phone(
                e164=e164,
                national=parse.national,
                digits=parse.digits,
                type=parse.type,
                area_code=parse.area_code,
                subscriber=parse.subscriber,
                status=PhoneStatus.UNVERIFIED.value,
                confidence=0,
                confidence_band=ConfidenceBand.INVALID.value,
                is_synthetic=synthetic,
                fake_signals=list(parse.fake_signals) + [n for n in notes if n in ("شماره خدماتی",)],
                first_seen_at=utcnow(),
                last_seen_at=utcnow(),
            )
            self.session.add(row)
            self.session.flush()
        else:
            row.last_seen_at = utcnow()
            if parse.type != "UNKNOWN" and row.type != parse.type and row.type == "UNKNOWN":
                row.type = parse.type
            if not row.area_code and parse.area_code:
                row.area_code = parse.area_code
        self._phone_cache[e164] = row
        return row, is_new

    # ------------------------------------------------------------------ #
    def upsert_candidates(
        self,
        candidates: list[BusinessCandidate],
        *,
        source_key: str,
        job_id: int | None = None,
        query_id: int | None = None,
        allow_synthetic: bool = True,
    ) -> PersistStats:
        stats = PersistStats()
        source = self._get_source(source_key)

        for cand in candidates:
            stats.candidates += 1
            relevance_score = cand.relevance.score if cand.relevance else 0

            # دروازه ارتباط و اسپم
            if cand.is_spam:
                stats.rejected_spam += 1
                continue
            if relevance_score < self.settings.relevance_gate:
                stats.rejected_irrelevant += 1
                # شاهد رد‌شده نیز ثبت می‌شود تا قابل بازبینی باشد
                self._record_observation(
                    cand, source, None, None, job_id, query_id,
                    status=ObservationStatus.SPAM.value if cand.is_spam else ObservationStatus.UNKNOWN.value,
                    confidence_hint=10,
                    stats=stats,
                )
                continue
            if not cand.phones and not cand.website:
                continue

            countries_synthetic = all(
                (p.is_synthetic if hasattr(p, "is_synthetic") else False) or is_synthetic_number(p.parse.national)
                for p in cand.phones
            ) if cand.phones else False
            if countries_synthetic:
                stats.synthetic += 1

            # --- شماره‌ها ---
            phone_rows: list[tuple[Phone, bool]] = []
            for p in cand.phones:
                if not p.e164:
                    continue
                phone_rows.append(self._upsert_phone(cand, p))
            phone_ids = [row.id for row, _ in phone_rows]

            # --- کسب‌وکار ---
            domain = cand.domain
            primary_e164 = cand.phones[0].e164 if cand.phones else None
            fp = business_fingerprint(name=cand.name, city_name=cand.city, domain=domain,
                                      phone_e164=primary_e164)
            business, is_new = self._find_business_for_candidate(cand, phone_ids=phone_ids, fp=fp)
            source_is_fixture = (source.kind or "").upper() == "FIXTURE"
            if business is None:
                business = Business(
                    fingerprint=fp,
                    name=cand.name,
                    name_key=name_key(cand.name) if cand.name else None,
                    discovered_at=utcnow(),
                    status=BusinessStatus.ACTIVE.value,
                    is_synthetic=source_is_fixture,
                    discovered_by_job_id=job_id,
                    discovered_by_source_id=source.id,
                )
                self.session.add(business)
                self.session.flush()
                stats.businesses_new += 1
                stats.new_business_ids.append(business.id)
                self._business_cache[fp] = business
            elif is_new:
                stats.businesses_new += 1
            else:
                stats.businesses_duplicate += 1

            self._apply_fields(business, cand, stats)
            self._link_location(business, cand)
            for row, created in phone_rows:
                if created:
                    stats.phones_new += 1
                    stats.new_phone_ids.append(row.id)
                else:
                    stats.phones_reused += 1
                self._link_phone(business, row, cand)

            business.last_seen_at = utcnow()
            if not business.is_synthetic and cand.phones:
                business.is_synthetic = source_is_fixture or all(
                    is_synthetic_number(p.parse.national) for p in cand.phones
                )

            # --- Evidence ---
            if cand.phones:
                for p in cand.phones:
                    phone_row = self._phone_cache.get(p.e164)
                    self._record_observation(cand, source, business, phone_row, job_id, query_id,
                                             stats=stats)
            else:
                self._record_observation(cand, source, business, None, job_id, query_id, stats=stats)

            self.session.flush()
            self._recompute_business(business)

        self.session.flush()
        return stats

    # ------------------------------------------------------------------ #
    def _apply_fields(self, business: Business, cand: BusinessCandidate, stats: PersistStats) -> None:
        """پرکردن فیلدهای خالی و علامت‌زدن تعارض — بدون بازنویسی داده معتبر و بدون حدس."""
        field_status = dict(business.field_status or {})
        updated = False

        def fill(attr: str, value, status_key: str | None = None) -> None:
            nonlocal updated
            if value in (None, "", [], {}):
                return
            current = getattr(business, attr)
            if current in (None, "", [], {}):
                setattr(business, attr, value)
                if status_key:
                    field_status[status_key] = FieldStatus.OK.value
                updated = True
            elif str(current).strip() != str(value).strip():
                key = status_key or attr
                comparable = (attr in ("name", "address", "city_name", "county_name", "province_name",
                                       "website", "owner_name"))
                if comparable and _is_meaningfully_different(str(current), str(value), attr):
                    if key not in field_status or field_status.get(key) == FieldStatus.OK.value:
                        field_status[key] = FieldStatus.CONFLICTED.value
                        stats.conflicts += 1

        fill("name", cand.name, "name")
        fill("address", cand.address, "address")
        fill("website", cand.website, "website")
        fill("owner_name", cand.owner_name, "owner_name")
        fill("city_name", cand.city, "city")
        fill("county_name", cand.county, "county")
        fill("province_name", cand.province, "province")

        # صداقت داده: مکانی که فقط از زمینه پرس‌وجو آمده (بدون شاهد در متن)
        # هرگز «تأییدشده» علامت نمی‌خورد.
        if (cand.location_confidence or 0) <= 20:
            for key in ("city", "county", "province"):
                if key in field_status and field_status[key] == FieldStatus.OK.value:
                    field_status[key] = FieldStatus.UNVERIFIED.value
                elif key not in field_status:
                    field_status[key] = FieldStatus.UNVERIFIED.value
        fill("description", cand.page_text_excerpt, None)

        if not business.name_key and cand.name:
            business.name_key = name_key(cand.name)
        if not business.domain and cand.website:
            business.domain = registrable_domain(cand.website)

        # دسته‌بندی و نوع کسب‌وکار (اجتماع مقادیر مشاهده‌شده)
        categories = set(business.categories or [])
        if cand.primary_category:
            categories.add(cand.primary_category)
        if categories and set(categories) != set(business.categories or []):
            business.categories = sorted(categories)
            updated = True
        if cand.business_type and cand.business_type != "UNKNOWN":
            if business.business_type in (None, "UNKNOWN"):
                business.business_type = cand.business_type
                updated = True
        if cand.social_links:
            socials = list(dict.fromkeys((business.social_links or []) + cand.social_links))[:10]
            if socials != (business.social_links or []):
                business.social_links = socials
                updated = True

        # امتیاز ارتباط: بیشینه امتیاز مشاهده‌شده (با شواهد)
        if cand.relevance:
            if cand.relevance.score > (business.relevance_score or 0):
                business.relevance_score = cand.relevance.score
                business.relevance_evidence = {
                    "score": cand.relevance.score,
                    "category_scores": cand.relevance.category_scores,
                    "role_scores": cand.relevance.role_scores,
                    "matched_terms": cand.relevance.matched_terms,
                    "negative_terms": cand.relevance.negative_terms,
                    "reasons": cand.relevance.reasons,
                    "primary_category": cand.relevance.primary_category,
                }
                updated = True
            if not business.primary_category and cand.relevance.primary_category:
                business.primary_category = cand.relevance.primary_category
                updated = True

        # وضعیت فیلدهای ناموجود
        for key, attr in (("name", "name"), ("address", "address"), ("website", "website"),
                          ("owner_name", "owner_name"), ("city", "city_name")):
            if getattr(business, attr) in (None, ""):
                if key not in field_status:
                    field_status[key] = FieldStatus.NOT_FOUND.value
            elif field_status.get(key) == FieldStatus.NOT_FOUND.value:
                field_status[key] = FieldStatus.OK.value
            elif key not in field_status:
                field_status[key] = FieldStatus.UNVERIFIED.value if key == "owner_name" else FieldStatus.OK.value

        business.field_status = field_status
        if updated:
            stats.businesses_updated += 1

    # ------------------------------------------------------------------ #
    def _link_phone(self, business: Business, phone: Phone, cand: BusinessCandidate) -> None:
        link = self.session.execute(
            select(BusinessPhone).where(
                BusinessPhone.business_id == business.id, BusinessPhone.phone_id == phone.id
            )
        ).scalar_one_or_none()
        if link is None:
            link = BusinessPhone(
                business_id=business.id,
                phone_id=phone.id,
                first_seen_at=utcnow(),
                last_seen_at=utcnow(),
                label=", ".join(cand.phones[0].nearby_role_terms[:2]) if cand.phones else None,
            )
            self.session.add(link)
        else:
            link.last_seen_at = utcnow()
        if not any(
            bp.is_primary
            for bp in self.session.execute(
                select(BusinessPhone).where(BusinessPhone.business_id == business.id)
            ).scalars()
        ):
            link.is_primary = True

    # ------------------------------------------------------------------ #
    def _record_observation(
        self,
        cand: BusinessCandidate,
        source: Source,
        business: Business | None,
        phone: Phone | None,
        job_id: int | None,
        query_id: int | None,
        *,
        status: str = ObservationStatus.VALID.value,
        confidence_hint: int | None = None,
        stats: PersistStats | None = None,
    ) -> None:
        url = cand.source_url
        dedupe = observation_fingerprint(
            url=url,
            phone_e164=phone.e164 if phone else None,
            business_fp=business.fingerprint if business else None,
        )
        # ۱) بررسی حافظه‌ای (سریع و مطمئن در همین دسته)
        if dedupe in self._seen_observations:
            if stats is not None:
                stats.observations_duplicate += 1
            return
        self._seen_observations.add(dedupe)
        # ۲) بررسی دیتابیس
        exists = self.session.execute(
            select(SourceObservation.id).where(SourceObservation.dedupe_hash == dedupe)
        ).scalar_one_or_none()
        if exists:
            row = self.session.get(SourceObservation, exists)
            if row is not None:
                row.observed_at = utcnow()
            if stats is not None:
                stats.observations_duplicate += 1
            return
        observation = SourceObservation(
            business_id=business.id if business else None,
            phone_id=phone.id if phone else None,
            source_id=source.id,
            query_id=query_id,
            job_id=job_id,
            evidence_kind=cand.evidence_kind,
            url=truncate(url, 990),
            url_hash=observation_fingerprint(url=url, phone_e164=None, business_fp=None),
            url_domain=registrable_domain(url),
            page_title=truncate(cand.name, 480),
            snippet=truncate(cand.snippet or cand.page_text_excerpt, 2000),
            extracted={
                "name": cand.name,
                "phones": [p.e164 for p in cand.phones],
                "address": cand.address,
                "city": cand.city,
                "province": cand.province,
                "website": cand.website,
                "owner_name": cand.owner_name,
                "business_type": cand.business_type,
                "primary_category": cand.primary_category,
                "relevance_score": cand.relevance.score if cand.relevance else None,
            },
            confidence_hint=confidence_hint if confidence_hint is not None else cand.confidence_hint,
            source_reliability=source.reliability,
            status=status,
            is_synthetic=cand.evidence_kind == "FIXTURE" or (
                bool(cand.phones) and all(is_synthetic_number(p.parse.national) for p in cand.phones)
            ),
            dedupe_hash=dedupe,
            observed_at=utcnow(),
        )
        self.session.add(observation)
        if stats is not None:
            stats.observations += 1

    # ------------------------------------------------------------------ #
    def _recompute_business(self, business: Business) -> None:
        """بازمحاسبه امتیاز اعتبار کسب‌وکار و پیوندهای شماره (منطق مشترک در scoring/pair)."""
        from ..scoring.pair import recompute_business as _recompute

        _recompute(self.session, business, settings=self.settings)

    # ------------------------------------------------------------------ #
    def recompute_all(self, limit: int | None = None) -> int:
        """بازمحاسبه امتیاز همه کسب‌وکارها (برای تغییر پارامترهای امتیازدهی)."""
        query = select(Business)
        if limit:
            query = query.limit(limit)
        count = 0
        for business in self.session.execute(query).scalars():
            self._recompute_business(business)
            count += 1
        self.session.flush()
        return count

    # ------------------------------------------------------------------ #
    def record_validation(self, phone: Phone, *, method: str = "FULL", notes: str | None = None) -> None:
        previous_band = phone.confidence_band
        previous_status = phone.status
        previous_conf = phone.confidence
        result = ValidationResult(
            phone_id=phone.id,
            method=method,
            result=phone.status,
            band=phone.confidence_band,
            confidence=phone.confidence,
            signals={"type": phone.type, "area_code": phone.area_code,
                     "fake_signals": phone.fake_signals, "source_count": phone.source_count},
            notes=notes,
            checked_at=utcnow(),
        )
        self.session.add(result)
        changed = previous_band != phone.confidence_band or previous_status != phone.status
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
                reason=notes,
            )
        )


def _is_meaningfully_different(current: str, incoming: str, attr: str) -> bool:
    """آیا دو مقدار واقعاً متعارض‌اند یا فقط تفاوت نگارشی دارند؟"""
    if attr == "website":
        return registrable_domain(current) != registrable_domain(incoming)
    if attr in ("name", "city_name", "county_name", "province_name", "owner_name"):
        return name_key(current) != name_key(incoming)
    a, b = current.strip(), incoming.strip()
    if a == b:
        return False
    if a in b or b in a:
        return False
    return True
