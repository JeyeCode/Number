"""کارگرهای پردازش صف: اجرای Job، خزش URL و بازاعتبارسنجی."""

from __future__ import annotations

import asyncio
import signal
import socket
import time
from dataclasses import dataclass
from typing import Any

from ..config import Settings, get_settings
from ..crawl.http import Fetcher
from ..db.session import Database, get_database
from ..extract.page import extract_from_page
from ..logging_setup import get_logger
from ..pipeline.discovery import DiscoveryRunner
from ..pipeline.persist import Persister
from ..pipeline.validation import ValidationService
from ..sources.registry import SourceRegistry
from .sqlite_queue import SqliteQueue

log = get_logger("worker")

TASK_RUN_JOB = "run_job"
TASK_CRAWL_URL = "crawl_url"
TASK_REVALIDATE = "revalidate"
TASK_PLAN_JOB = "plan_job"


@dataclass
class WorkerReport:
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    dead: int = 0
    loops: int = 0
    stop_reason: str = "completed"

    def as_dict(self) -> dict:
        return self.__dict__.copy()


class WorkerPool:
    """چند کارگر موازی با خاموشی نرم (Graceful Shutdown) و Heartbeat."""

    def __init__(
        self,
        *,
        database: Database | None = None,
        settings: Settings | None = None,
        workers: int | None = None,
        queue_name: str = "default",
    ) -> None:
        self.settings = settings or get_settings()
        self.database = database or get_database(self.settings)
        self.workers = workers or self.settings.workers
        self.queue = SqliteQueue(self.database, self.settings, queue_name=queue_name)
        self.registry = SourceRegistry(self.settings)
        self._stop = asyncio.Event()
        self.worker_id = f"{socket.gethostname()}-{int(time.time())%100000}"

    def request_stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------ #
    async def process_task(self, task, fetcher: Fetcher) -> dict[str, Any]:
        if task.task_type == TASK_RUN_JOB:
            job_id = int(task.payload.get("job_id"))
            runner = DiscoveryRunner(self.database, settings=self.settings, registry=self.registry,
                                     fetcher=fetcher)
            report = await runner.run(job_id)
            return report.as_dict()
        if task.task_type == TASK_PLAN_JOB:
            from ..services.discovery_service import run_job

            report = await run_job(job_id=int(task.payload.get("job_id")), database=self.database,
                                   settings=self.settings, plan_only=True)
            return report.as_dict()
        if task.task_type == TASK_REVALIDATE:
            with self.database.session() as session:
                service = ValidationService(session, self.settings)
                report = service.revalidate_due(limit=int(task.payload.get("limit", 200)),
                                                force=bool(task.payload.get("force")))
            return report.as_dict()
        if task.task_type == TASK_CRAWL_URL:
            url = task.payload.get("url")
            source_key = task.payload.get("source_key") or "manual"
            query_context = task.payload.get("query_context") or {}
            if not url:
                raise ValueError("payload بدون url")
            result = await fetcher.fetch(url, purpose="page")
            if not result.ok or not result.text:
                raise RuntimeError(f"دریافت ناموفق: {result.error}")
            candidates = extract_from_page(result.text, url, query_context=query_context, max_candidates=25)
            with self.database.session() as session:
                stats = Persister(session, self.settings).upsert_candidates(
                    candidates, source_key=source_key, job_id=task.payload.get("job_id")
                )
            return {"candidates": stats.candidates, "businesses_new": stats.businesses_new,
                    "phones_new": stats.phones_new}
        raise ValueError(f"نوع کار ناشناخته: {task.task_type}")

    # ------------------------------------------------------------------ #
    async def run_until_empty(self, *, max_loops: int = 50, idle_sleep: float = 1.0) -> WorkerReport:
        report = WorkerReport()
        fetcher = Fetcher(self.settings, database=self.database)
        try:
            for loop in range(max_loops):
                report.loops = loop + 1
                if self._stop.is_set():
                    report.stop_reason = "stop_requested"
                    break
                self.queue.release_expired_leases()
                tasks = self.queue.lease(self.worker_id, limit=self.workers)
                if not tasks:
                    report.stop_reason = "queue_empty"
                    break
                results = await asyncio.gather(
                    *(self._process_one(t, fetcher, report) for t in tasks), return_exceptions=True
                )
                for res in results:
                    if isinstance(res, Exception):  # pragma: no cover
                        log.error("خطای حل‌نشده کارگر: %s", res)
                await asyncio.sleep(idle_sleep)
        finally:
            await fetcher.aclose()
        return report

    async def _process_one(self, task, fetcher: Fetcher, report: WorkerReport) -> None:
        report.processed += 1
        try:
            result = await self.process_task(task, fetcher)
            self.queue.complete(task.id, result if isinstance(result, dict) else {"ok": True})
            report.succeeded += 1
            log.info("کار %s (%s) با موفقیت انجام شد", task.id, task.task_type)
        except Exception as exc:
            state = self.queue.fail(task.id, f"{type(exc).__name__}: {exc}")
            report.failed += 1
            if state == "DEAD":
                report.dead += 1
            log.warning("کار %s ناموفق بود (%s): %s", task.id, state, exc)

    async def serve_forever(self, *, poll_interval: float = 2.0) -> WorkerReport:
        """حالت سرویس دائمی — تا دریافت سیگنال توقف."""

        def _handle(signum, _frame):  # pragma: no cover - وابسته به سیگنال
            log.info("سیگنال %s دریافت شد؛ خاموشی نرم...", signum)
            self.request_stop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handle)
            except (ValueError, OSError):  # pragma: no cover
                pass

        report = WorkerReport()
        fetcher = Fetcher(self.settings, database=self.database)
        try:
            while not self._stop.is_set():
                self.queue.release_expired_leases()
                tasks = self.queue.lease(self.worker_id, limit=self.workers)
                if not tasks:
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=poll_interval)
                    except asyncio.TimeoutError:
                        pass
                    continue
                report.loops += 1
                await asyncio.gather(*(self._process_one(t, fetcher, report) for t in tasks),
                                     return_exceptions=True)
        finally:
            await fetcher.aclose()
        report.stop_reason = "stop_requested"
        return report


async def run_workers_once(*, database: Database | None = None, settings: Settings | None = None,
                           workers: int | None = None) -> WorkerReport:
    pool = WorkerPool(database=database, settings=settings, workers=workers)
    return await pool.run_until_empty()
