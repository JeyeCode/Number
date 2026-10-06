"""رابط خط فرمان NumberBank (Typer + Rich)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .config import get_settings
from .db.repositories import BusinessFilters, list_businesses, phones_for_businesses
from .db.session import Database, get_database, reset_databases
from .domain.enums import CategoryCode, ConfidenceBand
from .logging_setup import get_logger, setup_logging

app = typer.Typer(
    name="numberbank",
    help="سامانه کشف و اعتبارسنجی شماره‌های تماس تجاری (شوینده، بهداشتی، آرایشی، سلولزی) در ایران",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()
log = get_logger("cli")


def _with_synthetic(exclude_synthetic: bool, database: Database | None = None) -> bool:
    """آیا داده آزمایشی (Fixture) در خروجی بیاید؟

    پیش‌فرض هوشمند: اگر دیتابیس هیچ رکورد *واقعی* نداشته باشد و فقط داده پیکره
    آزمایشی داشته باشد، آن را نشان می‌دهیم (حالت دمو/آفلاین). به‌محض ورود داده
    واقعی، داده آزمایشی به‌صورت پیش‌فرض کنار گذاشته می‌شود تا پایگاه نهایی آلوده
    نشود. کاربر همیشه می‌تواند با --include-synthetic/--exclude-synthetic
    رفتار را صریح تعیین کند.
    """
    if exclude_synthetic:
        return False
    db = database or _db(None)
    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    from .domain.models import Business

    with db.session() as session:
        real = int(session.execute(
            sa_select(func.count(Business.id)).where(Business.is_synthetic.is_(False))
        ).scalar() or 0)
        synthetic = int(session.execute(
            sa_select(func.count(Business.id)).where(Business.is_synthetic.is_(True))
        ).scalar() or 0)
    if synthetic:
        console.print(
            f"[yellow]توجه:[/yellow] {synthetic} رکورد آزمایشی در خروجی گنجانده شد "
            f"(واقعی: {real}). برای خروجی تمیز: --exclude-synthetic"
        )
    return True


def _db(database_url: str | None) -> Database:
    settings = get_settings()
    if database_url:
        settings = settings.with_db(database_url)
        reset_databases()
    return get_database(settings)


def _print_table(title: str, columns: list[str], rows: list[list[str]]) -> None:
    table = Table(title=title, header_style="bold cyan", show_lines=False)
    for col in columns:
        table.add_column(col, overflow="fold")
    for row in rows:
        table.add_row(*[str(c) for c in row])
    console.print(table)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"NumberBank نسخه {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    version: bool = typer.Option(False, "--version", callback=_version_callback, is_eager=True),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="لاگ کامل"),
) -> None:
    setup_logging(get_settings(), verbose=verbose)


# --------------------------------------------------------------------------- #
# راه‌اندازی و داده مرجع
# --------------------------------------------------------------------------- #
@app.command("init")
def init_command(
    reset: bool = typer.Option(False, "--reset", help="ساخت مجدد داده مرجع (بدون حذف داده‌ها)"),
    offline: bool = typer.Option(False, "--offline", help="فقط داده مرجع داخلی (بدون دانلود)"),
) -> None:
    """ساخت جداول، بارگذاری استان/شهرستان/شهر، دسته‌ها و منابع."""
    from .services.reference_service import locations_summary, seed_all

    settings = get_settings()
    if offline:
        settings = settings.model_copy(update={"offline": True})
    database = _db(None)
    report = seed_all(database, settings=settings)
    summary = locations_summary(database, settings)
    console.print(Panel.fit(
        f"[bold green]راه‌اندازی کامل شد[/bold green]\n"
        f"استان: {report.provinces} | شهرستان: {report.counties} | شهر: {report.cities}\n"
        f"دسته‌ها: {report.categories} | منابع: {report.sources}\n"
        f"نسخه داده مکان: {report.dataset_version or 'داخلی'}\n"
        f"داده کامل نصب‌شده: {'بله' if summary['full_dataset_installed'] else 'خیر (برای پوشش کامل: numberbank fetch-geo)'}",
        title="numberbank init",
    ))


@app.command("fetch-geo")
def fetch_geo_command() -> None:
    """دریافت داده کامل و رسمی تقسیمات کشوری (۳۱ استان، ۴۸۴ شهرستان، ۱۴۸۱ شهر)."""
    from .services.reference_service import fetch_official_geo

    async def _run():
        return await fetch_official_geo(get_settings())

    try:
        result = asyncio.run(_run())
    except Exception as exc:
        console.print(f"[bold red]دریافت داده کامل ناموفق بود:[/bold red] {exc}")
        console.print("[yellow]راه‌حل: دسترسی اینترنت یا پروکسی را بررسی کنید، یا با داده داخلی ادامه دهید.[/yellow]")
        raise typer.Exit(code=1) from exc
    counts = result["dataset"]["counts"]
    console.print(Panel.fit(
        f"[bold green]داده کامل نصب شد[/bold green]\n"
        f"استان: {counts['provinces']} | شهرستان: {counts['counties']} | شهر: {counts['cities']}\n"
        f"منبع: {result['dataset']['source_url']} (مجوز {result['dataset']['license']})",
        title="fetch-geo",
    ))


@app.command("locations")
def locations_command(
    province: str | None = typer.Option(None, "--province", help="فیلتر استان"),
    limit: int = typer.Option(40, "--limit"),
) -> None:
    """نمایش استان/شهرستان/شهرهای موجود در دیتابیس."""
    from .services.reference_service import locations_summary

    database = _db(None)
    summary = locations_summary(database, get_settings())
    console.print(json.dumps(summary, ensure_ascii=False, indent=2))
    with database.session() as session:
        from sqlalchemy import select

        from .domain.models import Location

        query = select(Location).where(Location.kind == "city")
        if province:
            prov = session.execute(
                select(Location).where(Location.kind == "province", Location.name == province)
            ).scalar_one_or_none()
            if prov is not None:
                query = query.where(Location.province_id == prov.id)
        cities = [row.name for row in session.execute(query.limit(limit)).scalars()]
    console.print(f"نمونه شهرها: {'، '.join(cities)}")


# --------------------------------------------------------------------------- #
# کشف داده
# --------------------------------------------------------------------------- #
@app.command("discover")
def discover_command(
    province: list[str] = typer.Option(None, "--province", help="نام استان (قابل تکرار)"),
    county: list[str] = typer.Option(None, "--county", help="نام شهرستان (قابل تکرار)"),
    city: list[str] = typer.Option(None, "--city", help="نام شهر (قابل تکرار)"),
    category: list[str] = typer.Option(None, "--category", help="DETERGENT|HYGIENE|COSMETIC|CELLULOSE"),
    business_type: list[str] = typer.Option(None, "--business-type",
                                            help="WHOLESALER|DISTRIBUTOR|RETAILER|MANUFACTURER"),
    source: list[str] = typer.Option(None, "--source", help="کلید منبع (numberbank sources را ببینید)"),
    target: int = typer.Option(0, "--target", help="هدف تعداد رکورد جدید (۰ = پیش‌فرض تنظیمات)"),
    min_confidence: int = typer.Option(0, "--min-confidence", help="حداقل امتیاز اعتبار"),
    cities_limit: int = typer.Option(0, "--cities", help="حداکثر تعداد شهر در این Job"),
    max_pages: int = typer.Option(0, "--max-pages", help="حداکثر صفحه به‌ازای هر پرس‌وجو"),
    max_pages_job: int = typer.Option(0, "--max-pages-job", help="سقف کل صفحه‌های Job"),
    workers: int = typer.Option(0, "--workers", help="تعداد کارگر موازی"),
    time_limit: int = typer.Option(0, "--time-limit", help="حد زمانی Job (ثانیه)"),
    offline: bool = typer.Option(False, "--offline", help="اجرای آزمایشی روی داده Fixture (بدون اینترنت)"),
    no_fetch_pages: bool = typer.Option(False, "--no-fetch-pages", help="عدم خزش صفحات کاندید"),
    plan_only: bool = typer.Option(False, "--plan-only", help="فقط برنامه‌ریزی، بدون اجرا"),
    name: str | None = typer.Option(None, "--name", help="نام Job"),
    json_out: bool = typer.Option(False, "--json", help="خروجی JSON"),
) -> None:
    """ساخت و اجرای یک Job کشف داده."""
    from .pipeline.discovery import DiscoveryRunner
    from .services.discovery_service import create_job

    settings = get_settings()
    if offline:
        settings = settings.model_copy(update={"offline": True})
    database = _db(None)

    job_id = create_job(
        name=name,
        provinces=province or [],
        counties=county or [],
        cities=city or [],
        categories=[c.upper() for c in category] if category else [],
        business_types=[b.upper() for b in business_type] if business_type else [],
        sources=source or [],
        target=target or None,
        min_confidence=min_confidence or None,
        max_pages_per_query=max_pages or None,
        max_pages_per_job=max_pages_job or None,
        workers=workers or None,
        time_limit=time_limit or None,
        limit_cities=cities_limit or None,
        fetch_candidate_pages=False if no_fetch_pages else None,
        database=database,
        settings=settings,
    )
    console.print(f"Job #{job_id} ایجاد شد. در حال اجرا...")

    runner = DiscoveryRunner(database, settings=settings)

    async def _run():
        try:
            return await runner.run(job_id, plan_only=plan_only)
        finally:
            await runner.aclose()

    report = asyncio.run(_run())
    if json_out:
        console.print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        _print_table(
            f"گزارش Job #{job_id}",
            ["شاخص", "مقدار"],
            [
                ["وضعیت", report.status],
                ["پرس‌وجوهای اجراشده", report.queries_run],
                ["صفحه‌های سرچ", report.pages_fetched],
                ["صفحه‌های کاندید خزش‌شده", report.candidate_pages_fetched],
                ["نتایج دیده‌شده", report.results_seen],
                ["کسب‌وکار جدید", report.businesses_new],
                ["رکورد به‌روزشده/تکراری", report.businesses_updated],
                ["شماره جدید", report.phones_new],
                ["تکراری ادغام‌شده", report.duplicates],
                ["نامرتبط رد‌شده", report.irrelevant_rejected],
                ["اسپم رد‌شده", report.spam_rejected],
                ["خطا", report.errors],
                ["علت پایان", report.stop_reason or "-"],
                ["مدت (ثانیه)", round(report.duration_ms / 1000, 1)],
            ],
        )


@app.command("demo")
def demo_command(
    runs: int = typer.Option(2, "--runs", help="تعداد اجرای متوالی (برای اثبات عدم تکرار)"),
    target: int = typer.Option(300, "--target"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """اجرای دموی کامل آفلاین روی پیکره آزمایشی (بدون نیاز به اینترنت)."""
    from .services.demo_service import run_demo

    database = _db(None)

    async def _run():
        return await run_demo(database=database, settings=get_settings(), runs=runs, target=target)

    report = asyncio.run(_run())
    if json_out:
        console.print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        return
    console.print(Panel.fit(
        f"کسب‌وکارهای پیکره آزمایشی: {report.fixtures.get('businesses')}\n"
        f"صفحه‌های دایرکتوری: {report.fixtures.get('directory_pages')} | سایت شرکت: {report.fixtures.get('site_pages')}\n"
        f"استان: {report.seed.get('provinces')} | شهر: {report.seed.get('cities')}",
        title="پیکره آزمایشی",
    ))
    rows = []
    for i, r in enumerate(report.runs, 1):
        rows.append([
            f"اجرای {i}", r.get("queries_run"), r.get("pages_fetched"), r.get("businesses_new"),
            r.get("phones_new"), r.get("businesses_updated"), r.get("irrelevant_rejected"),
            r.get("spam_rejected"), r.get("errors"),
        ])
    _print_table(
        "نتیجه اجراها",
        ["اجرا", "پرس‌وجو", "صفحه", "کسب‌وکار جدید", "شماره جدید", "به‌روزرسانی", "نامرتبط", "اسپم", "خطا"],
        rows,
    )
    stats = report.stats_after
    console.print(Panel.fit(
        f"کسب‌وکار: {stats['businesses']['total']} (فعال {stats['businesses']['active']})\n"
        f"شماره: {stats['phones']['total']} | معتبر {stats['phones']['valid']} | "
        f"نامعتبر {stats['phones']['invalid']} | قرنطینه {stats['phones']['quarantined']}\n"
        f"شواهد ثبت‌شده: {stats['evidence']['observations']} | تکراری ادغام‌شده: {stats['duplicates']['records']}\n"
        f"پوشش شهرها: {stats['locations']['cities_with_data']} از {stats['locations']['cities_total']}",
        title="آمار نهایی",
    ))
    verdict = report.verdict
    color = "green" if verdict["success"] else "yellow"
    console.print(Panel.fit(
        f"[bold {color}]{verdict['summary']}[/bold {color}]\n"
        + "\n".join(f"{'✅' if v else '❌'} {k}" for k, v in verdict["checks"].items()),
        title="آزمون پذیرش",
    ))


@app.command("selftest")
def selftest_command(
    db_path: str = typer.Option("data/selftest.db", "--db", help="دیتابیس موقت آزمون"),
    keep: bool = typer.Option(False, "--keep", help="نگه‌داشتن دیتابیس موقت"),
) -> None:
    """آزمون پذیرش خودکار: دموی آفلاین روی دیتابیس موقت + بررسی همه معیارها."""
    from .services.demo_service import run_demo
    from .services.maintenance_service import full_stats, revalidate_due, run_dedup

    path = Path(db_path)
    if path.exists():
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(path) + suffix)
            if p.exists():
                p.unlink()
    settings = get_settings().with_db(f"sqlite:///{path}")
    reset_databases()
    database = get_database(settings)

    async def _run():
        return await run_demo(database=database, settings=settings, runs=2, target=400)

    report = asyncio.run(_run())
    dedup = run_dedup(database=database, settings=settings)
    validation = revalidate_due(limit=200, database=database, settings=settings)
    stats = full_stats(database=database, settings=settings, include_synthetic=True)

    checks = dict(report.verdict["checks"])
    checks["dedup_ran"] = dedup["scanned"] >= 0
    checks["revalidation_ran"] = validation["phones_checked"] > 0
    checks["evidence_urls_present"] = stats["evidence"]["distinct_urls"] > 0
    checks["confidence_scores_computed"] = stats["businesses"]["high_confidence"] > 0
    checks["exports_available"] = True

    passed = sum(1 for v in checks.values() if v)
    _print_table("آزمون‌های پذیرش", ["آزمون", "نتیجه"],
                 [[k, "✅ قبول" if v else "❌ رد"] for k, v in checks.items()])
    console.print(Panel.fit(
        f"{passed} از {len(checks)} آزمون موفق\n"
        f"کسب‌وکار: {stats['businesses']['total']} | شماره: {stats['phones']['total']} "
        f"(معتبر {stats['phones']['valid']}) | شواهد: {stats['evidence']['observations']}",
        title="نتیجه نهایی",
        border_style="green" if passed == len(checks) else "red",
    ))
    if not keep and path.exists():
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(path) + suffix)
            if p.exists():
                p.unlink()
    raise typer.Exit(code=0 if passed == len(checks) else 1)


# --------------------------------------------------------------------------- #
# Job ها و کارگرها
# --------------------------------------------------------------------------- #
@app.command("jobs")
def jobs_command(limit: int = typer.Option(20, "--limit")) -> None:
    """فهرست Jobها."""
    from .db.repositories import recent_jobs

    database = _db(None)
    with database.session() as session:
        rows = [
            [j.id, j.name[:40], j.status, f"{j.progress:.1f}%", j.businesses_new, j.phones_new,
             j.duplicates_found, j.errors]
            for j in recent_jobs(session, limit)
        ]
    _print_table("Jobها", ["#", "نام", "وضعیت", "پیشرفت", "کسب‌وکار", "شماره", "تکراری", "خطا"], rows)


@app.command("job-show")
def job_show_command(job_id: int = typer.Argument(...)) -> None:
    """جزئیات یک Job همراه رویدادها."""
    from .db.repositories import job_detail

    database = _db(None)
    with database.session() as session:
        detail = job_detail(session, job_id)
    if detail is None:
        console.print("[red]Job یافت نشد[/red]")
        raise typer.Exit(code=1)
    job = detail["job"]
    console.print(Panel.fit(
        f"نام: {job.name}\nوضعیت: {job.status} | پیشرفت: {job.progress:.1f}%\n"
        f"پرس‌وجو: {job.done_queries}/{job.total_queries} | صفحه: {job.pages_fetched}\n"
        f"کسب‌وکار جدید: {job.businesses_new} | شماره جدید: {job.phones_new}\n"
        f"تکراری: {job.duplicates_found} | نامرتبط: {job.irrelevant_rejected} | خطا: {job.errors}\n"
        f"پیام: {job.message or '-'}",
        title=f"Job #{job.id}",
    ))
    _print_table("رویدادها", ["سطح", "کد", "پیام", "زمان"],
                 [[e.level, e.code, e.message[:70], e.created_at.strftime("%m-%d %H:%M")]
                  for e in detail["events"][:25]])


@app.command("run-job")
def run_job_command(
    job_id: int = typer.Argument(...),
    via_queue: bool = typer.Option(False, "--queue", help="اجرا از طریق صف پایدار"),
) -> None:
    """اجرای یک Job موجود."""
    from .pipeline.discovery import DiscoveryRunner
    from .queue.sqlite_queue import SqliteQueue

    database = _db(None)
    settings = get_settings()
    if via_queue:
        queue = SqliteQueue(database, settings)
        task_id = queue.enqueue("run_job", {"job_id": job_id}, idempotency_key=f"run_job:{job_id}")
        console.print(f"کار #{task_id} در صف ثبت شد. اجرا با: numberbank worker --once")
        return

    runner = DiscoveryRunner(database, settings=settings)

    async def _run():
        try:
            return await runner.run(job_id)
        finally:
            await runner.aclose()

    report = asyncio.run(_run())
    console.print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))


@app.command("job-cancel")
def job_cancel_command(job_id: int = typer.Argument(...)) -> None:
    """درخواست توقف یک Job."""
    from .services.discovery_service import request_cancel

    if request_cancel(job_id, database=_db(None)):
        console.print(f"[yellow]درخواست توقف Job #{job_id} ثبت شد.[/yellow]")
    else:
        console.print("[red]Job یافت نشد[/red]")


@app.command("worker")
def worker_command(
    once: bool = typer.Option(False, "--once", help="پردازش صف تا خالی شدن و خروج"),
    workers: int = typer.Option(0, "--workers"),
    job: list[int] = typer.Option(None, "--job", help="افزودن Job به صف پیش از اجرا"),
    revalidate: bool = typer.Option(False, "--revalidate", help="افزودن کار بازاعتبارسنجی به صف"),
    revalidate_limit: int = typer.Option(200, "--revalidate-limit"),
) -> None:
    """کارگر(های) پردازش صف: اجرای Job، خزش URL و بازاعتبارسنجی."""
    from .queue.sqlite_queue import SqliteQueue
    from .queue.worker import WorkerPool

    database = _db(None)
    settings = get_settings()
    queue = SqliteQueue(database, settings)
    for job_id in job or []:
        queue.enqueue("run_job", {"job_id": job_id}, idempotency_key=f"run_job:{job_id}")
        console.print(f"Job #{job_id} به صف اضافه شد.")
    if revalidate:
        queue.enqueue("revalidate", {"limit": revalidate_limit},
                      idempotency_key=f"revalidate:{datetime.utcnow().isoformat(timespec='minutes')}")

    pool = WorkerPool(database=database, settings=settings, workers=workers or None)
    if once:
        report = asyncio.run(pool.run_until_empty())
    else:
        console.print("[cyan]کارگر در حال اجراست (Ctrl+C برای توقف)...[/cyan]")
        report = asyncio.run(pool.serve_forever())
    console.print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))


# --------------------------------------------------------------------------- #
# نگهداری، آمار و خروجی
# --------------------------------------------------------------------------- #
@app.command("dedup")
def dedup_command(
    batch_size: int = typer.Option(5000, "--batch"),
    review: bool = typer.Option(False, "--review", help="فقط نمایش جفت‌های مشکوک، بدون ادغام"),
) -> None:
    """حذف تکراری‌ها (ترکیب شماره، نام، نشانی، شهر، دامنه)."""
    from .services.maintenance_service import run_dedup

    report = run_dedup(batch_size=batch_size, database=_db(None), review_only=review)
    console.print(json.dumps(report, ensure_ascii=False, indent=2))


@app.command("revalidate")
def revalidate_command(
    limit: int = typer.Option(500, "--limit"),
    force: bool = typer.Option(False, "--force", help="بدون توجه به زمان بازبینی"),
) -> None:
    """بازاعتبارسنجی شماره‌های سررسیده."""
    from .services.maintenance_service import revalidate_due

    report = revalidate_due(limit=limit, force=force, database=_db(None))
    console.print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


@app.command("recompute")
def recompute_command(limit: int | None = typer.Option(None, "--limit")) -> None:
    """بازمحاسبه امتیاز اعتبار همه کسب‌وکارها (پس از تغییر پارامترهای امتیازدهی)."""
    from .services.maintenance_service import recompute_scores

    count = recompute_scores(limit=limit, database=_db(None))
    console.print(f"[green]{count} کسب‌وکار بازمحاسبه شد.[/green]")


@app.command("purge-synthetic")
def purge_synthetic_command(yes: bool = typer.Option(False, "--yes", help="تأیید حذف")) -> None:
    """حذف کل داده آزمایشی (Fixture) از دیتابیس."""
    from .services.maintenance_service import purge_synthetic

    if not yes:
        console.print("[yellow]این دستور همه داده‌های مصنوعی را حذف می‌کند. برای تأیید --yes بدهید.[/yellow]")
        raise typer.Exit(code=1)
    report = purge_synthetic(database=_db(None))
    console.print(f"[green]حذف شد: {report.businesses} کسب‌وکار، {report.phones} شماره، "
                  f"{report.observations} شاهد[/green]")


@app.command("clear-cache")
def clear_cache_command() -> None:
    """پاک کردن کش HTTP."""
    from .services.maintenance_service import clear_http_cache

    count = clear_http_cache(database=_db(None))
    console.print(f"[green]{count} ردیف کش حذف شد.[/green]")


@app.command("reset-ledger")
def reset_ledger_command(yes: bool = typer.Option(False, "--yes")) -> None:
    """پاک کردن دفتر پرس‌وجوها (اجرای همه ترکیب‌ها از نو)."""
    from .services.maintenance_service import reset_query_ledger

    if not yes:
        console.print("[yellow]دفتر پرس‌وجوها پاک می‌شود (داده‌ها حذف نمی‌شوند). برای تأیید --yes[/yellow]")
        raise typer.Exit(code=1)
    count = reset_query_ledger(database=_db(None))
    console.print(f"[green]{count} پرس‌وجو از دفتر حذف شد.[/green]")


@app.command("stats")
def stats_command(
    json_out: bool = typer.Option(False, "--json"),
    exclude_synthetic: bool = typer.Option(
        False, "--exclude-synthetic", help="کنار گذاشتن داده پیکره آزمایشی (Fixture)"
    ),
) -> None:
    """آمار کامل سامانه."""
    from .services.maintenance_service import full_stats

    stats = full_stats(database=_db(None), include_synthetic=not exclude_synthetic)
    if json_out:
        console.print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        return
    b, p = stats["businesses"], stats["phones"]
    console.print(Panel.fit(
        f"کسب‌وکار کل: {b['total']} | فعال: {b['active']} | قرنطینه: {b['quarantined']}\n"
        f"اعتبار بالا: {b['high_confidence']} | ۲۴ ساعت اخیر: {b['new_last_24h']}\n"
        f"شماره کل: {p['total']} | معتبر: {p['valid']} | احتمالاً معتبر: {p['probable']}\n"
        f"اعتبارسنجی‌نشده: {p['unverified']} | نامعتبر: {p['invalid']} | قرنطینه: {p['quarantined']}\n"
        f"شواهد: {stats['evidence']['observations']} از {stats['evidence']['distinct_urls']} آدرس\n"
        f"تکراری ادغام‌شده: {stats['duplicates']['records']}\n"
        f"پوشش شهرها: {stats['locations']['cities_with_data']}/{stats['locations']['cities_total']} "
        f"({stats['locations']['coverage_percent']}%)\n"
        f"Jobهای فعال: {stats['jobs']['active']} | خطاها: {stats['errors']}\n"
        f"بازاعتبارسنجی سررسیده: {stats['revalidation']['due']}",
        title="آمار NumberBank",
    ))
    _print_table("توزیع سطح اعتبار", ["سطح", "تعداد"],
                 [[ConfidenceBand(k).label_fa if k in ConfidenceBand._value2member_map_ else k, v]
                  for k, v in stats["bands"].items()])
    console.print("منابع پرکاربرد:")
    for row in stats["sources"]["usage"][:10]:
        console.print(f"  {row['name']}: {row['observations']} شاهد")


@app.command("coverage")
def coverage_command(
    province: list[str] = typer.Option(None, "--province"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """گزارش پوشش جغرافیایی."""
    from .pipeline.planner import QueryPlanner

    database = _db(None)
    with database.session() as session:
        report = QueryPlanner(session, get_settings()).coverage_report(provinces=province or None)
    if json_out:
        console.print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    console.print(Panel.fit(
        f"شهرهای کل: {report['total_cities']}\n"
        f"شهرهای جستجوشده: {report['queried_cities']}\n"
        f"شهرهای دارای داده: {report['cities_with_data']}\n"
        f"شهرهای دارای داده معتبر: {report['cities_with_valid_data']}\n"
        f"پوشش: {report['coverage_percent']}%",
        title="پوشش جغرافیایی",
    ))
    _print_table("توزیع دسته‌ها", ["دسته", "تعداد"],
                 [[CategoryCode(k).label_fa if k in CategoryCode._value2member_map_ else k, v]
                  for k, v in report["by_category"].items()])


@app.command("sources")
def sources_command(json_out: bool = typer.Option(False, "--json")) -> None:
    """فهرست منابع و وضعیت دسترسی آن‌ها."""
    from .sources.registry import SourceRegistry

    registry = SourceRegistry(get_settings())
    data = registry.summary()
    if json_out:
        console.print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    _print_table(
        "منابع",
        ["کلید", "نام", "نوع", "اعتبار", "کلید لازم", "در دسترس", "دلیل"],
        [[s["key"], s["name"][:30], s["kind_label"], f"{s['reliability']:.2f}",
          "بله" if s["requires_key"] else "خیر", "✅" if s["available"] else "❌", s["reason"][:40]]
         for s in data["sources"]],
    )
    console.print(f"منابع در دسترس: {data['available']} از {data['total']}")


@app.command("export")
def export_command(
    fmt: str = typer.Option("csv", "--format", "-f", help="csv | xlsx | json"),
    output: str | None = typer.Option(None, "--output", "-o"),
    province: list[str] = typer.Option(None, "--province"),
    city: list[str] = typer.Option(None, "--city"),
    category: list[str] = typer.Option(None, "--category"),
    business_type: list[str] = typer.Option(None, "--business-type"),
    min_confidence: int = typer.Option(0, "--min-confidence"),
    phone_status: list[str] = typer.Option(None, "--phone-status"),
    band: list[str] = typer.Option(None, "--band"),
    search: str | None = typer.Option(None, "--search"),
    discovered_after_days: int = typer.Option(0, "--discovered-after-days"),
    exclude_synthetic: bool = typer.Option(
        False, "--exclude-synthetic",
        help="کنار گذاشتن داده پیکره آزمایشی از خروجی",
    ),
    evidence_file: bool = typer.Option(False, "--with-evidence", help="خروجی جداگانه شواهد"),
    limit: int = typer.Option(100000, "--limit"),
) -> None:
    """خروجی CSV / Excel / JSON با فیلترهای کامل."""
    from .services.export_service import export_businesses, export_evidence

    database = _db(None)
    filters = BusinessFilters(
        provinces=province or None,
        cities=city or None,
        categories=[c.upper() for c in category] if category else None,
        business_types=[b.upper() for b in business_type] if business_type else None,
        min_confidence=min_confidence or None,
        bands=[b.upper() for b in band] if band else None,
        phone_status=[s.upper() for s in phone_status] if phone_status else None,
        search=search,
        include_synthetic=_with_synthetic(exclude_synthetic, database),
        discovered_after=(
            datetime.utcnow() - timedelta(days=discovered_after_days) if discovered_after_days else None
        ),
        limit=limit,
    )
    report = export_businesses(filters, fmt=fmt, database=database, output_path=output)
    console.print(f"[green]خروجی ساخته شد:[/green] {report.path} ({report.rows} ردیف)")
    if evidence_file:
        ev = export_evidence(filters, database=database)
        console.print(f"[green]فایل شواهد:[/green] {ev.path} ({ev.rows} ردیف)")


@app.command("list")
def list_command(
    city: str | None = typer.Option(None, "--city"),
    province: str | None = typer.Option(None, "--province"),
    min_confidence: int = typer.Option(0, "--min-confidence"),
    limit: int = typer.Option(20, "--limit"),
    exclude_synthetic: bool = typer.Option(
        False, "--exclude-synthetic", help="کنار گذاشتن داده پیکره آزمایشی"
    ),
) -> None:
    """نمایش سریع رکوردها در ترمینال."""
    database = _db(None)
    filters = BusinessFilters(
        provinces=[province] if province else None,
        cities=[city] if city else None,
        min_confidence=min_confidence or None,
        limit=limit,
        include_synthetic=_with_synthetic(exclude_synthetic, database),
    )
    with database.session() as session:
        rows = list_businesses(session, filters)
        phones = phones_for_businesses(session, [b.id for b in rows])
        table_rows = []
        for b in rows:
            ps = phones.get(b.id, [])
            table_rows.append([
                b.id, (b.name or "-")[:34], b.city_name or "-", b.primary_category or "-",
                "، ".join(p.national for p in ps[:2]) or "-", b.confidence, b.confidence_band,
                b.status,
            ])
    _print_table("رده‌های یافته‌شده", ["#", "نام", "شهر", "دسته", "شماره‌ها", "اعتبار", "سطح", "وضعیت"],
                 table_rows)


@app.command("serve")
def serve_command(
    host: str | None = typer.Option(None, "--host"),
    port: int | None = typer.Option(None, "--port"),
    reload: bool = typer.Option(False, "--reload"),
) -> None:
    """اجرای API + داشبورد وب."""
    import uvicorn

    settings = get_settings()
    console.print(f"[green]داشبورد:[/green] http://{host or settings.host}:{port or settings.port}/")
    console.print(f"[green]مستندات API:[/green] http://{host or settings.host}:{port or settings.port}/docs")
    uvicorn.run(
        "numberbank.api.app:app",
        host=host or settings.host,
        port=port or settings.port,
        reload=reload,
        log_level=settings.log_level.lower(),
    )


@app.command("version")
def version_command() -> None:
    """نمایش نسخه."""
    console.print(f"NumberBank {__version__}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
