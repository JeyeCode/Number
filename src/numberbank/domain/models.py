"""مدل داده سامانه (SQLAlchemy 2.0) — ۱۸ جدول اصلی طبق سند معماری."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


# --------------------------------------------------------------------------- #
# مرجع: مکان، دسته، منبع
# --------------------------------------------------------------------------- #
class Location(Base, TimestampMixin):
    """درخت تقسیمات کشوری: استان → شهرستان → شهر."""

    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)  # province|county|city
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    name_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    official_id: Mapped[str | None] = mapped_column(String(40), index=True)
    province_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"), index=True)
    county_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"), index=True)
    area_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    dataset_version: Mapped[str | None] = mapped_column(String(24))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint("official_id", name="uq_location_official_id"),
        Index("ix_location_kind_province", "kind", "province_id"),
        Index("ix_location_name_key_kind", "name_key", "kind"),
    )


class Category(Base, TimestampMixin):
    """دسته‌بندی فعالیت + واژگان مرتبط (قابل بسط از فایل واژگان)."""

    __tablename__ = "categories"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name_fa: Mapped[str] = mapped_column(String(80), nullable=False)
    terms: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    role_terms: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    negative_terms: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Source(Base, TimestampMixin):
    """رجیستری منابع با وزن اعتبار."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(400))
    reliability: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    requires_key: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    official: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_status: Mapped[str | None] = mapped_column(String(40))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime)
    notes: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


# --------------------------------------------------------------------------- #
# موجودیت اصلی: کسب‌وکار
# --------------------------------------------------------------------------- #
class Business(Base, TimestampMixin):
    __tablename__ = "businesses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(300))
    name_key: Mapped[str | None] = mapped_column(String(300), index=True)
    owner_name: Mapped[str | None] = mapped_column(String(200))
    owner_status: Mapped[str] = mapped_column(String(16), default="NOT_FOUND")
    address: Mapped[str | None] = mapped_column(Text)
    address_status: Mapped[str] = mapped_column(String(16), default="NOT_FOUND")
    province_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"), index=True)
    county_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"), index=True)
    city_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"), index=True)
    province_name: Mapped[str | None] = mapped_column(String(120), index=True)
    county_name: Mapped[str | None] = mapped_column(String(120))
    city_name: Mapped[str | None] = mapped_column(String(120), index=True)
    website: Mapped[str | None] = mapped_column(String(400))
    domain: Mapped[str | None] = mapped_column(String(200), index=True)
    social_links: Mapped[list[str]] = mapped_column(JSON, default=list)
    business_type: Mapped[str] = mapped_column(String(24), default="UNKNOWN", index=True)
    categories: Mapped[list[str]] = mapped_column(JSON, default=list)
    primary_category: Mapped[str | None] = mapped_column(String(32), index=True)
    relevance_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    relevance_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confidence: Mapped[int] = mapped_column(Integer, default=0, index=True)
    confidence_band: Mapped[str] = mapped_column(String(16), default="INVALID", index=True)
    confidence_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    independent_source_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", index=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    field_status: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    description: Mapped[str | None] = mapped_column(Text)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime)
    discovered_by_job_id: Mapped[int | None] = mapped_column(ForeignKey("search_jobs.id"))
    discovered_by_source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))

    phones: Mapped[list[BusinessPhone]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_business_city_category", "city_name", "primary_category"),
        Index("ix_business_band_status", "confidence_band", "status"),
    )


class Phone(Base, TimestampMixin):
    """شماره تلفن نرمال‌شده و یکتا (مستقل از کسب‌وکار)."""

    __tablename__ = "phones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    e164: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    national: Mapped[str] = mapped_column(String(20), nullable=False)
    digits: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(16), default="UNKNOWN", index=True)
    area_code: Mapped[str | None] = mapped_column(String(4), index=True)
    subscriber: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(20), default="UNVERIFIED", index=True)
    confidence: Mapped[int] = mapped_column(Integer, default=0, index=True)
    confidence_band: Mapped[str] = mapped_column(String(16), default="INVALID")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    invalid_reason: Mapped[str | None] = mapped_column(String(200))
    fake_signals: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    validation_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)

    businesses: Mapped[list[BusinessPhone]] = relationship(
        back_populates="phone", cascade="all, delete-orphan"
    )


