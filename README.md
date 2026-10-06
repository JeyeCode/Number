# NumberBank — سامانه کشف پیوسته شماره‌های تماس کسب‌وکارهای ایران

**NumberBank** یک نرم‌افزار مستقل، قابل اجرا و قابل توسعه است که شماره‌های تماس **کسب‌وکارهای واقعی**
را در حوزه‌های زیر، در **همه ایران** و به‌ویژه **شهرستان‌ها و شهرهای کوچک**، به‌صورت خودکار کشف،
اعتبارسنجی، یکتاسازی و به‌روزرسانی می‌کند:

> شوینده‌ها • محصولات بهداشتی • آرایشی و بهداشتی • سلولزی و محصولات مرتبط •
> عمده‌فروشان • توزیع‌کنندگان • پخش‌کنندگان • فروشندگان/خرده‌فروشان تخصصی

> **اصل بنیادی:** هیچ شماره، نام یا نشانی‌ای ساخته یا حدس زده نمی‌شود. هر داده باید «شاهد» داشته باشد
> (آدرس منبع، نام منبع، نوع منبع، تاریخ مشاهده، متن استخراج‌شده، وضعیت اعتبار منبع). نبود داده با
> `NOT_FOUND`، نامعلومی با `UNVERIFIED` و تناقض با `CONFLICTED` ثبت می‌شود.

---

## ۱) نصب سریع

نیازمندی‌ها: Python 3.10+ (روی Linux/macOS/Windows). دیتابیس پیش‌فرض SQLite است و **هیچ سرویس
جانبی (Redis/Postgres/Docker) برای شروع لازم نیست**.

```bash
git clone <REPO_URL> Number && cd Number
python3 -m venv .venv
.venv/bin/pip install -e ".[api,excel,dev]"     # نصب کامل (API + خروجی Excel + ابزار توسعه)
.venv/bin/numberbank version
```

میان‌بر با Make:

```bash
make venv install-all     # ساخت محیط + نصب کامل
make init                 # ساخت جداول و بارگذاری داده مرجع (۳۱ استان/۳۸۰ شهرستان/۵۵۴ شهر)
make demo                 # اجرای دموی آفلاین سرتاسری روی پیکره آزمایشی + آزمون پذیرش
make serve                # اجرای API + داشبورد وب روی http://0.0.0.0:8000
make test                 # اجرای آزمون‌ها (واحد + یکپارچه، بدون نیاز به اینترنت)
```

## ۲) اجرای واقعی (کشف داده آنلاین)

```bash
# ۱) بارگذاری داده مرجع (استان/شهرستان/شهر، دسته‌ها، منابع)
.venv/bin/numberbank init

# ۲) (اختیاری) دریافت داده کامل و رسمی تقسیمات کشوری: ۳۱ استان / ۴۸۴ شهرستان / ۱۴۸۱ شهر
.venv/bin/numberbank fetch-geo

# ۳) ساخت یک Job کشف داده با فیلترهای کامل (استان، شهر، دسته، هدف، حداقل اعتبار، منابع، کارگر، زمان)
.venv/bin/numberbank discover \
  --province "خراسان جنوبی" --city بیرجند \
  --category DETERGENT --category HYGIENE \
  --business-type WHOLESALER --business-type DISTRIBUTOR \
  --target 300 --min-confidence 45 --workers 6 --pages 2 --time-limit 900

# ۴) مشاهده وضعیت، آمار، پوشش و منابع
.venv/bin/numberbank jobs
.venv/bin/numberbank stats
.venv/bin/numberbank coverage
.venv/bin/numberbank sources

# ۵) خروجی گرفتن (CSV / Excel / JSON) با فیلترهای کامل
.venv/bin/numberbank export --format xlsx --output out/businesses.xlsx --min-confidence 60
.venv/bin/numberbank export --format csv  --output out/all.csv --with-evidence

# ۶) به‌روزرسانی مستمر: بازاعتبارسنجی سررسیده‌ها + یکتاسازی مجدد
.venv/bin/numberbank revalidate --limit 500
.venv/bin/numberbank dedup
```

### متغیرهای محیطی مهم (فایل `.env.example` را ببینید)

