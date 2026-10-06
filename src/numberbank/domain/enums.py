"""شمارشی‌ها و وضعیت‌های دامنه — مرجع واحد وضعیت‌ها در کل سامانه."""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - سادگی نمایش
        return str(self.value)


class FieldStatus(StrEnum):
    """وضعیت هر فیلد اختیاری — ممنوعیت هرگونه حدس‌زدن."""

    OK = "OK"
    NOT_FOUND = "NOT_FOUND"
    UNVERIFIED = "UNVERIFIED"
    CONFLICTED = "CONFLICTED"


class PhoneType(StrEnum):
    LANDLINE = "LANDLINE"
    MOBILE = "MOBILE"
    SERVICE = "SERVICE"
    UNKNOWN = "UNKNOWN"


class PhoneStatus(StrEnum):
    VALID = "VALID"
    PROBABLE = "PROBABLE"
    UNVERIFIED = "UNVERIFIED"
    INVALID = "INVALID"
    QUARANTINED = "QUARANTINED"

    # نام قدیمی (سازگاری عقب‌رو)
    UNREACHABLE_UNKNOWN = "UNVERIFIED"

    @property
    def label_fa(self) -> str:
        return {
            "VALID": "معتبر",
            "PROBABLE": "احتمالاً معتبر",
            "UNVERIFIED": "اعتبارسنجی‌نشده",
            "INVALID": "نامعتبر",
            "QUARANTINED": "قرنطینه (نامرتبط)",
        }[self.value]


class ConfidenceBand(StrEnum):
    VERY_HIGH = "VERY_HIGH"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INVALID = "INVALID"

    @property
    def label_fa(self) -> str:
        return {
            "VERY_HIGH": "اعتبار بسیار بالا",
            "HIGH": "اعتبار بالا",
            "MEDIUM": "اعتبار متوسط",
            "LOW": "اعتبار پایین",
            "INVALID": "نامعتبر",
        }[self.value]


BAND_THRESHOLDS: tuple[tuple[int, ConfidenceBand], ...] = (
    (85, ConfidenceBand.VERY_HIGH),
    (70, ConfidenceBand.HIGH),
    (45, ConfidenceBand.MEDIUM),
    (20, ConfidenceBand.LOW),
)


def band_for_score(score: int) -> ConfidenceBand:
    for threshold, band in BAND_THRESHOLDS:
        if score >= threshold:
            return band
    return ConfidenceBand.INVALID


class BusinessType(StrEnum):
    WHOLESALER = "WHOLESALER"
    DISTRIBUTOR = "DISTRIBUTOR"
    RETAILER = "RETAILER"
    MANUFACTURER = "MANUFACTURER"
    STORE = "STORE"
    UNKNOWN = "UNKNOWN"

    @property
    def label_fa(self) -> str:
        return {
            "WHOLESALER": "عمده‌فروش",
            "DISTRIBUTOR": "پخش/توزیع‌کننده",
            "RETAILER": "خرده‌فروش",
            "MANUFACTURER": "تولیدکننده",
            "STORE": "فروشگاه",
            "UNKNOWN": "نامشخص",
        }[self.value]


class CategoryCode(StrEnum):
    DETERGENT = "DETERGENT"
    HYGIENE = "HYGIENE"
    COSMETIC = "COSMETIC"
    CELLULOSE = "CELLULOSE"
    OTHER = "OTHER"

    @property
    def label_fa(self) -> str:
        return {
            "DETERGENT": "شوینده",
            "HYGIENE": "بهداشتی",
            "COSMETIC": "آرایشی و بهداشتی",
            "CELLULOSE": "سلولزی",
            "OTHER": "سایر",
        }[self.value]


class BusinessStatus(StrEnum):
    ACTIVE = "ACTIVE"
    QUARANTINED = "QUARANTINED"
    INVALID = "INVALID"
    MERGED = "MERGED"
    ARCHIVED = "ARCHIVED"

    @property
    def label_fa(self) -> str:
        return {
            "ACTIVE": "فعال",
            "QUARANTINED": "قرنطینه",
            "INVALID": "نامعتبر",
            "MERGED": "ادغام‌شده",
            "ARCHIVED": "بایگانی",
        }[self.value]


class SourceKind(StrEnum):
    SEARCH_ENGINE = "SEARCH_ENGINE"
    BUSINESS_DIRECTORY = "BUSINESS_DIRECTORY"
    COMPANY_SITE = "COMPANY_SITE"
    OPEN_DATA = "OPEN_DATA"
    SOCIAL = "SOCIAL"
    FIXTURE = "FIXTURE"

    @property
    def label_fa(self) -> str:
        return {
            "SEARCH_ENGINE": "موتور جستجو",
            "BUSINESS_DIRECTORY": "دایرکتوری تجاری",
            "COMPANY_SITE": "سایت شرکت",
            "OPEN_DATA": "داده باز",
            "SOCIAL": "شبکه اجتماعی عمومی",
            "FIXTURE": "داده آزمایشی",
        }[self.value]


class JobKind(StrEnum):
    DISCOVERY = "DISCOVERY"
    ENRICH = "ENRICH"
    REVALIDATE = "REVALIDATE"
    DEDUP = "DEDUP"


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def label_fa(self) -> str:
        return {
            "PENDING": "در انتظار",
            "RUNNING": "در حال اجرا",
            "PAUSED": "متوقف‌شده",
            "DONE": "پایان‌یافته",
            "FAILED": "خطا",
            "CANCELLED": "لغو‌شده",
        }[self.value]


class TaskState(StrEnum):
    QUEUED = "QUEUED"
    LEASED = "LEASED"
    DONE = "DONE"
    FAILED = "FAILED"
    DEAD = "DEAD"


class QueryState(StrEnum):
    PENDING = "PENDING"
    DONE = "DONE"
    FAILED = "FAILED"
    COOLING = "COOLING"
    BLOCKED = "BLOCKED"


class EvidenceKind(StrEnum):
    SEARCH_SNIPPET = "SEARCH_SNIPPET"
    JSONLD = "JSONLD"
    DIRECTORY_ENTRY = "DIRECTORY_ENTRY"
    PAGE_TEXT = "PAGE_TEXT"
    FIXTURE = "FIXTURE"


class ObservationStatus(StrEnum):
    VALID = "VALID"
    SPAM = "SPAM"
    COPIED = "COPIED"
    UNREACHABLE = "UNREACHABLE"
    UNKNOWN = "UNKNOWN"


class ValidationMethod(StrEnum):
    FORMAT = "FORMAT"
    CROSS_SOURCE = "CROSS_SOURCE"
    STALENESS = "STALENESS"
    FULL = "FULL"
