"""خروجی گرفتن از داده‌ها: CSV، Excel (XLSX) و JSON با فیلترهای کامل."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..config import Settings, get_settings
from ..db.repositories import BusinessFilters, businesses_for_export
from ..db.session import Database, get_database
from ..domain.models import ExportRecord
from ..logging_setup import get_logger

log = get_logger("export")

EXPORT_COLUMNS = [
    ("id", "شناسه"),
    ("name", "نام کسب‌وکار"),
    ("owner_name", "نام مالک/مسئول"),
    ("owner_status", "وضعیت نام مالک"),
    ("province_name", "استان"),
    ("county_name", "شهرستان"),
    ("city_name", "شهر"),
    ("address", "آدرس"),
    ("address_status", "وضعیت آدرس"),
    ("primary_phone", "شماره ثابت"),
    ("mobile_phone", "شماره موبایل تجاری"),
    ("other_phones", "سایر شماره‌ها"),
    ("phone_status", "وضعیت شماره"),
    ("phone_type", "نوع شماره"),
    ("website", "وب‌سایت"),
    ("domain", "دامنه"),
    ("owner_conflict", "وضعیت تعارض"),
    ("business_type", "نوع کسب‌وکار"),
    ("categories", "دسته‌بندی فعالیت"),
    ("primary_category", "دسته اصلی"),
    ("source_count", "تعداد منابع"),
    ("independent_source_count", "تعداد منابع مستقل"),
    ("source_keys", "منابع کشف"),
    ("evidence_urls", "لینک منابع (Evidence)"),
    ("first_evidence_url", "لینک منبع اصلی"),
    ("relevance_score", "امتیاز ارتباط"),
    ("confidence", "امتیاز اعتبار"),
    ("confidence_band", "سطح اعتبار"),
    ("status", "وضعیت رکورد"),
    ("field_status", "وضعیت فیلدها"),
    ("discovered_at", "تاریخ کشف"),
    ("last_seen_at", "تاریخ آخرین مشاهده"),
    ("last_validated_at", "تاریخ آخرین اعتبارسنجی"),
    ("is_duplicate_merged", "رکورد تکراری ادغام‌شده"),
    ("is_synthetic", "داده آزمایشی"),
    ("confidence_breakdown", "ریز امتیاز اعتبار"),
    ("description", "توضیحات"),
]


@dataclass
class ExportReport:
    path: str
    fmt: str
    rows: int
    bytes_written: int
    filters: dict[str, Any]

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "format": self.fmt,
            "rows": self.rows,
            "bytes": self.bytes_written,
            "filters": self.filters,
        }


def _row_for(business, phones, *, evidence_by_business: dict[int, list[dict]] | None = None) -> dict:
    landlines = [p.national for p in phones if p.type == "LANDLINE"]
    mobiles = [p.national for p in phones if p.type == "MOBILE"]
    others = [p.national for p in phones if p.type not in ("LANDLINE", "MOBILE")]
    primary_status = phones[0].status if phones else "NOT_FOUND"
    primary_type = phones[0].type if phones else ""
    evidence = (evidence_by_business or {}).get(business.id, [])
    urls = [e["url"] for e in evidence if e.get("url")]
    sources = sorted({e.get("source_key") for e in evidence if e.get("source_key")})
    field_status = business.field_status or {}
    conflicted = [k for k, v in field_status.items() if v == "CONFLICTED"]
    return {
        "id": business.id,
        "name": business.name or "NOT_FOUND",
        "owner_name": business.owner_name or "NOT_FOUND",
        "owner_status": business.owner_status,
        "province_name": business.province_name or "NOT_FOUND",
        "county_name": business.county_name or "NOT_FOUND",
        "city_name": business.city_name or "NOT_FOUND",
        "address": business.address or "NOT_FOUND",
        "address_status": business.address_status,
        "primary_phone": ", ".join(landlines) or "NOT_FOUND",
        "mobile_phone": ", ".join(mobiles) or "NOT_FOUND",
        "other_phones": ", ".join(others) or "",
        "phone_status": primary_status,
        "phone_type": primary_type,
        "website": business.website or "NOT_FOUND",
        "domain": business.domain or "",
        "owner_conflict": ("CONFLICTED" if conflicted else "OK"),
        "business_type": business.business_type or "UNKNOWN",
        "categories": ", ".join(business.categories or []) or "NOT_FOUND",
        "primary_category": business.primary_category or "NOT_FOUND",
        "source_count": business.source_count,
        "independent_source_count": business.independent_source_count,
        "source_keys": ", ".join(sources),
        "evidence_urls": " | ".join(urls[:5]),
        "first_evidence_url": urls[0] if urls else "NOT_FOUND",
        "relevance_score": business.relevance_score,
        "confidence": business.confidence,
        "confidence_band": business.confidence_band,
        "status": business.status,
        "field_status": json.dumps(field_status, ensure_ascii=False),
        "discovered_at": business.discovered_at.isoformat() if business.discovered_at else "",
        "last_seen_at": business.last_seen_at.isoformat() if business.last_seen_at else "",
        "last_validated_at": business.last_validated_at.isoformat() if business.last_validated_at else "",
        "is_duplicate_merged": "YES" if business.status == "MERGED" else "NO",
        "is_synthetic": "YES" if business.is_synthetic else "NO",
        "confidence_breakdown": json.dumps(business.confidence_breakdown or {}, ensure_ascii=False),
        "description": (business.description or "")[:500],
    }


def _collect_evidence(session, business_ids: list[int]) -> dict[int, list[dict]]:
    from ..domain.models import Source, SourceObservation

    if not business_ids:
        return {}
    rows = session.execute(
        select(SourceObservation, Source)
        .join(Source, Source.id == SourceObservation.source_id)
        .where(SourceObservation.business_id.in_(business_ids))
    ).all()
    out: dict[int, list[dict]] = {}
    for obs, src in rows:
        out.setdefault(obs.business_id, []).append(
            {"url": obs.url, "source_key": src.key, "kind": obs.evidence_kind,
             "observed_at": obs.observed_at.isoformat() if obs.observed_at else None,
             "snippet": (obs.snippet or "")[:300]}
        )
    return out


def export_businesses(
    filters: BusinessFilters,
    *,
    fmt: str = "csv",
    database: Database | None = None,
    settings: Settings | None = None,
    output_path: str | Path | None = None,
) -> ExportReport:
    settings = settings or get_settings()
    database = database or get_database(settings)
    fmt = fmt.lower()
    if fmt not in ("csv", "xlsx", "excel", "json"):
        raise ValueError("فرمت پشتیبانی‌شده: csv | xlsx | json")
    if fmt == "excel":
        fmt = "xlsx"

    export_dir = Path(settings.export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if output_path is None:
        output_path = export_dir / f"numberbank-{stamp}.{fmt}"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rows_written = 0
    with database.session() as session:
        if fmt == "json":
            rows: list[dict] = []
            for business, phones in businesses_for_export(session, filters):
                rows.append(_row_for(business, phones))
                rows_written += 1
            output_path.write_text(
                json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        elif fmt == "csv":
            with output_path.open("w", encoding="utf-8-sig", newline="") as fh:
                writer = csv.DictWriter(
                    fh, fieldnames=[k for k, _ in EXPORT_COLUMNS], extrasaction="ignore"
                )
                writer.writeheader()
                for business, phones in businesses_for_export(session, filters):
                    writer.writerow(_row_for(business, phones))
                    rows_written += 1
        else:  # xlsx
            try:
                from openpyxl import Workbook
                from openpyxl.styles import Font
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "برای خروجی Excel بسته openpyxl لازم است: pip install 'numberbank[excel]'"
                ) from exc
            wb = Workbook(write_only=False)
            ws = wb.active
            ws.title = "Businesses"
            ws.sheet_view.rightToLeft = True
            ws.append([label for _, label in EXPORT_COLUMNS])
            for cell in ws[1]:
                cell.font = Font(bold=True)
            for business, phones in businesses_for_export(session, filters):
                row = _row_for(business, phones)
                ws.append([row.get(key, "") for key, _ in EXPORT_COLUMNS])
                rows_written += 1
            wb.save(output_path)

        session.add(
            ExportRecord(
                path=str(output_path),
                fmt=fmt,
                filters=_filters_as_dict(filters),
                row_count=rows_written,
            )
        )

    size = output_path.stat().st_size if output_path.exists() else 0
    log.info("خروجی %s ساخته شد: %s ردیف (%s بایت)", fmt, rows_written, size)
    return ExportReport(path=str(output_path), fmt=fmt, rows=rows_written, bytes_written=size,
                        filters=_filters_as_dict(filters))


def export_evidence(filters: BusinessFilters, *, database: Database | None = None,
                    settings: Settings | None = None, output_path: str | Path | None = None) -> ExportReport:
    """خروجی جداگانه شواهد (URL، منبع، تاریخ، متن استخراج‌شده) برای بازبینی انسانی."""
    settings = settings or get_settings()
    database = database or get_database(settings)
    output_path = Path(output_path or (Path(settings.export_dir) /
                                       f"evidence-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    with database.session() as session:
        from ..db.repositories import list_businesses

        businesses = list_businesses(session, filters)
        evidence = _collect_evidence(session, [b.id for b in businesses])
        names = {b.id: b.name for b in businesses}
        with output_path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["business_id", "business_name", "source_key", "kind", "url", "observed_at", "snippet"])
            for business_id, items in evidence.items():
                for item in items:
                    writer.writerow([
                        business_id, names.get(business_id, ""), item["source_key"], item["kind"],
                        item["url"], item["observed_at"], item["snippet"],
                    ])
                    rows += 1
    return ExportReport(path=str(output_path), fmt="csv", rows=rows,
                        bytes_written=output_path.stat().st_size if output_path.exists() else 0,
                        filters=_filters_as_dict(filters))


def _filters_as_dict(filters: BusinessFilters) -> dict:
    return {
        k: (v.isoformat() if isinstance(v, datetime) else v)
        for k, v in filters.__dict__.items()
    }


def to_dataframe_rows(filters: BusinessFilters, *, database: Database | None = None,
                      settings: Settings | None = None) -> list[dict]:
    """ردیف‌های آماده برای API/داشبورد (بدون فایل)."""
    settings = settings or get_settings()
    database = database or get_database(settings)
    out: list[dict] = []
    with database.session() as session:
        businesses = []
        for business, phones in businesses_for_export(session, filters):
            businesses.append((business, phones))
        ids = [b.id for b, _ in businesses]
        evidence = _collect_evidence(session, ids)
        for business, phones in businesses:
            row = _row_for(business, phones, evidence_by_business=evidence)
            row["phones_detail"] = [
                {
                    "number": p.national,
                    "e164": p.e164,
                    "type": p.type,
                    "status": p.status,
                    "confidence": p.confidence,
                }
                for p in phones
            ]
            out.append(row)
    return out
