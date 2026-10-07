# راهنمای کامل استفاده — NumberBank

این سند همه دستورها، گردش‌کارها و API را با مثال توضیح می‌دهد. همه دستورها با
`numberbank` (یا `.venv/bin/numberbank`) اجرا می‌شوند.

---

## ۱) راه‌اندازی اولیه

```bash
numberbank init                     # ساخت جداول + بارگذاری استان/شهرستان/شهر + دسته‌ها + منابع
numberbank init --reset             # پاک‌سازی و بارگذاری از نو
numberbank locations --province یزد # بررسی داده مرجع بارگذاری‌شده
numberbank fetch-geo                # (اختیاری) داده کامل رسمی: ۴۸۴ شهرستان و ۱۴۸۱ شهر
```

خروجی `init` در حالت عادی: **۳۱ استان / ۳۸۰ شهرستان / ۵۵۴ شهر / ۵ دسته / ۱۱ منبع**.

---

## ۲) اجرای یک Job کشف داده

```bash
numberbank discover \
  --province "سیستان و بلوچستان" --province "کرمان" \
  --city خاش --city زاهدان --city کوهدشت \
  --category DETERGENT --category COSMETIC \
  --business-type WHOLESALER --business-type DISTRIBUTOR --business-type RETAILER \
  --source fixture_search_a --source fixture_search_b \
  --target 500 --min-confidence 45 --pages 2 --max-pages 400 --workers 6 --time-limit 900
```

نکته‌ها:

* اگر `--province/--city` داده نشود، برنامه‌ریز همه شهرها را با **اولویت شهرهای کوچک** انتخاب می‌کند
  (سیاست عمدی برای پوشش شهرستان‌ها).
* `--min-confidence` کف امتیاز اعتبار برای «شمارش به‌عنوان نتیجه معتبر» است.
* `--target` سقف نتایج معتبر یک Job؛ با رسیدن به آن، Job با دلیل `target_reached` پایان می‌یابد.
* `--offline` (یا `NUMBERBANK_OFFLINE=true`) اجرا را روی پیکره آزمایشی محلی محدود می‌کند.
* خروجی: شناسه Job + جدول آمار (پرس‌وجو، صفحه، کسب‌وکار جدید، شماره جدید، نامرتبط، اسپم، خطا).

مدیریت Jobها:

```bash
numberbank jobs --limit 20
numberbank job-show 3
numberbank run-job 3
numberbank job-cancel 3
numberbank worker --once            # یک چرخه کارگر (Jobهای PENDING + خزش URL + بازاعتبارسنجی)
numberbank worker --workers 4       # چند کارگر موازی
```

---

## ۳) آمار، پوشش و منابع

```bash
numberbank stats                     # کل کسب‌وکار/شماره/شاهد/تکرار/پوشش + توزیع باند و دسته
numberbank stats --json > stats.json
numberbank stats --exclude-synthetic # کنار گذاشتن داده پیکره آزمایشی
numberbank coverage --province یزد   # گزارش پوشش شهری
numberbank sources                   # وضعیت فعال/غیرفعال هر منبع + تعداد شواهد
numberbank queries --limit 50        # دفتر پرس‌وجوها (چه چیزی کجا پرسیده شد)
numberbank reset-ledger              # پاک کردن دفتر پرس‌وجوها برای اجرای همه ترکیب‌ها از نو
```

---

## ۴) خروجی گرفتن

```bash
numberbank export --format csv   --output out/all.csv
numberbank export --format xlsx  --output out/all.xlsx --min-confidence 60
numberbank export --format json  --output out/all.json --category DETERGENT --province یزد
numberbank export --format csv --with-evidence --output out/filtered.csv \
  --city خاش --band HIGH --phone-status VALID --discovered-after-days 7
numberbank export --format csv --exclude-synthetic   # فقط داده واقعی (حالت زنده)
```

فیلترهای پشتیبانی‌شده: `--province`، `--city`، `--category`، `--business-type`، `--band`،
`--min-confidence`، `--phone-status`، `--search`، `--discovered-after-days`، `--source-key`، `--limit`.
قابلیت `--with-evidence` یک فایل جداگانه شامل **همه شواهد** (منبع/تاریخ/متن) می‌سازد.

ستون‌های اصلی خروجی: شناسه، نام کسب‌وکار، نام مالک/مسئول + وضعیت آن، استان/شهرستان/شهر، نشانی،
تلفن ثابت، موبایل تجاری، سایر شماره‌ها، وضعیت و نوع شماره، وب‌سایت، دامنه، تعارض مالک، نوع
کسب‌وکار، دسته‌ها، تعداد منابع، منابع، شواهد (آدرس‌ها)، تاریخ کشف، آخرین مشاهده، آخرین اعتبارسنجی،
امتیاز و باند اعتبار، وضعیت رکورد، وضعیت فیلدها، تفکیک امتیاز، توضیحات.

---

## ۵) نگه‌داری، یکتاسازی و بازاعتبارسنجی

```bash
numberbank dedup --threshold 0.80 --review-threshold 0.62   # ادغام تکراری‌ها با حفظ همه منابع
numberbank revalidate --limit 500                            # بازاعتبارسنجی سررسیده‌ها
numberbank recompute                                         # بازمحاسبه امتیاز پس از تغییر پارامترها
numberbank purge-synthetic                                   # حذف داده آزمایشی از دیتابیس
numberbank clear-cache                                       # پاک کردن کش HTTP
```

اجرای Docker (اختیاری؛ برای مقیاس با PostgreSQL و کارگر جداگانه):

