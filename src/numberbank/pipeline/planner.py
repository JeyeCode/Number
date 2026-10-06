"""برنامه‌ریز پرس‌وجو و پوشش جغرافیایی.

مغز سامانه برای «هر اجرا نتیجه تازه»:
  ۱) ترکیب‌های استفاده‌نشده (شکل‌های پرس‌وجوی جدید) اجرا می‌شوند.
  ۲) ترکیب‌های اجراشده یک صفحه عمیق‌تر می‌روند (تا سقف مجاز).
  ۳) ترکیب‌های اشباع‌شده (بدون نتیجه جدید) هرگز دوباره اجرا نمی‌شوند.
  ۴) منابع تازه‌فعال‌شده، همان پرس‌وجوها را از دید جدید بررسی می‌کنند.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..domain.enums import BusinessType, CategoryCode, QueryState
from ..domain.models import Business, Location, SearchQuery
from ..logging_setup import get_logger
from ..relevance.lexicon import QUERY_KEYWORDS, QUERY_ROLE_KEYWORDS
from ..text.normalize import normalize_fa, search_text

log = get_logger("planner")

# شکل‌های پرس‌وجو — ترتیب اهمیت دارد (اولین‌ها بازده بالاتری دارند)
QUERY_SHAPES: list[str] = [
    "{role} {category} {city}",
    "{category} {city}",
    "{role} {category} {city} {province}",
    "{category} {city} خرید عمده",
    "{role} {category} {county}",
    "{category} {city} پخش سراسری",
    "{role} {category} {city} شماره تماس",
    "{category} {city} لیست قیمت",
]

QUERY_SHAPES_EXTRA: list[str] = [
    "{category} {city} نمایندگی",
    "{role} {category} {city} آدرس",
    "{category} {city} انبار",
    "{role} {category} {city} واتساپ",
]


@dataclass
class PlannedTask:
    """یک واحد کار اجرایی: (پرس‌وجو، منبع، صفحه)."""

    query_text: str
    source_key: str
    page: int = 1
    province: str | None = None
    county: str | None = None
    city: str | None = None
    category: str | None = None
    business_type: str | None = None
    shape_index: int = 0
    is_new: bool = True
    query_id: int | None = None


@dataclass
class CityScope:
    city: str
    county: str | None = None
    province: str | None = None
    existing_businesses: int = 0


@dataclass
class PlanSummary:
    tasks: list[PlannedTask] = field(default_factory=list)
    total_combinations: int = 0
    new_combinations: int = 0
    deeper_queries: int = 0
    skipped_exhausted: int = 0


class QueryPlanner:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------ #
    def resolve_scope(
        self,
        *,
        provinces: list[str] | None = None,
        counties: list[str] | None = None,
        cities: list[str] | None = None,
        limit_cities: int | None = None,
        prefer_small_cities: bool = True,
    ) -> list[CityScope]:
        """تعیین دامنه جغرافیایی از جدول مکان‌ها (استان → شهرستان → شهر)."""
        query = select(Location).where(Location.kind == "city")
        norm = lambda xs: {normalize_fa(x) for x in (xs or [])}  # noqa: E731

        prov_set, county_set, city_set = norm(provinces), norm(counties), norm(cities)
        scopes: list[CityScope] = []
        province_names: dict[int, str] = {
            row.id: row.name for row in self.session.execute(select(Location).where(Location.kind == "province")).scalars()
        }
        county_names: dict[int, str] = {
            row.id: row.name for row in self.session.execute(select(Location).where(Location.kind == "county")).scalars()
        }

        counts = dict(
            self.session.execute(
                select(Business.city_name, func.count(Business.id))
                .where(Business.status == "ACTIVE", Business.city_name.is_not(None))
                .group_by(Business.city_name)
            ).all()
        )

        for row in self.session.execute(query).scalars():
            pname = province_names.get(row.province_id)
            cname = county_names.get(row.county_id)
            if prov_set and normalize_fa(pname or "") not in prov_set:
                continue
            if county_set and normalize_fa(cname or "") not in county_set:
                continue
            if city_set and normalize_fa(row.name) not in city_set:
                continue
            scopes.append(
                CityScope(city=row.name, county=cname, province=pname,
                          existing_businesses=int(counts.get(row.name, 0)))
            )

        # تمرکز بر شهرهای کوچک: شهرهای با کمترین داده موجود اول
        if prefer_small_cities:
            scopes.sort(key=lambda s: (s.existing_businesses, len(s.city), s.city))
        else:
            scopes.sort(key=lambda s: (-s.existing_businesses, s.city))

        if limit_cities:
            scopes = scopes[:limit_cities]
        return scopes

    # ------------------------------------------------------------------ #
    def build_tasks(
        self,
        *,
        scopes: list[CityScope],
        categories: list[str],
        business_types: list[str],
        source_keys: list[str],
        max_pages: int | None = None,
        budget: int | None = None,
        allow_refresh_days: int | None = None,
        include_extra_shapes: bool = True,
        jitter: bool = True,
    ) -> PlanSummary:
        """تولید وظایف با احترام به دفتر پرس‌وجوها (بدون تکرار)."""
        settings = self.settings
        max_pages = max_pages or settings.max_pages_per_query
        budget = budget or settings.max_pages_per_job
        refresh_days = allow_refresh_days if allow_refresh_days is not None else settings.query_refresh_days
        now = datetime.utcnow()

        ledger_rows = {
            (r.source_key, r.query_hash): r for r in self.session.execute(select(SearchQuery)).scalars()
        }

        shapes = list(QUERY_SHAPES) + (QUERY_SHAPES_EXTRA if include_extra_shapes else [])
        summary = PlanSummary()
        candidates: list[PlannedTask] = []

        categories = categories or [CategoryCode.DETERGENT.value]
        business_types = business_types or [
            BusinessType.WHOLESALER.value,
            BusinessType.DISTRIBUTOR.value,
            BusinessType.RETAILER.value,
        ]

        for scope in scopes:
            for category in categories:
                cat_kw = QUERY_KEYWORDS.get(category) or [CategoryCode(category).label_fa]
                for role in business_types:
                    role_kws = QUERY_ROLE_KEYWORDS.get(role) or [""]
                    summary.total_combinations += 1
                    for shape_index, shape in enumerate(shapes):
                        for cat_term in cat_kw[:2]:
                            for role_term in role_kws[:2] if role_kws else [""]:
                                text = self._render(
                                    shape,
                                    role=role_term or "فروش",
                                    category=cat_term,
                                    city=scope.city,
                                    county=scope.county or scope.city,
                                    province=scope.province or "",
                                )
                                text = normalize_fa(text, zwnj="space").strip()
                                if not text:
                                    continue
                                task = PlannedTask(
                                    query_text=text,
                                    source_key="",
                                    province=scope.province,
                                    county=scope.county,
                                    city=scope.city,
                                    category=category,
                                    business_type=role,
                                    shape_index=shape_index,
                                )
                                candidates.append(task)

        # حذف ترکیب‌های تکراری متن پرس‌وجو
        seen_texts: set[str] = set()
        unique_candidates: list[PlannedTask] = []
        for task in candidates:
            key = search_text(task.query_text)
            if key in seen_texts:
                continue
            seen_texts.add(key)
            unique_candidates.append(task)

        if jitter:  # جلوگیری از ترتیب همیشه‌یکسان بین اجراها
            random.shuffle(unique_candidates)

        selected: list[PlannedTask] = []
        for task in unique_candidates:
            if len(selected) >= budget:
                break
            for source_key in source_keys:
                from ..dedup.fingerprint import query_fingerprint

                qhash = query_fingerprint(source_key, task.query_text)
                row = ledger_rows.get((source_key, qhash))
                if row is None:
                    selected.append(self._task(task, source_key, page=1, is_new=True))
                    summary.new_combinations += 1
                    break
                if row.state == QueryState.BLOCKED.value:
                    summary.skipped_exhausted += 1
                    continue
                if row.state == QueryState.DONE.value and (row.new_results or 0) == 0 and row.run_count >= 1:
                    # اشباع‌شده: فقط اگر بازه تازه‌سازی گذشته باشد
                    due = row.next_run_at is not None and row.next_run_at <= now
                    if not due:
                        summary.skipped_exhausted += 1
                        continue
                    selected.append(self._task(task, source_key, page=1, is_new=False, query_id=row.id))
                    break
                if row.depth < max_pages:
                    due = row.next_run_at is None or row.next_run_at <= now
                    if not due:
                        continue
                    selected.append(
                        self._task(task, source_key, page=row.depth + 1, is_new=False, query_id=row.id)
                    )
                    summary.deeper_queries += 1
                    break
                # عمق کامل ⇒ بررسی تازه‌سازی
                last = row.last_run_at or row.created_at
                if last and (now - last) >= timedelta(days=refresh_days):
                    selected.append(self._task(task, source_key, page=1, is_new=False, query_id=row.id))
                    break
                summary.skipped_exhausted += 1

        summary.tasks = selected
        log.info(
            "برنامه‌ریزی: %s وظیفه (ترکیب جدید=%s، عمق بیشتر=%s، اشباع‌شده=%s) از %s ترکیب",
            len(selected), summary.new_combinations, summary.deeper_queries,
            summary.skipped_exhausted, summary.total_combinations,
        )
        return summary

    @staticmethod
    def _render(shape: str, **kwargs) -> str:
        try:
            return shape.format(**{k: (v or "").strip() for k, v in kwargs.items()})
        except (KeyError, IndexError):
            return ""

    @staticmethod
    def _task(task: PlannedTask, source_key: str, *, page: int, is_new: bool, query_id: int | None = None) -> PlannedTask:
        return PlannedTask(
            query_text=task.query_text,
            source_key=source_key,
            page=page,
            province=task.province,
            county=task.county,
            city=task.city,
            category=task.category,
            business_type=task.business_type,
            shape_index=task.shape_index,
            is_new=is_new,
            query_id=query_id,
        )

    # ------------------------------------------------------------------ #
    def coverage_report(self, *, provinces: list[str] | None = None) -> dict:
        """گزارش پوشش: شهرهای بررسی‌شده/باقی‌مانده به‌ازای هر دسته."""
        city_rows = list(
            self.session.execute(select(Location).where(Location.kind == "city")).scalars()
        )
        province_names = {
            r.id: r.name for r in self.session.execute(select(Location).where(Location.kind == "province")).scalars()
        }
        norm_provs = {normalize_fa(p) for p in (provinces or [])}
        total_cities = 0
        for row in city_rows:
            pname = province_names.get(row.province_id)
            if norm_provs and normalize_fa(pname or "") not in norm_provs:
                continue
            total_cities += 1

        queried_cities = {
            r for (r,) in self.session.execute(select(SearchQuery.city_name).distinct()) if r
        }
        discovered_cities = {
            r for (r,) in self.session.execute(
                select(Business.city_name).where(Business.status == "ACTIVE").distinct()
            ) if r
        }
        with_data = {r for (r,) in self.session.execute(
            select(Business.city_name).where(Business.status == "ACTIVE",
                                             Business.confidence >= self.settings.min_confidence).distinct()
        ) if r}

        # تفکیک استانی برای داشبورد پوشش
        counties_by_province: dict[str, int] = {}
        cities_by_province: dict[str, int] = {}
        for row in self.session.execute(select(Location).where(Location.kind.in_(["county", "city"]))).scalars():
            pname = province_names.get(row.province_id)
            if not pname:
                continue
            target = counties_by_province if row.kind == "county" else cities_by_province
            target[pname] = target.get(pname, 0) + 1
        businesses_by_province = {
            r: int(c) for r, c in self.session.execute(
                select(Business.province_name, func.count(Business.id))
                .where(Business.status == "ACTIVE")
                .group_by(Business.province_name)
            ).all()
        }
        datasets_by_province = {
            r: int(c) for r, c in self.session.execute(
                select(Business.province_name, func.count(func.distinct(Business.city_name)))
                .where(Business.status == "ACTIVE")
                .group_by(Business.province_name)
            ).all()
        }
        by_province = sorted(
            (
                {
                    "province": name,
                    "counties": counties_by_province.get(name, 0),
                    "cities": cities_by_province.get(name, 0),
                    "cities_with_data": datasets_by_province.get(name, 0),
                    "businesses": businesses_by_province.get(name, 0),
                    "coverage_percent": round(
                        100.0 * datasets_by_province.get(name, 0) / cities_by_province[name], 2
                    ) if cities_by_province.get(name) else 0.0,
                }
                for name in cities_by_province
            ),
            key=lambda r: (-r["businesses"], r["province"]),
        )

        return {
            "total_cities": total_cities,
            "by_province": by_province,
            "queried_cities": len(queried_cities),
            "cities_with_data": len(discovered_cities),
            "cities_with_valid_data": len(with_data),
            "remaining_cities": max(0, total_cities - len(queried_cities)),
            "coverage_percent": round(100.0 * len(queried_cities) / total_cities, 2) if total_cities else 0.0,
            "by_category": {
                code: int(
                    self.session.execute(
                        select(func.count(Business.id)).where(Business.primary_category == code,
                                                               Business.status == "ACTIVE")
                    ).scalar()
                    or 0
                )
                for code in (CategoryCode.DETERGENT.value, CategoryCode.HYGIENE.value,
                             CategoryCode.COSMETIC.value, CategoryCode.CELLULOSE.value)
            },
        }
