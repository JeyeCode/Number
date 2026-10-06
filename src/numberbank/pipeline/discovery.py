"""اجراکننده اصلی کشف داده: صف وظایف، کارگرهای موازی، ثبت نتایج و پیشرفت."""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select

from ..config import Settings, get_settings
from ..crawl.http import Fetcher
from ..db.session import Database, get_database
from ..dedup.fingerprint import query_fingerprint
from ..domain.enums import JobStatus, QueryState
from ..domain.models import (
    JobEvent,
    SearchHistory,
    SearchJob,
    SearchQuery,
    utcnow,
)
from ..errors import DiscoveryError, RobotsDisallowed, SourceUnavailable
from ..extract.page import extract_from_page
from ..logging_setup import get_logger
from ..sources.base import SearchQuerySpec, SourceAdapter
from ..sources.registry import SourceRegistry
from .candidates import candidate_from_result_item
from .persist import Persister

log = get_logger("discovery")


@dataclass
class JobReport:
    job_id: int
    status: str = JobStatus.PENDING.value
    queries_run: int = 0
    pages_fetched: int = 0
    results_seen: int = 0
    candidate_pages_fetched: int = 0
    businesses_new: int = 0
    businesses_updated: int = 0
    phones_new: int = 0
    phones_reused: int = 0
    duplicates: int = 0
    irrelevant_rejected: int = 0
    spam_rejected: int = 0
    synthetic_seen: int = 0
    errors: int = 0
    stop_reason: str | None = None
    duration_ms: int = 0
    new_business_ids: list[int] = field(default_factory=list)
    new_phone_ids: list[int] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "messages"}