| متغیر | توضیح |
|---|---|
| `NUMBERBANK_OFFLINE` | `true` = فقط پیکره آزمایشی (بدون شبکه) — برای دمو و آزمون |
| `NUMBERBANK_DATABASE_URL` | پیش‌فرض `sqlite:///./data/numberbank.db`؛ برای مقیاس بزرگ: `postgresql+psycopg://...` |
| `NUMBERBANK_WORKERS` | تعداد کارگر موازی |
| `NUMBERBANK_GOOGLE_CSE_KEY` / `..._CX` | فعال‌سازی Google Programmable Search |
| `NUMBERBANK_BING_API_KEY` | فعال‌سازی Bing Web Search API |
| `NUMBERBANK_SEARXNG_BASE_URL` | استفاده از نمونه SearXNG خودتان |
| `NUMBERBANK_PROXY_URL` | پروکسی HTTP(S) برای خزش |

## ۳) معماری (خلاصه)

```
                    ┌──────────────┐
  Job/داشبورد  ───▶ │ QueryPlanner │  استان→شهرستان→شهر × دسته × نقش ⇒ هزاران پرس‌وجو
                    └──────┬───────┘
                           ▼
   ┌───────────────────────────────────────────────┐
   │ SourceRegistry: موتور جستجو / دایرکتوری / داده │
   │ عمومی / سایت شرکت‌ها / منابع آزمایشی (offline) │
   └──────┬────────────────────────────────────────┘
          ▼
   ┌────────────┐   ┌──────────────┐   ┌────────────────┐
   │ Crawler    │──▶│ Extractor    │──▶│ RelevanceScorer│
   │ robots/rate│   │ JSON-LD/بلوک │   │ + SpamDetector │
   └────────────┘   └──────────────┘   └───────┬────────┘
                                               ▼
        ┌──────────────┐   ┌─────────────┐   ┌───────────────┐
        │ PhoneEngine  │──▶│ Persister   │──▶│ Confidence    │
        │ نرمال‌سازی/  │   │ شواهد + یکتا│   │ Scoring (باند)│
        │ کیفیت/جعلی   │   └──────┬──────┘   └───────────────┘
        └──────────────┘          ▼
                          ┌──────────────┐        ┌─────────────┐
                          │ Deduplicator │───────▶│  Database   │
                          │ ادغام چندسیگنال│      │ ۱۹ جدول     │
                          └──────────────┘        └──────┬──────┘
                                                         ▼
                                    ┌──────────────┬──────────────┬───────────┐
                                    │ API (FastAPI)│ داشبورد وب  │ Exporter  │
                                    └──────────────┴──────────────┴───────────┘
```

ماژول‌ها (همه جدا و قابل توسعه): `crawl/`, `sources/`, `extract/`, `phones/`, `relevance/`,
`scoring/`, `dedup/`, `pipeline/`, `geo/`, `db/`, `domain/`, `queue/`, `services/`, `api/`, `web/`, `fixtures/`.

شرح کامل تصمیم‌های معماری و دلایل انتخاب فناوری در [`ARCHITECTURE.md`](ARCHITECTURE.md).

## ۴) داشبورد و API

```bash
.venv/bin/numberbank serve --host 0.0.0.0 --port 8000
```

* داشبورد: `/` (آمار کل، شماره معتبر/نامعتبر/جدید/تکراری، اعتبار بالا، شهرهای بررسی‌شده و باقی‌مانده،
  وضعیت Job فعال با درصد پیشرفت، خطاها، منابع استفاده‌شده)
* کسب‌وکارها: `/businesses` و جزئیات هر رکورد با **شواهد و تاریخچه اعتبارسنجی**: `/businesses/{id}`
* Jobها: `/jobs` • پوشش جغرافیایی: `/coverage` • منابع: `/sources`
* مستندات تعاملی API: `/docs`

نمونه فراخوانی API:

```bash
curl "http://localhost:8000/api/health"
curl "http://localhost:8000/api/stats?include_synthetic=true"
curl "http://localhost:8000/api/v1/businesses?city=خاش&min_confidence=60&limit=10"
curl "http://localhost:8000/api/v1/businesses/14"
curl "http://localhost:8000/api/v1/coverage"
curl "http://localhost:8000/api/v1/jobs"
```

