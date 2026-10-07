# بستهٔ دادهٔ نمونهٔ NumberBank — **دادهٔ ساختگی/آزمایشی**

> ⚠️ **هشدار مهم:** تمام نام‌ها، شماره‌ها و آدرس‌های این بسته **ساختگی** و تولیدشده توسط
> «پیکرهٔ آزمایشی آفلاین» خودِ نرم‌افزار هستند (`src/numberbank/fixtures`). این بسته برای
> **آزمایش قالب ستون‌ها، API، داشبورد و فرایند یکتاسازی** است، نه دادهٔ واقعی کسب‌وکارها.
> برای تولید دادهٔ واقعی باید خودِ ابزار روی اینترنت آزاد اجرا شود:
> `numberbank discover --province "..." --city "..." --target 500`
> (هیچ شمارهٔ واقعی در این بسته یا در مخزن قرار داده نشده است.)

## محتوا

| فایل | توضیح |
|---|---|
| `businesses.csv` | خروجی CSV کسب‌وکارهای **فعال** (161 ردیف) با همهٔ ستون‌های استاندارد |
| `businesses.json` | همان رکوردها به‌صورت JSON (ساختار کامل، مناسب ادغام ماشینی) |
| `businesses.xlsx` | همان رکوردها در قالب Excel |
| `evidence.csv` | خروجی همراه با ستون‌های شواهد (آدرس منبع، نوع منبع، تاریخ مشاهده، دادهٔ استخراجی) |
| `numberbank-demo.db` | کل پایگاه دادهٔ دمو (SQLite، ۱۹ جدول) — قابل بازکردن با `sqlite3` یا اجرای داشبورد روی آن |
| `stats.json` / `coverage.json` / `sources.json` | آمار، پوشش جغرافیایی و وضعیت منابع در لحظهٔ ساخت |
| `manifest.json` | فهرست فایل‌ها با SHA-256 و خلاصهٔ اعداد |

## اعداد این اجرا

* کسب‌وکارها: **207** کل (فعال 161، ادغام‌شده 46)
* شماره‌ها: **212** (معتبر 212، نامعتبر 0)
* شواهد: **402** مشاهده از **152** آدرس منبع یکتا
* پوشش شهرها: **6 از 548**
* آزمون پذیرش دمو: **۸ از ۸** ✅

## بازتولید همین بسته در ۳ دقیقه

```bash
git clone https://github.com/JeyeCode/Number.git && cd Number
python3 -m venv .venv && .venv/bin/pip install -e ".[api,excel]"
.venv/bin/numberbank init --offline      # دادهٔ مرجع (۳۱ استان / ۳۸۰ شهرستان / ۵۵۴ شهر) + جداول
.venv/bin/numberbank demo --runs 2 --target 300
.venv/bin/numberbank export -f csv -o businesses.csv
```

یا خیلی کوتاه‌تر: `make install && make demo` (۸ معیار پذیرش را هم چاپ می‌کند).

## استفاده از دیتابیس همراه

```bash
sqlite3 numberbank-demo.db "SELECT name, city_name, primary_phone FROM v_business_export LIMIT 5;"
# یا اجرای داشبورد/API روی همین فایل:
NUMBERBANK_DATABASE_URL="sqlite:///$(pwd)/numberbank-demo.db" numberbank serve --port 8000
```

## توضیح ستون‌های کلیدی

`name` نام کسب‌وکار · `owner_name`/`owner_status` نام مالک (فقط اگر عمومی منتشر شده باشد) ·
`province_name`/`county_name`/`city_name` تقسیمات کشوری · `primary_phone`/`mobile_phone`/`other_phones` ·
`phone_status` وضعیت شماره (VALID/PROBABLE/UNVERIFIED/INVALID) · `confidence`/`band` امتیاز و باند اعتبار ·
`relevance_score` میزان ارتباط با حوزهٔ هدف · `source_count` تعداد منابع تأییدکننده ·
`category`/`business_type` دسته و نوع کسب‌وکار · `discovered_at`/`last_validated_at` تاریخ کشف و آخرین اعتبارسنجی ·
`status` وضعیت رکورد (ACTIVE/MERGED/QUARANTINED) · `is_synthetic` ساختگی‌بودن رکورد.

ساخته‌شده با NumberBank v1.0.0 · 2026-10-07