class DiscoveryRunner:
    """اجرای یک Job کشف داده با معماری Queue/Worker درون‌فرآیندی و مقاوم به خطا."""

    def __init__(
        self,
        database: Database | None = None,
        *,
        settings: Settings | None = None,
        registry: SourceRegistry | None = None,
        fetcher: Fetcher | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.database = database or get_database(self.settings)
        self.registry = registry or SourceRegistry(self.settings)
        self._fetcher = fetcher
        self._visited_urls: set[str] = set()
        self._persist_lock = asyncio.Lock()
        self._cancel = False

    # ------------------------------------------------------------------ #
    async def fetcher(self) -> Fetcher:
        if self._fetcher is None:
            self._fetcher = Fetcher(self.settings, database=self.database)
        return self._fetcher

    async def aclose(self) -> None:
        if self._fetcher is not None:
            await self._fetcher.aclose()

    def request_cancel(self) -> None:
        self._cancel = True

    # ------------------------------------------------------------------ #
    def _event(self, session, job_id: int | None, level: str, code: str, message: str, **context) -> None:
        session.add(
            JobEvent(job_id=job_id, level=level, code=code, message=message[:800], context=context or {})
        )

    def _load_job(self, job_id: int) -> SearchJob:
        with self.database.session() as session:
            job = session.get(SearchJob, job_id)
            if job is None:
                raise ValueError(f"Job با شناسه {job_id} یافت نشد")
            return job

    async def run(self, job_id: int, *, plan_only: bool = False) -> JobReport:
        started = time.monotonic()
        report = JobReport(job_id=job_id)
        job = self._load_job(job_id)
        params = dict(job.params or {})
        settings = self.settings

        job_name = job.name
        with self.database.session() as session:
            row = session.get(SearchJob, job_id)
            if row is None:
                raise DiscoveryError(f"Job {job_id} یافت نشد")
            row.status = JobStatus.RUNNING.value
            row.started_at = utcnow()
            row.heartbeat_at = utcnow()
            row.progress = 0.0
            session.flush()
            self._event(session, job_id, "INFO", "JOB_STARTED", f"شروع Job «{job_name}»", params=params)

        try:
            from .planner import QueryPlanner

            with self.database.session() as session:
                planner = QueryPlanner(session, settings)
                scopes = planner.resolve_scope(
                    provinces=params.get("provinces"),
                    counties=params.get("counties"),
                    cities=params.get("cities"),
                    limit_cities=params.get("limit_cities"),
                    prefer_small_cities=params.get("prefer_small_cities", True),
                )
                source_keys = self._resolve_source_keys(params)
                plan = planner.build_tasks(
                    scopes=scopes,
                    categories=params.get("categories") or [],
                    business_types=params.get("business_types") or [],
                    source_keys=source_keys,
                    max_pages=params.get("max_pages_per_query") or settings.max_pages_per_query,
                    budget=params.get("max_pages_per_job") or settings.max_pages_per_job,
                    include_extra_shapes=bool(params.get("expand_query_shapes", True)),
                )
                job = session.get(SearchJob, job_id)
                job.total_queries = len(plan.tasks)
                self._event(
                    session, job_id, "INFO", "PLAN_READY",
                    f"{len(plan.tasks)} وظیفه برنامه‌ریزی شد",
                    cities=len(scopes), new_combinations=plan.new_combinations,
                    deeper=plan.deeper_queries, exhausted=plan.skipped_exhausted,
                    sources=source_keys,
                )
                jobs_tasks = list(plan.tasks)

            if plan_only:
                report.status = JobStatus.PENDING.value
                report.messages.append(f"{len(jobs_tasks)} وظیفه آماده اجرا شد")
                return report

            if not jobs_tasks:
                with self.database.session() as session:
                    job = session.get(SearchJob, job_id)
                    job.status = JobStatus.DONE.value
                    job.finished_at = utcnow()
                    job.progress = 100.0
                    job.message = "همه ترکیب‌های پرس‌وجو پیش‌تر اجرا شده‌اند؛ Job جدیدی برای اجرا نبود."
                    self._event(session, job_id, "WARNING", "NOTHING_TO_DO",
                                "ترکیب تازه‌ای برای اجرا یافت نشد (داده‌ها به‌روز هستند)")
                report.status = JobStatus.DONE.value
                report.stop_reason = "no_new_queries"
                report.duration_ms = int((time.monotonic() - started) * 1000)
                return report

            await self._execute_tasks(job_id, jobs_tasks, params, report, started)

            # پاک‌سازی و ادغام تکراری‌های تازه‌کشف‌شده
            if params.get("run_dedup", True):
                try:
                    from .dedup import Deduplicator

                    with self.database.session() as session:
                        dedup_report = Deduplicator(session, settings).run(job_id=job_id, batch_size=4000)
                        report.duplicates += dedup_report.merged
                        self._event(session, job_id, "INFO", "DEDUP_DONE",
                                    f"{dedup_report.clusters} خوشه تکراری، {dedup_report.merged} رکورد ادغام شد",
                                    **dedup_report.as_dict())
                except Exception as exc:  # dedup نباید نتیجه Job را از بین ببرد
                    report.errors += 1
                    with self.database.session() as session:
                        self._event(session, job_id, "ERROR", "DEDUP_FAILED", f"خطای ادغام: {exc}")

            report.status = JobStatus.DONE.value if not self._cancel else JobStatus.CANCELLED.value
            report.stop_reason = report.stop_reason or "completed"
        except Exception as exc:  # pragma: no cover - محافظت نهایی
            log.exception("خطای مهلک در اجرای Job %s", job_id)
            report.status = JobStatus.FAILED.value
            report.errors += 1
            report.stop_reason = f"fatal: {exc}"
            with self.database.session() as session:
                job = session.get(SearchJob, job_id)
                if job is not None:
                    job.status = JobStatus.FAILED.value
                    job.message = f"خطای مهلک: {exc}"[:2000]
                    job.finished_at = utcnow()
                self._event(session, job_id, "ERROR", "JOB_FAILED", str(exc)[:500])
        finally:
            report.duration_ms = int((time.monotonic() - started) * 1000)
            with self.database.session() as session:
                job = session.get(SearchJob, job_id)
                if job is not None:
                    # وضعیت پایانی همیشه ثبت می‌شود (DONE / CANCELLED / FAILED)
                    terminal = {
                        JobStatus.DONE.value: JobStatus.DONE.value,
                        JobStatus.CANCELLED.value: JobStatus.CANCELLED.value,
                        JobStatus.FAILED.value: JobStatus.FAILED.value,
                    }.get(report.status, JobStatus.DONE.value)
                    if job.status not in (JobStatus.FAILED.value, JobStatus.CANCELLED.value):
                        job.status = terminal
                    job.finished_at = utcnow()
                    job.progress = 100.0 if job.status != JobStatus.FAILED.value else job.progress
                    job.message = (job.message or "")[:2000]
                    if report.stop_reason:
                        job.message = (job.message or "") + f" | پایان: {report.stop_reason}"
            await self.aclose()
        return report

    # ------------------------------------------------------------------ #
    def _resolve_source_keys(self, params: dict) -> list[str]:
        requested = params.get("sources") or []
        if requested:
            available = {s.key for s in self.registry.available()}
            chosen = [k for k in requested if k in available]
            if not chosen:
                chosen = [s.key for s in self.registry.available()]
            return chosen
        if self.settings.offline:
            return [s.key for s in self.registry.available() if s.kind.value == "FIXTURE"]
        return [s.key for s in self.registry.available()]

    # ------------------------------------------------------------------ #
    async def _execute_tasks(
        self,
        job_id: int,
        tasks: list,
        params: dict,
        report: JobReport,
        started: float,
    ) -> None:
        settings = self.settings
        target = int(params.get("target") or settings.default_target)
        min_confidence = int(params.get("min_confidence") or settings.min_confidence)
        time_limit = int(params.get("time_limit") or settings.job_time_limit)
        workers = max(1, int(params.get("workers") or settings.workers))
        fetch_pages = bool(params.get("fetch_candidate_pages", settings.fetch_candidate_pages))
        max_candidate_pages = int(params.get("max_candidate_pages_per_query") or settings.max_candidate_pages_per_query)

        sem = asyncio.Semaphore(workers)
        budget_lock = asyncio.Lock()
        totals = {"queries": 0, "pages": 0, "results": 0, "cand_pages": 0, "businesses_new": 0,
                  "phones_new": 0, "updated": 0, "dup": 0, "irrelevant": 0, "spam": 0, "synthetic": 0,
                  "errors": 0, "qualified": 0}
        stop_flag = {"stop": False, "reason": None}
        fetcher = await self.fetcher()

        async def stop_check() -> bool:
            if stop_flag["stop"]:
                return True
            if self._cancel:
                stop_flag.update(stop=True, reason="cancel_requested")
                return True
            if time.monotonic() - started > time_limit:
                stop_flag.update(stop=True, reason="time_limit")
                return True
            if totals["qualified"] >= target:
                stop_flag.update(stop=True, reason="target_reached")
                return True
            async with budget_lock:
                with self.database.session() as session:
                    job = session.get(SearchJob, job_id)
                    if job is not None and job.cancel_requested:
                        stop_flag.update(stop=True, reason="cancel_requested")
                        return True
            return False

        async def process_task(task) -> None:
            if await stop_check():
                return
            async with sem:
                if await stop_check():
                    return
                source = self.registry.get(task.source_key)
                if source is None:
                    return
                spec = SearchQuerySpec(
                    text=task.query_text,
                    page=task.page,
                    results_per_page=settings.results_per_page,
                    province=task.province,
                    county=task.county,
                    city=task.city,
                    category=task.category,
                    business_type=task.business_type,
                )
                query_context = spec.context
                query_row_id = await asyncio.to_thread(
                    self._ensure_query_row, job_id, task, source
                )
                task_started = time.monotonic()
                errors = 0
                new_results = 0
                results_count = 0
                status = "DONE"
                message = None
                try:
                    page = await source.search(spec, fetcher)
                    results_count = len(page.items)
                    totals["results"] += results_count
                    totals["pages"] += 1
                    report.results_seen += results_count

                    candidates = []
                    for item in page.items:
                        candidates.extend(
                            candidate_from_result_item(
                                item,
                                query_context=query_context,
                                source_reliability=source.reliability,
                                source_kind=source.kind.value,
                            )
                        )

                    if candidates:
                        stats = await self._persist(candidates, source.key, job_id, query_row_id)
                        new_results += stats.businesses_new + stats.phones_new
                        totals["businesses_new"] += stats.businesses_new
                        totals["phones_new"] += stats.phones_new
                        totals["updated"] += stats.businesses_updated
                        totals["dup"] += stats.businesses_duplicate
                        totals["irrelevant"] += stats.rejected_irrelevant
                        totals["spam"] += stats.rejected_spam
                        totals["synthetic"] += stats.synthetic
                        report.businesses_new += stats.businesses_new
                        report.businesses_updated += stats.businesses_updated
                        report.phones_new += stats.phones_new
                        report.phones_reused += stats.phones_reused
                        report.new_business_ids.extend(stats.new_business_ids)
                        report.new_phone_ids.extend(stats.new_phone_ids)
                        report.duplicates += stats.businesses_duplicate
                        report.irrelevant_rejected += stats.rejected_irrelevant
                        report.spam_rejected += stats.rejected_spam
                        report.synthetic_seen += stats.synthetic
                        totals["qualified"] += stats.businesses_new

                    # خزش صفحات کاندید برای شواهد غنی‌تر
                    if fetch_pages and max_candidate_pages > 0:
                        fetched = 0
                        for item in page.items:
                            if fetched >= max_candidate_pages or await stop_check():
                                break
                            url = item.url
                            if not url or url in self._visited_urls:
                                continue
                            self._visited_urls.add(url)
                            html = await source.fetch_page(url, fetcher)
                            totals["cand_pages"] += 1
                            fetched += 1
                            if not html:
                                continue
                            page_candidates = extract_from_page(
                                html, url, query_context=query_context, max_candidates=15,
                                source_kind=source.kind.value,
                            )
                            if page_candidates:
                                stats2 = await self._persist(page_candidates, source.key, job_id, query_row_id)
                                new_results += stats2.businesses_new + stats2.phones_new
                                totals["businesses_new"] += stats2.businesses_new
                                totals["phones_new"] += stats2.phones_new
                                totals["irrelevant"] += stats2.rejected_irrelevant
                                totals["spam"] += stats2.rejected_spam
                                report.businesses_new += stats2.businesses_new
                                report.phones_new += stats2.phones_new
                                report.businesses_updated += stats2.businesses_updated
                                report.irrelevant_rejected += stats2.rejected_irrelevant
                                totals["qualified"] += stats2.businesses_new
                except RobotsDisallowed as exc:
                    errors += 1
                    status = "BLOCKED"
                    message = str(exc)
                    totals["errors"] += 1
                    report.errors += 1
                except SourceUnavailable as exc:
                    errors += 1
                    status = "FAILED"
                    message = str(exc)
                    totals["errors"] += 1
                    report.errors += 1
                except Exception as exc:  # یک پرس‌وجو هرگز کل Job را متوقف نمی‌کند
                    errors += 1
                    status = "FAILED"
                    message = f"{type(exc).__name__}: {exc}"
                    totals["errors"] += 1
                    report.errors += 1
                    log.debug("خطا در وظیفه %s/%s: %s", source.key, task.query_text, message)
                finally:
                    totals["queries"] += 1
                    duration_ms = int((time.monotonic() - task_started) * 1000)
                    await asyncio.to_thread(
                        self._finalize_query_row,
                        query_row_id, task, job_id, source.key, task.query_text,
                        status, results_count, new_results, errors, duration_ms, message, min_confidence,
                    )
                    await asyncio.to_thread(self._update_job_progress, job_id, totals, len(tasks), report)

        await asyncio.gather(*(process_task(t) for t in tasks), return_exceptions=True)

        report.queries_run = totals["queries"]
        report.pages_fetched = totals["pages"]
        report.candidate_pages_fetched = totals["cand_pages"]
        report.stop_reason = stop_flag["reason"] or "completed"
        if stop_flag["stop"]:
            report.messages.append(f"توقف زودهنگام: {stop_flag['reason']}")

    # ------------------------------------------------------------------ #
    async def _persist(self, candidates, source_key: str, job_id: int, query_id: int | None):
        """ذخیره دسته‌ای کاندیدها؛ در صورت برخورد یکتایی، هر رکورد مستقل تلاش می‌شود."""
        from sqlalchemy.exc import IntegrityError

        from .persist import PersistStats

        def _work() -> PersistStats:
            with self.database.session() as session:
                persister = Persister(session, self.settings)
                try:
                    return persister.upsert_candidates(
                        candidates, source_key=source_key, job_id=job_id, query_id=query_id
                    )
                except IntegrityError as exc:
                    log.warning("برخورد یکتایی در ذخیره دسته‌ای (%s) — تلاش رکوردبه‌رکورد", exc.orig)
                    session.rollback()
            # مسیر جایگزین: هر کاندید در تراکنش مستقل
            totals = PersistStats()
            for cand in candidates:
                with self.database.session() as session:
                    persister = Persister(session, self.settings)
                    try:
                        with session.begin_nested():
                            stats = persister.upsert_candidates(
                                [cand], source_key=source_key, job_id=job_id, query_id=query_id
                            )
                        _merge_stats(totals, stats)
                    except IntegrityError:
                        continue
                    except Exception as exc:  # داده معیوب یک رکورد، بقیه را متوقف نمی‌کند
                        log.debug("رد کردن کاندید معیوب: %s", exc)
                        continue
            return totals

        async with self._persist_lock:
            return await asyncio.to_thread(_work)

    # ------------------------------------------------------------------ #
    def _ensure_query_row(self, job_id: int, task, source: SourceAdapter) -> int:
        qhash = query_fingerprint(source.key, task.query_text)
        with self.database.session() as session:
            row = session.execute(
                select(SearchQuery).where(
                    SearchQuery.source_key == source.key, SearchQuery.query_hash == qhash
                )
            ).scalar_one_or_none()
            if row is None:
                row = SearchQuery(
                    job_id=job_id,
                    source_key=source.key,
                    query_text=task.query_text,
                    query_hash=qhash,
                    province_name=task.province,
                    county_name=task.county,
                    city_name=task.city,
                    category=task.category,
                    business_type=task.business_type,
                    state=QueryState.PENDING.value,
                    depth=0,
                    max_depth=self.settings.max_pages_per_query,
                )
                session.add(row)
                session.flush()
            elif row.job_id is None:
                row.job_id = job_id
            return row.id

    def _finalize_query_row(
        self,
        query_id: int,
        task,
        job_id: int,
        source_key: str,
        query_text: str,
        status: str,
        results: int,
        new_results: int,
        errors: int,
        duration_ms: int,
        message: str | None,
        min_confidence: int,
    ) -> None:
        with self.database.session() as session:
            row = session.get(SearchQuery, query_id)
            if row is not None:
                row.run_count = (row.run_count or 0) + 1
                row.pages_fetched = (row.pages_fetched or 0) + (1 if status == "DONE" else 0)
                row.results_seen = (row.results_seen or 0) + results
                row.new_results = new_results
                row.failures = (row.failures or 0) + errors
                row.last_run_at = utcnow()
                if status == "BLOCKED":
                    row.state = QueryState.BLOCKED.value
                    row.next_run_at = None
                elif status == "FAILED":
                    row.state = QueryState.COOLING.value
                    row.next_run_at = utcnow() + timedelta(hours=6 * max(1, row.failures))
                else:
                    row.depth = max(row.depth or 0, task.page)
                    if new_results == 0 and task.page >= 1:
                        # اشباع در این عمق: عقب‌نشینی موقت تا زمان تازه‌سازی
                        row.state = QueryState.DONE.value
                        row.next_run_at = utcnow() + timedelta(days=self.settings.query_refresh_days)
                    else:
                        row.state = QueryState.DONE.value
                        row.next_run_at = None
            session.add(
                SearchHistory(
                    query_id=query_id,
                    job_id=job_id,
                    source_key=source_key,
                    query_text=query_text[:500],
                    status=status,
                    pages=1 if status == "DONE" else 0,
                    results=results,
                    new_results=new_results,
                    errors=errors,
                    duration_ms=duration_ms,
                    message=(message or "")[:1000] or None,
                    started_at=utcnow(),
                    finished_at=utcnow(),
                )
            )

    def _update_job_progress(self, job_id: int, totals: dict, total_tasks: int, report: JobReport) -> None:
        with self.database.session() as session:
            job = session.get(SearchJob, job_id)
            if job is None:
                return
            job.done_queries = totals["queries"]
            job.pages_fetched = totals["pages"]
            job.results_seen = totals["results"]
            job.businesses_new = totals["businesses_new"]
            job.businesses_updated = totals["updated"]
            job.phones_new = totals["phones_new"]
            job.duplicates_found = totals["dup"]
            job.irrelevant_rejected = totals["irrelevant"]
            job.errors = totals["errors"]
            job.progress = round(100.0 * totals["queries"] / max(1, total_tasks), 2)
            job.heartbeat_at = utcnow()


async def run_discovery_job(job_id: int, *, database: Database | None = None, settings: Settings | None = None,
                            registry: SourceRegistry | None = None) -> JobReport:
    runner = DiscoveryRunner(database, settings=settings, registry=registry)
    try:
        return await runner.run(job_id)
    finally:
        await runner.aclose()


def _merge_stats(target, extra) -> None:
    """جمع آمار دو دسته ذخیره‌سازی."""
    for name in ("candidates", "businesses_new", "businesses_updated", "businesses_duplicate",
                 "phones_new", "phones_reused", "observations", "observations_duplicate",
                 "rejected_irrelevant", "rejected_spam", "conflicts", "synthetic"):
        setattr(target, name, getattr(target, name, 0) + getattr(extra, name, 0))
    target.new_business_ids.extend(getattr(extra, "new_business_ids", []) or [])
    target.new_phone_ids.extend(getattr(extra, "new_phone_ids", []) or [])


@contextlib.asynccontextmanager
async def temporary_fetcher(settings: Settings | None = None, database: Database | None = None):
    f = Fetcher(settings or get_settings(), database=database)
    try:
        yield f
    finally:
        await f.aclose()