class BusinessPhone(Base, TimestampMixin):
    """پیوند کسب‌وکار ↔ شماره با امتیاز اختصاصی آن پیوند."""

    __tablename__ = "business_phones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    phone_id: Mapped[int] = mapped_column(ForeignKey("phones.id"), nullable=False, index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    label: Mapped[str | None] = mapped_column(String(60))
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="UNVERIFIED", index=True)
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)

    business: Mapped[Business] = relationship(back_populates="phones")
    phone: Mapped[Phone] = relationship(back_populates="businesses")

    __table_args__ = (UniqueConstraint("business_id", "phone_id", name="uq_business_phone"),)


# --------------------------------------------------------------------------- #
# Evidence و اعتبارسنجی
# --------------------------------------------------------------------------- #
class SourceObservation(Base):
    """Evidence خام: هر بار مشاهده یک داده در یک منبع."""

    __tablename__ = "source_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int | None] = mapped_column(ForeignKey("businesses.id"), index=True)
    phone_id: Mapped[int | None] = mapped_column(ForeignKey("phones.id"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    query_id: Mapped[int | None] = mapped_column(ForeignKey("search_queries.id"))
    job_id: Mapped[int | None] = mapped_column(ForeignKey("search_jobs.id"))
    evidence_kind: Mapped[str] = mapped_column(String(24), default="PAGE_TEXT")
    url: Mapped[str | None] = mapped_column(String(1000))
    url_hash: Mapped[str | None] = mapped_column(String(40), index=True)
    url_domain: Mapped[str | None] = mapped_column(String(200), index=True)
    page_title: Mapped[str | None] = mapped_column(String(500))
    snippet: Mapped[str | None] = mapped_column(Text)
    extracted: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confidence_hint: Mapped[int] = mapped_column(Integer, default=50)
    source_reliability: Mapped[float] = mapped_column(Float, default=0.5)
    status: Mapped[str] = mapped_column(String(16), default="UNKNOWN", index=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    dedupe_hash: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class ValidationResult(Base):
    __tablename__ = "validation_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone_id: Mapped[int] = mapped_column(ForeignKey("phones.id"), nullable=False, index=True)
    business_id: Mapped[int | None] = mapped_column(ForeignKey("businesses.id"), index=True)
    method: Mapped[str] = mapped_column(String(24), default="FULL")
    result: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    band: Mapped[str] = mapped_column(String(16), default="INVALID")
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    signals: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)


class ValidationHistory(Base):
    __tablename__ = "validation_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone_id: Mapped[int] = mapped_column(ForeignKey("phones.id"), nullable=False, index=True)
    previous_band: Mapped[str | None] = mapped_column(String(16))
    new_band: Mapped[str] = mapped_column(String(16))
    previous_status: Mapped[str | None] = mapped_column(String(20))
    new_status: Mapped[str] = mapped_column(String(20))
    previous_confidence: Mapped[int | None] = mapped_column(Integer)
    new_confidence: Mapped[int] = mapped_column(Integer)
    changed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)


# --------------------------------------------------------------------------- #
# Job ها، پرس‌وجوها و تاریخچه
# --------------------------------------------------------------------------- #
class SearchJob(Base, TimestampMixin):
    __tablename__ = "search_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="DISCOVERY", index=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    target: Mapped[int] = mapped_column(Integer, default=0)
    min_confidence: Mapped[int] = mapped_column(Integer, default=0)
    total_queries: Mapped[int] = mapped_column(Integer, default=0)
    done_queries: Mapped[int] = mapped_column(Integer, default=0)
    pages_fetched: Mapped[int] = mapped_column(Integer, default=0)
    results_seen: Mapped[int] = mapped_column(Integer, default=0)
    businesses_new: Mapped[int] = mapped_column(Integer, default=0)
    businesses_updated: Mapped[int] = mapped_column(Integer, default=0)
    phones_new: Mapped[int] = mapped_column(Integer, default=0)
    phones_revalidated: Mapped[int] = mapped_column(Integer, default=0)
    duplicates_found: Mapped[int] = mapped_column(Integer, default=0)
    irrelevant_rejected: Mapped[int] = mapped_column(Integer, default=0)
    unverified_count: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime)


