"""ساخت پیکره آزمایشی آفلاین (Fixture Corpus).

هدف: آزمون واقعی کل زنجیره (جستجو → استخراج → امتیازدهی → یکتاسازی → خروجی)
بدون نیاز به اینترنت، به‌صورت **قطعی** و **تکرارپذیر**.

⚠️ همه داده‌های این پیکره مصنوعی‌اند: نام‌ها ساختگی و شماره‌ها در بلوک رزرو‌شده
``000`` قرار دارند (هیچ شماره واقعی ساخته نمی‌شود). رکوردهای حاصل با پرچم
``is_synthetic`` علامت می‌خورند و در خروجی پیش‌فرض نمایش داده نمی‌شوند.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

from ..config import get_settings
from ..logging_setup import get_logger
from ..text.normalize import normalize_fa

log = get_logger("fixtures")

# --------------------------------------------------------------------------- #
CITIES = [
    {"city": "بافق", "county": "بافق", "province": "یزد", "area": "035"},
    {"city": "مهدی شهر", "county": "مهدی شهر", "province": "سمنان", "area": "023"},
    {"city": "نطنز", "county": "نطنز", "province": "اصفهان", "area": "031"},
    {"city": "کوهدشت", "county": "کوهدشت", "province": "لرستان", "area": "066"},
    {"city": "بندر گز", "county": "بندر گز", "province": "گلستان", "area": "017"},
    {"city": "خاش", "county": "خاش", "province": "سیستان و بلوچستان", "area": "054"},
]

CATEGORIES = [
    {"code": "DETERGENT", "label": "مواد شوینده", "keywords": ["مواد شوینده", "شوینده", "مایع ظرفشویی"]},
    {"code": "HYGIENE", "label": "محصولات بهداشتی", "keywords": ["محصولات بهداشتی", "لوازم بهداشتی"]},
    {"code": "COSMETIC", "label": "لوازم آرایشی و بهداشتی", "keywords": ["لوازم آرایشی و بهداشتی", "آرایشی"]},
    {"code": "CELLULOSE", "label": "محصولات سلولوزی", "keywords": ["محصولات سلولزی", "دستمال کاغذی"]},
]

ROLES = [
    {"code": "WHOLESALER", "label": "عمده فروشی"},
    {"code": "DISTRIBUTOR", "label": "پخش"},
    {"code": "RETAILER", "label": "فروشگاه"},
]

SURNAMES = [
    "رضایی", "کریمی", "احمدی", "موسوی", "حسینی", "نوری", "صادقی", "اکبری",
    "شریفی", "قاسمی", "جعفری", "مرادی", "رحیمی", "زمانی", "کاظمی", "بهرامی",
]

STREETS = ["خیابان امام خمینی", "بلوار شهید بهشتی", "خیابان طالقانی", "میدان مرکزی",
           "بلوار معلم", "خیابان ۲۲ بهمن", "کوی کارگران", "خیابان بازار"]

SPAM_PAGES = [
    ("پخش زنده فوتبال آنلاین", "تماشای آنلاین پخش زنده فوتبال و سریال با لینک دانلود رایگان"),
    ("استخدام منشی خانم در شرکت بازرگانی", "استخدام منشی با رزومه، حقوق مناسب، بیمه"),
    ("خرید فالوور اینستاگرام", "افزایش ممبر و خرید فالوور با تست کانال رایگان"),
    ("سایت شرط بندی معتبر", "شرط بندی آنلاین و بازی انفجاری با پشتیبانی ۲۴ ساعته"),
    ("آموزش سئو و افزایش رتبه", "دوره آموزش سئو، لینک دانلود جزوه و مقاله"),
]

IRRELEVANT_PAGES = [
    ("آموزشگاه رانندگی امید", "آموزش رانندگی پایه سوم و موتورسیکلت با شهریه اقساطی"),
    ("دفتر پیشخوان دولت شهرستان", "خدمات پیشخوان دولت، ثبت نام و امور ثبتی"),
    ("کافه رستوران سنتی", "غذای ایرانی و دستور پخت خانگی، رزرو میز"),
]


@dataclass
class FixtureBuildReport:
    businesses: int = 0
    directory_pages: int = 0
    site_pages: int = 0
    spam_pages: int = 0
    index_entries: int = 0
    root: str = ""

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _landline(area: str, seq: int) -> str:
    """شماره ثابت در بلوک مصنوعی ۰۰۰ (غیرقابل تماس، صرفاً برای تست)."""
    return f"0{area}000{seq:04d}"


def _mobile(seq: int) -> str:
    return f"0912000{seq:04d}"


def _format_landline(raw: str) -> str:
    return f"{raw[:3]}-{raw[3:]}"


def _business_html(item: dict, *, include_owner: bool = False, extra_ceo: bool = False) -> str:
    owner = f"<p>مدیرعامل: {item['owner']}</p>" if include_owner else ""
    return f"""
      <div class="biz-item" data-id="{item['seq']}">
        <h3 class="biz-name">{item['name']}</h3>
        <p class="biz-category">دسته: {item['category_label']} | نوع: {item['role_label']}</p>
        <p class="biz-phone">تلفن: {_format_landline(item['landline'])}</p>
        <p class="biz-mobile">همراه: {item['mobile']}</p>
        <p class="biz-address">آدرس: {item['address']}</p>
        {owner}
      </div>"""


def _directory_page_html(city: dict, category: dict, items: list[dict], *, source_name: str) -> str:
    blocks = "".join(_business_html(i, include_owner=bool(i.get("owner") and i["seq"] % 3 == 0)) for i in items)
    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
  <meta charset="utf-8">
  <title>{category['label']} {city['city']} | {source_name}</title>
  <meta name="description" content="فهرست کسب‌وکارهای {category['label']} در {city['city']} استان {city['province']}">
</head>
<body>
  <header><h1>کسب‌وکارهای {category['label']} در {city['city']}</h1>
  <p>استان {city['province']} — شهرستان {city['county']}</p></header>
  <main class="listing">{blocks}
  </main>
  <footer><p>تماس با ما | ساعات کاری ۹ تا ۱۸</p></footer>
</body>
</html>"""