```bash
make docker-build          # ساخت تصویر
make docker-up             # PostgreSQL + API/داشبورد + کارگر
# یا بدون Docker:
NUMBERBANK_DATABASE_URL="postgresql+psycopg://user:pass@host:5432/numberbank" numberbank init
```

سیاست یکتاسازی: ادغام خودکار تنها با **لنگر هویتی** (شماره مشترک + نام/نشانی هم‌خوان، دامنه یکسان،
نشانی تقریباً یکسان، یا هسته نام یکسان در همان شهر) انجام می‌شود؛ جفت‌های مشکوک برای **بازبینی
انسانی** فهرست می‌شوند و هیچ داده‌ای حذف نمی‌شود (رکورد ادغام‌شده `MERGED` می‌شود).

---

## ۶) سرویس API + داشبورد

```bash
numberbank serve --host 0.0.0.0 --port 8000
```

| مسیر | توضیح |
|---|---|
| `/` | داشبورد آماری زنده |
| `/businesses` , `/businesses/{id}` | فهرست و جزئیات با شواهد و تاریخچه اعتبارسنجی |
| `/jobs` | ساخت/پیگیری Job |
| `/coverage` , `/sources` | پوشش جغرافیایی و وضعیت منابع |
| `/docs` | مستندات OpenAPI |

| API | توضیح |
|---|---|
| `GET /api/health` | سلامت سرویس و نوع دیتابیس |
| `GET /api/stats?include_synthetic=true` | آمار کامل داشبورد |
| `GET /api/v1/businesses?...` | جستجوی فیلتردار + صفحه‌بندی |
| `GET /api/v1/businesses/{id}` | جزئیات + شواهد + تاریخچه |
| `GET /api/v1/phones?...` | فهرست شماره‌ها با فیلتر وضعیت/نوع/اعتبار |
| `POST /api/v1/jobs` | ساخت Job (پارامتر `run_now=true` برای اجرای فوری) |
| `GET /api/v1/jobs` , `GET /api/v1/jobs/{id}` | فهرست و جزئیات |
| `POST /api/v1/jobs/{id}/cancel` | درخواست توقف |
| `GET /api/v1/coverage` | پوشش استانی/شهری |
| `GET /api/v1/sources` , `/api/v1/queries` , `/api/v1/locations` | منابع، دفتر پرس‌وجو، مکان‌ها |
| `GET /api/v1/dedup` | آخرین ادغام‌های تکراری |
| `GET /api/v1/export?format=csv&...` | خروجی از طریق HTTP |

---

## ۷) گردش‌کارهای پیشنهادی

### الف) پوشش تدریجی شهرستان‌ها با منابع رایگان

```bash
numberbank init
numberbank discover --province "خراسان جنوبی" --target 200 --workers 4
numberbank dedup
numberbank stats
numberbank export --format xlsx --output out/kh-south.xlsx
```

### ب) اجرای شبانه خودکار (cron)

```cron
0 2 * * * cd /opt/Number && .venv/bin/numberbank worker --once >> /var/log/numberbank.log 2>&1
```

### ج) به‌روزرسانی مستمر بدون تکرار داده پیشین

هر اجرا ابتدا دفتر پرس‌وجوها را می‌خواند، ترکیب‌های تکراری را رد می‌کند، شماره‌های موجود را
بازشناسی می‌کند (نه کشف جدید)، فیلدهای خالی رکوردهای قدیمی را پر می‌کند و شماره‌های سررسیده را
بازاعتبارسنجی می‌کند. سنجه `previously_known_reported_new` در گزارش دمو تضمین می‌کند هیچ شماره
پیش‌شناخته‌شده‌ای «جدید» گزارش نشود.

### د) بررسی کیفیت داده

```bash
numberbank stats --json | jq '.phones, .duplicates, .reviews'
numberbank list --city خاش --min-confidence 60
```

---

## ۸) عیب‌یابی

| نشانه | راه‌حل |
|---|---|
| `No module named numberbank` | نصب editable را تکرار کنید: `.venv/bin/pip install -e ".[api,excel,dev]"` |
| همه Jobها خطا می‌دهند | `numberbank logs`/`job-show` را ببینید؛ در محیط بسته `--offline` یا `NUMBERBANK_OFFLINE=true` بگذارید |
| دیتابیس قفل می‌شود | `NUMBERBANK_WORKERS` را کم کنید؛ WAL و `busy_timeout` فعال است |
| پوشش صفر است | ابتدا `init` و سپس `discover` را اجرا کنید؛ شهرها فقط با Job کشف پوشش می‌گیرند |
| منابع فعال نیستند | کلید API را در `.env` بگذارید یا از منابع HTML (DuckDuckGo/Mojeek/SearXNG) استفاده کنید |


## دادهٔ نمونهٔ آمادهٔ دانلود

برای استفادهٔ سریع بدون اجرای خزنده، بستهٔ آزمایشی در `demo-dataset/` مخزن قرار دارد:

```bash
# از مخزن کلون‌شده
sqlite3 demo-dataset/numberbank-demo.db "SELECT name, city_name, primary_phone FROM v_business_export LIMIT 5;"

# یا اجرای داشبورد روی همان دیتابیس
NUMBERBANK_DATABASE_URL="sqlite:///$(pwd)/demo-dataset/numberbank-demo.db" numberbank serve --port 8000
```

⚠️ دادهٔ آن بسته **ساختگی و آزمایشی** است (جزئیات در `demo-dataset/README-DATASET.md`).