class SearchQuery(Base, TimestampMixin):
    """دفتر پرس‌وجوها — تضمین‌کننده نتیجe تکراری‌نداشتن در اجراهای بعدی."""

    __tablename__ = "search_queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("search_jobs.id"), index=True)
    source_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    query_text: Mapped[str] = mapped_column(String(500), nullable=False)
    query_hash: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    province_name: Mapped[str | None] = mapped_column(String(120), index=True)
    county_name: Mapped[str | None] = mapped_column(String(120))
    city_name: Mapped[str | None] = mapped_column(String(120), index=True)
    category: Mapped[str | None] = mapped_column(String(32), index=True)
    business_type: Mapped[str | None] = mapped_column(String(24))
    state: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    max_depth: Mapped[int] = mapped_column(Integer, default=3)
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    pages_fetched: Mapped[int] = mapped_column(Integer, default=0)
    results_seen: Mapped[int] = mapped_column(Integer, default=0)
    new_results: Mapped[int] = mapped_column(Integer, default=0)
    duplicates_seen: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)

    __table_args__ = (UniqueConstraint("source_key", "query_hash", name="uq_query_source_hash"),)


class SearchHistory(Base):
    __tablename__ = "search_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query_id: Mapped[int | None] = mapped_column(ForeignKey("search_queries.id"), index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("search_jobs.id"), index=True)
    source_key: Mapped[str] = mapped_column(String(64))
    query_text: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="DONE")
    pages: Mapped[int] = mapped_column(Integer, default=0)
    results: Mapped[int] = mapped_column(Integer, default=0)
    new_results: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class DuplicateRecord(Base):
    __tablename__ = "duplicate_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    merged_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(120), default="")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    signals: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("search_jobs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("search_jobs.id"), index=True)
    level: Mapped[str] = mapped_column(String(12), default="INFO", index=True)
    code: Mapped[str] = mapped_column(String(48), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)


# --------------------------------------------------------------------------- #
# صف، کش و کمکی‌ها
# --------------------------------------------------------------------------- #
class QueueTask(Base, TimestampMixin):
    __tablename__ = "queue_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    queue: Mapped[str] = mapped_column(String(40), default="default", index=True)
    task_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="QUEUED", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    available_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)
    leased_at: Mapped[datetime | None] = mapped_column(DateTime)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    worker_id: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (Index("ix_queue_state_priority", "state", "priority", "available_at"),)


class CrawlCache(Base):
    __tablename__ = "crawl_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url_hash: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(160))
    body: Mapped[bytes | None] = mapped_column(LargeBinary)
    encoding: Mapped[str | None] = mapped_column(String(40))
    error: Mapped[str | None] = mapped_column(Text)
    robots_allowed: Mapped[bool | None] = mapped_column(Boolean)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)


class ExportRecord(Base):
    __tablename__ = "exports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path: Mapped[str] = mapped_column(String(600), nullable=False)
    fmt: Mapped[str] = mapped_column(String(12), nullable=False)
    filters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class SystemKV(Base):
    __tablename__ = "system_kv"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SchemaMeta(Base):
    __tablename__ = "schema_meta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(24), nullable=False)
    note: Mapped[str | None] = mapped_column(String(200))
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


ALL_MODELS = [
    Location,
    Category,
    Source,
    Business,
    Phone,
    BusinessPhone,
    SourceObservation,
    ValidationResult,
    ValidationHistory,
    SearchJob,
    SearchQuery,
    SearchHistory,
    DuplicateRecord,
    JobEvent,
    QueueTask,
    CrawlCache,
    ExportRecord,
    SystemKV,
    SchemaMeta,
]