## ۵) آزمون‌ها و اعتبارسنجی واقعی

```bash
make test                                  # ۵۳ آزمون واحد و یکپارچه (آفلاین) — همه سبز
NUMBERBANK_RUN_LIVE=1 make test-live       # ۲ آزمون زنده (نیازمند اینترنت آزاد)
.venv/bin/numberbank selftest              # دموی سرتاسری + ۸ معیار پذیرش روی دیتابیس موقت
```

نتیجه اجرای واقعی روی پیکره آزمایشی (۲۸۸ کسب‌وکار / ۹۶ دایردپوری / ۵۷ سایت شرکت / ۸ صفحه اسپم):

| سنجه | نتیجه |
|---|---|
| کسب‌وکار فعال / کل | ۱۶۰ / ۲۰۵ (۴۵ تکراری ادغام‌شده) |
| شماره‌های ثبت‌شده | ۲۱۱ (همه معتبر، ۰ نامعتبر، ۰ قرنطینه) |
| شواهد ثبت‌شده | ۳۹۸ شاهد از ۱۵۱ آدرس متمایز |
| متن‌های نامرتبط رد‌شده | ~۲٬۰۰۰ در هر اجرا (با ثبت شاهد برای بازبینی) |
| معیارهای پذیرش | **۸ از ۸ موفق** |
| خطا | ۰ خطای مهلک؛ یک خطا هرگز کل Job را متوقف نمی‌کند |

## ۶) مستندات

| سند | محتوا |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | معماری، تحلیل حجم/فناوری، مدل داده، الگوریتم‌ها |
| [`docs/USAGE.md`](docs/USAGE.md) | راهنمای کامل استفاده، همه دستورها، گردش‌کارها، API |
| [`docs/TESTING.md`](docs/TESTING.md) | آزمون‌های اجراشده، نتایج، سنجه‌های پذیرش |
| [`docs/LIMITATIONS_AND_SCALE.md`](docs/LIMITATIONS_AND_SCALE.md) | محدودیت‌های فعلی و نقشه راه مقیاس‌پذیری تا میلیون‌ها رکورد |

## ۷) وضعیت پروژه و آنچه هنوز انجام نشده

* ✅ کشف، اعتبارسنجی، یکتاسازی، پایگاه داده، صف/کارگر، API، داشبورد، خروجی، آزمون‌ها
  (۵۳ آزمون سبز + `make demo` با ۸ از ۸ معیار پذیرش)
* ⚠️ خزش واقعی اینترنت در محیط سندباکس قابل آزمون نیست؛ مسیر واقعی آماده است اما آزمون زنده
  به شبکه آزاد نیاز دارد (`make test-live`)
* ⚠️ `fetch-geo` داده کامل ۴۸۴ شهرستان/۱۴۸۱ شهر را می‌گیرد؛ نسخه همراه برنامه ۳۸۰/۵۵۴ است
* ⚠️ نام‌های مالک/مسئول فقط از صفحاتی خوانده می‌شود که خودشان منتشر کرده‌اند و وضعیت آن با
  `owner_status` مشخص می‌شود؛ داده‌ای از شبکه‌های اجتماعی برداشت نمی‌شود مگر صفحه عمومی و قانونی باشد

## ۸) مجوز و حقوق داده

* کد: قابل استفاده و توسعه آزاد.
* داده تقسیمات کشوری: از مخزن عمومی [`sajaddp/list-of-cities-in-Iran`](https://github.com/sajaddp/list-of-cities-in-Iran)
  با مجوز **GPL-3.0** — هنگام اجرای `fetch-geo` دریافت و **ارجاع آن الزامی** است.
* داده استخراج‌شده: هر رکورد همراه با آدرس منبع و تاریخ مشاهده ذخیره می‌شود؛ کاربر موظف است پیش
  از استفاده تجاری، مجوز منابع را رعایت کند (`robots.txt`، نرخ درخواست، شرایط استفاده هر سایت).