def _company_site_html(item: dict) -> str:
    jsonld = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "LocalBusiness",
            "name": item["name"],
            "telephone": item["landline"],
            "address": {
                "@type": "PostalAddress",
                "streetAddress": item["address_street"],
                "addressLocality": item["city"],
                "addressRegion": item["province"],
            },
            "url": item["site_url"],
            "openingHours": "Sa-Th 09:00-18:00",
        },
        ensure_ascii=False,
    )
    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
  <meta charset="utf-8">
  <title>{item['name']} | {item['category_label']} در {item['city']}</title>
  <meta property="og:site_name" content="{item['name']}">
  <meta name="description" content="{item['name']}، {item['role_label']} {item['category_label']} در {item['city']}">
  <script type="application/ld+json">{jsonld}</script>
</head>
<body>
  <header>
    <h1>{item['name']}</h1>
    <p>{item['role_label']} {item['category_label']} — ارسال به سراسر کشور</p>
    <a href="tel:{item['landline']}">تماس: {_format_landline(item['landline'])}</a>
    <a href="https://wa.me/98{item['mobile'][1:]}">واتساپ</a>
  </header>
  <main>
    <h2>درباره ما</h2>
    <p>{item['name']} با سال‌ها تجربه در زمینه {item['category_label']}، عرضه عمده و خرده در {item['city']} فعالیت دارد.</p>
    <p>لیست قیمت و کاتالوگ محصولات به‌صورت تلفنی اعلام می‌شود.</p>
  </main>
  <footer>
    <p>آدرس: {item['address']}</p>
    <p>تلفن: {_format_landline(item['landline'])} — همراه: {item['mobile']}</p>
    <p>ساعات کاری: شنبه تا پنجشنبه ۹ تا ۱۸</p>
  </footer>
</body>
</html>"""


def _spam_page_html(title: str, body: str, city: dict) -> str:
    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8"><title>{title}</title></head>
<body><h1>{title}</h1><p>{body}</p>
<p>تماس: {_format_landline(_landline(city['area'], 9999))}</p>
<p>لینک دانلود گلچین و خرید فالوور با تست کانال</p>
</body></html>"""


def _irrelevant_page_html(title: str, body: str, city: dict) -> str:
    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8"><title>{title}</title></head>
