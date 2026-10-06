"""خدمات یکتاسازی: خوشه‌بندی، ادغام رکوردها و حفظ منابع همه نسخه‌ها."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..dedup.cluster import find_duplicate_clusters
from ..dedup.similarity import BusinessView
from ..domain.enums import BusinessStatus
from ..domain.models import (
    Business,
    BusinessPhone,
    DuplicateRecord,
    Phone,
    SourceObservation,
    utcnow,
)
from ..logging_setup import get_logger
from ..scoring.pair import recompute_business

log = get_logger("dedup")


@dataclass
class DedupReport:
    scanned: int = 0
    clusters: int = 0
    merged: int = 0
    review_pairs: int = 0
    observations_moved: int = 0
    phones_moved: int = 0
    duration_ms: int = 0
    details: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "clusters": self.clusters,
            "merged": self.merged,
            "review_pairs": self.review_pairs,
            "observations_moved": self.observations_moved,
            "phones_moved": self.phones_moved,
        }


class Deduplicator:
    """حذف تکراری‌ها بر پایه شماره، نام، نشانی، شهر، دامنه و شباهت نام."""

    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------ #
    def _load_views(self, *, batch_size: int, statuses: tuple[str, ...]) -> list[BusinessView]:
        businesses = (
            self.session.execute(
                select(Business)
                .where(Business.status.in_(statuses))
                .order_by(Business.id)
                .limit(batch_size)
            )
            .scalars()
            .all()
        )
        views: list[BusinessView] = []
        for b in businesses:
            phones = [
                p.e164
                for p in self.session.execute(
                    select(Phone).join(BusinessPhone, BusinessPhone.phone_id == Phone.id).where(
                        BusinessPhone.business_id == b.id
                    )
                ).scalars()
            ]
            views.append(
                BusinessView(
                    id=b.id,
                    name=b.name,
                    name_key=b.name_key,
                    city_name=b.city_name,
                    address=b.address,
                    domain=b.domain,
                    phones=phones,
                )
            )
        return views

    # ------------------------------------------------------------------ #
    def run(
        self,
        *,
        job_id: int | None = None,
        batch_size: int = 3000,
        include_synthetic: bool = True,
        review_only: bool = False,
    ) -> DedupReport:
        started = time.monotonic()
        report = DedupReport()
        statuses = (BusinessStatus.ACTIVE.value, BusinessStatus.QUARANTINED.value)
        views = self._load_views(batch_size=batch_size, statuses=statuses)
        report.scanned = len(views)
        if len(views) < 2:
            report.duration_ms = int((time.monotonic() - started) * 1000)
            return report

        scores = {i: (v.id or 0) for i, v in enumerate(views)}
        del scores
        confidences = {
            b.id: b.confidence
            for b in self.session.execute(
                select(Business).where(Business.id.in_([v.id for v in views]))
            ).scalars()
        }
        score_map = {i: confidences.get(v.id, 0) for i, v in enumerate(views)}

        clusters, review_pairs = find_duplicate_clusters(views, scores=score_map, settings=self.settings)
        report.clusters = len(clusters)
        report.review_pairs = len(review_pairs)

        if review_only:
            report.details = [
                {"a": views[a].name, "b": views[b].name, "score": round(s, 3), "signals": sig}
                for a, b, s, sig in review_pairs[:200]
            ]
            report.duration_ms = int((time.monotonic() - started) * 1000)
            return report

        for cluster in clusters:
            canonical_view = views[cluster.canonical_index]
            canonical = self.session.get(Business, canonical_view.id)
            if canonical is None:
                continue
            for idx in cluster.members:
                if idx == cluster.canonical_index:
                    continue
                merged = self.session.get(Business, views[idx].id)
                if merged is None or merged.status == BusinessStatus.MERGED.value:
                    continue
                moved_obs, moved_phones = self._merge_into(canonical, merged)
                report.observations_moved += moved_obs
                report.phones_moved += moved_phones
                report.merged += 1
                self.session.add(
                    DuplicateRecord(
                        canonical_id=canonical.id,
                        merged_id=merged.id,
                        reason="; ".join(cluster.reasons) or "شباهت چندسیگنالی",
                        score=cluster.score,
                        signals=cluster.signals,
                        job_id=job_id,
                    )
                )
                report.details.append(
                    {
                        "canonical": canonical.name,
                        "merged": merged.name,
                        "score": round(cluster.score, 3),
                        "signals": cluster.signals,
                    }
                )
            self.session.flush()
            recompute_business(self.session, canonical, settings=self.settings)

        self.session.flush()
        report.duration_ms = int((time.monotonic() - started) * 1000)
        log.info("یکتاسازی: %s رکورد بررسی، %s خوشه، %s ادغام", report.scanned, report.clusters, report.merged)
        return report

    # ------------------------------------------------------------------ #
    def _merge_into(self, canonical: Business, merged: Business) -> tuple[int, int]:
        """انتقال شواهد و شماره‌های رکورد تکراری به رکورد کانونیکال (بدون حذف داده)."""
        observations = (
            self.session.execute(
                select(SourceObservation).where(SourceObservation.business_id == merged.id)
            )
            .scalars()
            .all()
        )
        for obs in observations:
            obs.business_id = canonical.id

        canonical_phones = {
            link.phone_id
            for link in self.session.execute(
                select(BusinessPhone).where(BusinessPhone.business_id == canonical.id)
            ).scalars()
        }
        links = (
            self.session.execute(select(BusinessPhone).where(BusinessPhone.business_id == merged.id))
            .scalars()
            .all()
        )
        moved_phones = 0
        for link in links:
            if link.phone_id in canonical_phones:
                self.session.delete(link)
            else:
                link.business_id = canonical.id
                link.is_primary = False
                moved_phones += 1
                canonical_phones.add(link.phone_id)

        # تکمیل فیلدهای خالی رکورد کانونیکال از رکورد ادغام‌شده
        for attr in ("name", "address", "website", "domain", "owner_name", "city_name",
                     "county_name", "province_name", "description"):
            if getattr(canonical, attr) in (None, "", []) and getattr(merged, attr) not in (None, "", []):
                setattr(canonical, attr, getattr(merged, attr))
        if (merged.relevance_score or 0) > (canonical.relevance_score or 0):
            canonical.relevance_score = merged.relevance_score
            canonical.relevance_evidence = merged.relevance_evidence
        if merged.categories:
            canonical.categories = sorted(set(canonical.categories or []) | set(merged.categories))
        if merged.social_links:
            canonical.social_links = list(
                dict.fromkeys((canonical.social_links or []) + merged.social_links)
            )[:10]

        merged.status = BusinessStatus.MERGED.value
        merged.last_seen_at = merged.last_seen_at or utcnow()

        # ثبت مکان رکورد کانونیکال در صورت نبود
        if canonical.city_id is None and merged.city_id is not None:
            canonical.city_id = merged.city_id
            canonical.county_id = canonical.county_id or merged.county_id
            canonical.province_id = canonical.province_id or merged.province_id

        self.session.flush()
        return len(observations), moved_phones


def dedup_stats(session: Session) -> dict:
    total = session.query(DuplicateRecord).count()
    recent = (
        session.query(DuplicateRecord)
        .order_by(DuplicateRecord.created_at.desc())
        .limit(10)
        .all()
    )
    return {
        "total_duplicate_records": total,
        "recent": [
            {
                "canonical_id": r.canonical_id,
                "merged_id": r.merged_id,
                "score": r.score,
                "reason": r.reason,
                "at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in recent
        ],
    }
