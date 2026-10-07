"""داده‌های مرجع: بارگذاری مکان/دسته/منبع، دریافت داده کامل جغرافیایی، قالب‌های پیکربندی."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select

from ..config import Settings, get_settings
from ..db.session import Database, get_database
from ..domain.enums import BusinessType, CategoryCode
from ..domain.models import Category, Location
from ..geo.gazetteer import Gazetteer, get_gazetteer, reset_gazetteer_cache
from ..logging_setup import get_logger
from ..relevance.lexicon import (
    BUSINESS_SIGNALS,
    CATEGORY_TERMS,
    NEGATIVE_TERMS,
    ROLE_TERMS,
)
from ..sources.registry import SourceRegistry, sync_sources_to_db
from ..text.normalize import name_key

log = get_logger("reference")

OFFICIAL_DATASET_BASE = "https://raw.githubusercontent.com/sajaddp/list-of-cities-in-Iran/main/dist/json"
OFFICIAL_DATASET_API = "https://api.github.com/repos/sajaddp/list-of-cities-in-Iran/contents/dist/json"


@dataclass
class SeedReport:
    provinces: int = 0
    counties: int = 0
    cities: int = 0
    categories: int = 0
    sources: int = 0
    dataset_version: str | None = None

    def as_dict(self) -> dict:
        return self.__dict__.copy()


# --------------------------------------------------------------------------- #
def seed_locations(database: Database, gazetteer: Gazetteer | None = None) -> tuple[int, int, int]:
    """بارگذاری درخت مکان‌ها در جدول ``locations`` (ادغام‌پذیر و تکرارپذیر)."""
    gz = gazetteer or get_gazetteer()
    provinces = [loc for loc in gz.locations if loc.kind == "province"]
    counties = [loc for loc in gz.locations if loc.kind == "county"]
    cities = [loc for loc in gz.locations if loc.kind == "city"]

    with database.session() as session:
        existing = {
            f"{row.kind}:{row.name_key}": row
            for row in session.execute(select(Location)).scalars()
        }
        id_map: dict[int, int] = {}

        def upsert(loc, kind: str, parent_id: int | None, official_id: str) -> int:
            key = f"{kind}:{loc.name_key}"
            row = existing.get(key)
            if row is None:
                row = Location(
                    kind=kind,
                    name=loc.name,
                    name_key=loc.name_key,
                    official_id=official_id,
                    area_codes=[loc.province_area_code] if loc.province_area_code else [],
                    dataset_version=gz.dataset_version,
                )
                session.add(row)
                session.flush()
                existing[key] = row
            else:
                row.name = loc.name
                row.dataset_version = gz.dataset_version
                if loc.province_area_code:
                    row.area_codes = [loc.province_area_code]
            if parent_id is not None:
                if kind == "county":
                    row.province_id = parent_id
                elif kind == "city":
                    row.county_id = parent_id
            return row.id

        prov_ids: dict[str, int] = {}
        for loc in provinces:
            official = f"p:{loc.name_key}"
            row_id = upsert(loc, "province", None, official)
            id_map[loc.id] = row_id
            prov_ids[loc.name] = row_id

        county_ids: dict[tuple[str, str], int] = {}
        for loc in counties:
            parent = prov_ids.get(loc.province or "")
            official = f"c:{loc.province_key if hasattr(loc, 'province_key') else loc.province}:{loc.name_key}"
            row_id = upsert(loc, "county", parent, official)
            id_map[loc.id] = row_id
            county_ids[(loc.province or "", loc.name)] = row_id

        for loc in cities:
            parent = county_ids.get((loc.province or "", loc.county or "")) or prov_ids.get(loc.province or "")
            official = f"city:{loc.province}:{loc.county}:{loc.name_key}"
            row_id = upsert(loc, "city", parent, official)
            id_map[loc.id] = row_id

        # تکمیل ارجاع استان/شهرستان برای شهرها
        for loc in cities:
            row = existing.get(f"city:{loc.name_key}")
            if row is None:
                continue
            row.province_id = row.province_id or prov_ids.get(loc.province or "")
            row.county_id = row.county_id or county_ids.get((loc.province or "", loc.county or ""))
        session.flush()

    return len(provinces), len(counties), len(cities)


def seed_categories(database: Database) -> int:
    with database.session() as session:
        existing = {row.code: row for row in session.execute(select(Category)).scalars()}
        count = 0
        for code in (CategoryCode.DETERGENT.value, CategoryCode.HYGIENE.value,
                     CategoryCode.COSMETIC.value, CategoryCode.CELLULOSE.value):
            terms = [{"term": t, "weight": w} for t, w in CATEGORY_TERMS.get(code, [])]
            row = existing.get(code)
            if row is None:
                session.add(
                    Category(
                        code=code,
                        name_fa=CategoryCode(code).label_fa,
                        terms=terms,
                        role_terms=[{"code": k, "terms": [{"term": t, "weight": w} for t, w in v]}
                                    for k, v in ROLE_TERMS.items()],
                        negative_terms=[{"term": t, "weight": w} for t, w in NEGATIVE_TERMS],
                    )
                )
            else:
                row.terms = terms
                row.name_fa = CategoryCode(code).label_fa
            count += 1
        # دسته سایر (واژگان عمومی)
        if "OTHER" not in existing:
            session.add(
                Category(
                    code="OTHER",
                    name_fa="سایر",
                    terms=[{"term": t, "weight": w} for t, w in BUSINESS_SIGNALS][:10],
                    role_terms=[],
                    negative_terms=[],
                )
            )
        count += 1
    return count


def seed_all(database: Database | None = None, *, settings: Settings | None = None,
             gazetteer: Gazetteer | None = None) -> SeedReport:
    settings = settings or get_settings()
    database = database or get_database(settings)
    gz = gazetteer or get_gazetteer()
    p, c, ci = seed_locations(database, gz)
    cats = seed_categories(database)
    registry = SourceRegistry(settings)
    sources = sync_sources_to_db(registry, database)
    write_directories_template(settings)
    write_categories_template(settings)
    write_blocklist_template(settings)
    report = SeedReport(provinces=p, counties=c, cities=ci, categories=cats, sources=sources,
                        dataset_version=gz.dataset_version)
    log.info("بارگذاری داده مرجع: %s استان، %s شهرستان، %s شهر، %s منبع",
             report.provinces, report.counties, report.cities, report.sources)
    return report


# --------------------------------------------------------------------------- #
async def fetch_official_geo(settings: Settings | None = None, *, write_full: bool = True) -> dict:
    """دریافت داده کامل تقسیمات کشوری از مخزن عمومی (مجوز GPL-3.0، ارجاع الزامی)."""
    import httpx

    settings = settings or get_settings()
    ref_dir = Path(settings.data_dir) / "reference"
    raw_dir = ref_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    files = {"provinces": "provinces.json", "counties": "counties.json", "cities": "cities.json"}
    downloaded: dict[str, list] = {}
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True,
                                 headers={"User-Agent": settings.user_agent}) as client:
        for key, filename in files.items():
            url = f"{OFFICIAL_DATASET_BASE}/{filename}"
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                log.warning("دریافت %s ناموفق (%s) — تلاش با GitHub API", filename, exc)
                api_url = f"{OFFICIAL_DATASET_API}/{filename}"
                resp = await client.get(api_url)
                resp.raise_for_status()
                import base64

                payload = resp.json()
                data = json.loads(base64.b64decode(payload["content"]).decode("utf-8"))
            downloaded[key] = data
            (raw_dir / filename).write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )

    provinces = downloaded["provinces"]
    counties = downloaded["counties"]
    cities = downloaded["cities"]
    pro_map = {p["id"]: p for p in provinces}

    full = {
        "meta": {
            "dataset_version": "official-1404",
            "source": "sajaddp/list-of-cities-in-Iran (GPL-3.0) — منبع اصلی: مرکز آمار ایران، لیست تقسیمات کشوری سال ۱۴۰۴",
            "source_url": "https://github.com/sajaddp/list-of-cities-in-Iran",
            "license": "GPL-3.0",
            "attribution_required": True,
            "counts": {"provinces": len(provinces), "counties": len(counties), "cities": len(cities)},
        },
        "provinces": [
            {"id": p["id"], "name": p["name"], "area_code": p.get("tel_prefix")} for p in provinces
        ],
        "counties": [
            {"id": c["id"], "name": c["name"], "province_id": c["province_id"]} for c in counties
        ],
        "cities": [
            {"id": c["id"], "name": c["name"], "province_id": c["province_id"], "county_id": c["county_id"]}
            for c in cities
        ],
    }
    if write_full:
        (ref_dir / "full_locations.json").write_text(
            json.dumps(full, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    # بارگذاری کامل در دیتابیس (ادغام با داده فعلی)
    database = get_database(settings)
    gz = Gazetteer()
    gz.dataset_version = full["meta"]["dataset_version"]
    from ..geo.gazetteer import LocationInfo

    counter = 1
    for p in full["provinces"]:
        gz.add(LocationInfo(id=counter, kind="province", name=p["name"], name_key=name_key(p["name"]),
                            province=p["name"], province_area_code=p["area_code"]))
        counter += 1
    for c in full["counties"]:
        prov = pro_map.get(c["province_id"], {})
        gz.add(LocationInfo(id=counter, kind="county", name=c["name"], name_key=name_key(c["name"]),
                            province=prov.get("name"), county=c["name"],
                            province_area_code=prov.get("tel_prefix")))
        counter += 1
    for c in full["cities"]:
        prov = pro_map.get(c["province_id"], {})
        county = next((x for x in counties if x["id"] == c["county_id"]), {})
        gz.add(LocationInfo(id=counter, kind="city", name=c["name"], name_key=name_key(c["name"]),
                            province=prov.get("name"), county=county.get("name"),
                            province_area_code=prov.get("tel_prefix")))
        counter += 1
    report = seed_locations(database, gz)
    reset_gazetteer_cache()
    return {
        "provinces": report[0],
        "counties": report[1],
        "cities": report[2],
        "dataset": full["meta"],
    }


def locations_summary(database: Database | None = None, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    database = database or get_database(settings)
    with database.session() as session:
        rows = dict(
            session.execute(select(Location.kind, func.count(Location.id)).group_by(Location.kind)).all()
        )
        with_area = int(
            session.execute(
                select(func.count(Location.id)).where(Location.kind == "province",
                                                      Location.area_codes.is_not(None))
            ).scalar()
            or 0
        )
    gz = get_gazetteer()
    return {
        "db": {"provinces": int(rows.get("province", 0)), "counties": int(rows.get("county", 0)),
               "cities": int(rows.get("city", 0))},
        "gazetteer": {"provinces": gz.province_count, "counties": gz.county_count, "cities": gz.city_count},
        "provinces_with_area_code": with_area,
        "dataset_version": gz.dataset_version,
        "full_dataset_installed": (Path(settings.data_dir) / "reference" / "full_locations.json").exists(),
    }


# --------------------------------------------------------------------------- #
# قالب‌های پیکربندی (قابل ویرایش توسط کاربر، بدون تغییر کد)
# --------------------------------------------------------------------------- #
def write_directories_template(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    path = Path(settings.data_dir) / "reference" / "directories.json"
    if path.exists():
        return path
    template = [
        {
            "key": "sample_directory",
            "name": "نمونه دایرکتوری تجاری (غیرفعال)",
            "kind": "BUSINESS_DIRECTORY",
            "reliability": 0.7,
            "search_url": "https://example.ir/search?q={query}&page={page}",
            "list_selector": "div.company-item",
            "enabled": False,
            "notes": "این یک نمونه است. برای افزودن منبع واقعی، کپی کنید و مقادیر را پر کنید. "
                     "پیش از استفاده، شرایط استفاده و robots.txt منبع را بررسی کنید.",
        }
    ]
    path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_categories_template(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    path = Path(settings.data_dir) / "reference" / "categories.json"
    if path.exists():
        return path
    template = {
        "note": (
            "واژگان پیش‌فرض در کد تعریف شده‌اند و این فایل فقط آن‌ها را گسترش می‌دهد. "
            "هر ترم باید [«واژه», وزن] باشد. برای جانشینی کامل یک دسته از ساختار "
            "{\"replace\": true, \"terms\": [[...]]} استفاده کنید."
        ),
        "categories": {
            "DETERGENT": [],
            "HYGIENE": [],
            "COSMETIC": [],
            "CELLULOSE": [],
        },
        "roles": {
            "WHOLESALER": [],
            "DISTRIBUTOR": [],
            "RETAILER": [],
            "MANUFACTURER": [],
        },
        "negative": [],
    }
    path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_blocklist_template(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    path = Path(settings.data_dir) / "reference" / "blocklist.txt"
    if not path.exists():
        path.write_text(
            "# دامنه‌ها/الگوهایی که هرگز نباید خزش شوند (هر خط یک الگو)\n"
            "# مثال:\n"
            "# spam-site.example\n",
            encoding="utf-8",
        )
    return path


def load_blocklist(settings: Settings | None = None) -> list[str]:
    settings = settings or get_settings()
    path = Path(settings.data_dir) / "reference" / "blocklist.txt"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def default_business_types() -> list[str]:
    return [BusinessType.WHOLESALER.value, BusinessType.DISTRIBUTOR.value,
            BusinessType.RETAILER.value, BusinessType.MANUFACTURER.value]


def default_categories() -> list[str]:
    return [CategoryCode.DETERGENT.value, CategoryCode.HYGIENE.value,
            CategoryCode.COSMETIC.value, CategoryCode.CELLULOSE.value]