<body><h1>{title}</h1><p>{body}</p>
<p>آدرس: {city['city']}، بلوار مرکزی</p>
<p>تلفن: {_format_landline(_landline(city['area'], 8888))}</p>
</body></html>"""


def build_fixtures(*, seed: int = 20261006, root: Path | None = None, force: bool = False) -> FixtureBuildReport:
    settings = get_settings()
    root = root or (Path(settings.data_dir) / "fixtures")
    pages_dir = root / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    index_path = root / "index.json"
    if index_path.exists() and not force:
        try:
            existing = json.loads(index_path.read_text(encoding="utf-8"))
            report = FixtureBuildReport(
                businesses=existing.get("meta", {}).get("businesses", 0),
                directory_pages=existing.get("meta", {}).get("directory_pages", 0),
                site_pages=existing.get("meta", {}).get("site_pages", 0),
                spam_pages=existing.get("meta", {}).get("spam_pages", 0),
                index_entries=len(existing.get("pages", {})),
                root=str(root),
            )
            return report
        except (OSError, json.JSONDecodeError):
            pass

    rng = random.Random(seed)
    pages: dict[str, dict] = {}
    source_a: list[dict] = []
    source_b: list[dict] = []
    businesses: list[dict] = []
    seq = 100

    for city in CITIES:
        for category in CATEGORIES:
            city_slug = f"c{abs(hash(city['city'])) % 10000}"
            cat_slug = category["code"].lower()
            for role in ROLES:
                for _variant_index in range(4):
                    seq += 1
                    surname = rng.choice(SURNAMES)
                    prefix = rng.choice(["", "شرکت ", "بازرگانی "])
                    name = f"{prefix}{role['label']} {category['label']} {surname}"
                    variant = rng.random()
                    name_variant = name
                    if variant < 0.22:  # نگارش متفاوت نام برای آزمون یکتاسازی
                        name_variant = name.replace("ی", "ي").replace("  ", " ")
                    landline = _landline(city["area"], seq)
                    mobile = _mobile(seq)
                    address = f"{city['city']}، {rng.choice(STREETS)}، پلاک {rng.randint(1, 200)}"
                    item = {
                        "seq": seq,
                        "name": name,
                        "name_variant": name_variant,
                        "category": category["code"],
                        "category_label": category["label"],
                        "role": role["code"],
                        "role_label": role["label"],
                        "city": city["city"],
                        "county": city["county"],
                        "province": city["province"],
                        "area": city["area"],
                        "landline": landline,
                        "mobile": mobile,
                        "address": address,
                        "address_street": rng.choice(STREETS),
                        "owner": f"{surname} {rng.choice(['محمد', 'علی', 'حسن', 'رضا'])}",
                        "site_url": f"https://{cat_slug}-{city_slug}-{seq}.example.ir/",
                        "keywords": " ".join(
                            [role["label"], category["label"], city["city"], city["province"],
                             city["county"], *category["keywords"]]
                        ),
                    }
                    businesses.append(item)

                    # حضور در هر دایرکتوری در مرحله ساخت صفحه‌ها تعیین می‌شود
                    item["in_a"] = rng.random() < 0.75
                    item["in_b"] = rng.random() < 0.70
                    if not item["in_a"] and not item["in_b"]:
                        item["in_a"] = True

    # --- صفحه‌های دایرکتوری ---
    directory_pages = 0
    for source_key, entries, source_name in (
        ("fixture_search_a", [b for b in businesses if b["in_a"]], "فهرست تجاری الف"),
        ("fixture_search_b", [b for b in businesses if b["in_b"]], "راهنمای کسب‌وکار ب"),
    ):
        groups: dict[tuple[str, str], list[dict]] = {}
        for item in entries:
            groups.setdefault((item["city"], item["category"]), []).append(item)
        del entries  # فقط برای شفافیت: ورودی‌های ساخت‌نشده استفاده نمی‌شوند
        for (city_name, category_code), items in groups.items():
            city = next(c for c in CITIES if c["city"] == city_name)
            category = next(c for c in CATEGORIES if c["code"] == category_code)
            for page_no in range(1, 3):  # دو صفحه برای هر ترکیب
                start = (page_no - 1) * 5
                window = items[start : start + 5]
                if not window:
                    continue
                url = f"https://{source_key}.example.ir/{normalize_fa(city_name).replace(' ', '-')}/{category_code.lower()}/page{page_no}.html"
                file_name = f"{source_key}_{city_name.replace(' ', '_')}_{category_code.lower()}_{page_no}.html"
                html = _directory_page_html(
                    city,
                    category,
                    [
                        {**i, "name": (i["name_variant"] if page_no == 2 else i["name"])}
                        for i in window
                    ],
                    source_name=source_name,
                )
                (pages_dir / file_name).write_text(html, encoding="utf-8")
                pages[url] = {"file": f"pages/{file_name}", "title": f"{category['label']} {city_name}"}
                directory_pages += 1
                for rank, item in enumerate(window):
                    display_name = item["name_variant"] if page_no == 2 else item["name"]
                    target = source_a if source_key == "fixture_search_a" else source_b
                    target.append(
                        {
                            "url": url,
                            "title": display_name,
                            "snippet": f"{display_name} — {item['role_label']} {item['category_label']}، "
                                       f"{item['city']}، تلفن: {_format_landline(item['landline'])} — "
                                       f"همراه: {item['mobile']}. آدرس: {item['address']}",
                            "city": item["city"],
                            "county": item["county"],
                            "province": item["province"],
                            "category": item["category"],
                            "keywords": item["keywords"],
                            "file": pages[url]["file"],
                            "rank": rank,
                        }
                    )

    # --- صفحه‌های سایت اختصاصی ---
    site_pages = 0
    for item in businesses:
        if item["seq"] % 5 != 0:
            continue
        url = item["site_url"]
        file_name = f"site_{item['seq']}.html"
        (pages_dir / file_name).write_text(_company_site_html(item), encoding="utf-8")
        pages[url] = {"file": f"pages/{file_name}", "title": item["name"]}
        site_pages += 1
        source_a.append(
            {
                "url": url,
                "title": item["name"],
                "snippet": f"سایت رسمی {item['name']} — {item['role_label']} {item['category_label']} در "
                           f"{item['city']}، ارسال به سراسر کشور. تماس: {_format_landline(item['landline'])}",
                "city": item["city"],
                "county": item["county"],
                "province": item["province"],
                "category": item["category"],
                "keywords": item["keywords"],
                "file": pages[url]["file"],
            }
        )

    # --- صفحه‌های اسپم و نامرتبط ---
    spam_pages = 0
    for i, (title, body) in enumerate(SPAM_PAGES):
        city = CITIES[i % len(CITIES)]
        url = f"https://spam-{i}.example.ir/page.html"
        file_name = f"spam_{i}.html"
        (pages_dir / file_name).write_text(_spam_page_html(title, body, city), encoding="utf-8")
        pages[url] = {"file": f"pages/{file_name}", "title": title}
        spam_pages += 1
        source_a.append(
            {
                "url": url,
                "title": title,
                "snippet": body,
                "city": city["city"],
                "province": city["province"],
                "category": "OTHER",
                "keywords": f"{title} {body}",
                "file": pages[url]["file"],
            }
        )
    for i, (title, body) in enumerate(IRRELEVANT_PAGES):
        city = CITIES[(i + 2) % len(CITIES)]
        url = f"https://irrelevant-{i}.example.ir/"
        file_name = f"irrelevant_{i}.html"
        (pages_dir / file_name).write_text(_irrelevant_page_html(title, body, city), encoding="utf-8")
        pages[url] = {"file": f"pages/{file_name}", "title": title}
        spam_pages += 1
        source_b.append(
            {
                "url": url,
                "title": title,
                "snippet": body,
                "city": city["city"],
                "province": city["province"],
                "category": "OTHER",
                "keywords": f"{title} {body}",
                "file": pages[url]["file"],
            }
        )

    index = {
        "meta": {
            "generated_by": "numberbank.fixtures.build",
            "synthetic": True,
            "warning": "همه داده‌ها مصنوعی است و شماره‌ها در بلوک رزرو‌شده ۰۰۰ قرار دارند.",
            "businesses": len(businesses),
            "directory_pages": directory_pages,
            "site_pages": site_pages,
            "spam_pages": spam_pages,
            "cities": [c["city"] for c in CITIES],
            "categories": [c["code"] for c in CATEGORIES],
        },
        "pages": pages,
        "sources": {"fixture_search_a": source_a, "fixture_search_b": source_b},
    }
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")

    report = FixtureBuildReport(
        businesses=len(businesses),
        directory_pages=directory_pages,
        site_pages=site_pages,
        spam_pages=spam_pages,
        index_entries=len(pages),
        root=str(root),
    )
    log.info("پیکره آزمایشی ساخته شد: %s کسب‌وکار، %s صفحه", report.businesses, report.index_entries)
    return report
