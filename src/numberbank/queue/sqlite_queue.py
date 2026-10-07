"""صف پایدار روی پایگاه داده: Lease، Retry با Backoff، Dead-Letter و Idempotency."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from ..config import Settings, get_settings
from ..db.session import Database, get_database
from ..domain.enums import TaskState
from ..domain.models import QueueTask, utcnow
from ..logging_setup import get_logger

log = get_logger("queue")

BACKOFF_BASE_SECONDS = 30


@dataclass
class QueueStats:
    queued: int = 0
    leased: int = 0
    done: int = 0
    failed: int = 0
    dead: int = 0

    @property
    def total(self) -> int:
        return self.queued + self.leased + self.done + self.failed + self.dead

    def as_dict(self) -> dict:
        return {"queued": self.queued, "leased": self.leased, "done": self.done,
                "failed": self.failed, "dead": self.dead, "total": self.total}


class SqliteQueue:
    """صف با تضمین «حداقل یک‌بار اجرا» و محافظت از کارهای تکراری."""

    def __init__(self, database: Database | None = None, settings: Settings | None = None,
                 *, queue_name: str = "default") -> None:
        self.settings = settings or get_settings()
        self.database = database or get_database(self.settings)
        self.queue_name = queue_name

    # ------------------------------------------------------------------ #
    def enqueue(
        self,
        task_type: str,
        payload: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
        priority: int = 100,
        delay_seconds: int = 0,
        max_attempts: int | None = None,
    ) -> int | None:
        key = idempotency_key or f"{task_type}:{uuid.uuid4().hex[:16]}"
        with self.database.session() as session:
            exists = session.execute(
                select(QueueTask).where(QueueTask.idempotency_key == key)
            ).scalar_one_or_none()
            if exists is not None:
                if exists.state in (TaskState.DONE.value, TaskState.QUEUED.value,
                                    TaskState.LEASED.value):
                    return None  # کار تکراری ثبت نمی‌شود
                exists.state = TaskState.QUEUED.value
                exists.available_at = utcnow() + timedelta(seconds=delay_seconds)
                exists.payload = payload or {}
                return exists.id
            task = QueueTask(
                queue=self.queue_name,
                task_type=task_type,
                payload=payload or {},
                idempotency_key=key,
                priority=priority,
                max_attempts=max_attempts or self.settings.max_attempts,
                available_at=utcnow() + timedelta(seconds=delay_seconds),
            )
            session.add(task)
            session.flush()
            return task.id

    # ------------------------------------------------------------------ #
    def lease(self, worker_id: str, *, limit: int = 1) -> list[QueueTask]:
        tasks: list[QueueTask] = []
        with self.database.session() as session:
            now = utcnow()
            rows = (
                session.execute(
                    select(QueueTask)
                    .where(
                        QueueTask.queue == self.queue_name,
                        QueueTask.state.in_([TaskState.QUEUED.value, TaskState.LEASED.value]),
                        QueueTask.available_at <= now,
                    )
                    .order_by(QueueTask.priority.asc(), QueueTask.id.asc())
                    .limit(limit * 3)
                )
                .scalars()
                .all()
            )
            for row in rows:
                if row.state == TaskState.LEASED.value:
                    if row.lease_expires_at and row.lease_expires_at > now:
                        continue  # در دست کارگر دیگری است
                    log.warning("Lease منقضی‌شده برای کار %s — آزادسازی", row.id)
                row.state = TaskState.LEASED.value
                row.worker_id = worker_id
                row.leased_at = now
                row.lease_expires_at = now + timedelta(seconds=self.settings.lease_seconds)
                row.attempts = (row.attempts or 0) + 1
                tasks.append(row)
                if len(tasks) >= limit:
                    break
            session.flush()
            # جدا کردن از نشست (بعد از commit مقادیر لازم را داریم)
            for t in tasks:
                session.expunge(t)
        return tasks

    # ------------------------------------------------------------------ #
    def complete(self, task_id: int, result: dict[str, Any] | None = None) -> None:
        with self.database.session() as session:
            task = session.get(QueueTask, task_id)
            if task is None:
                return
            task.state = TaskState.DONE.value
            task.result = result or {}
            task.finished_at = utcnow()
            task.error = None

    def fail(self, task_id: int, error: str, *, retry: bool = True) -> str:
        with self.database.session() as session:
            task = session.get(QueueTask, task_id)
            if task is None:
                return TaskState.DEAD.value
            task.error = error[:2000]
            if retry and (task.attempts or 0) < (task.max_attempts or 3):
                task.state = TaskState.QUEUED.value
                delay = BACKOFF_BASE_SECONDS * (2 ** max(0, (task.attempts or 1) - 1))
                task.available_at = utcnow() + timedelta(seconds=min(delay, 3600))
                task.worker_id = None
                task.lease_expires_at = None
                log.info("کار %s برای تلاش %s/%s دوباره زمان‌بندی شد (تأخیر %ss)",
                         task.id, (task.attempts or 0) + 1, task.max_attempts, delay)
                return TaskState.QUEUED.value
            task.state = TaskState.DEAD.value
            task.finished_at = utcnow()
            log.error("کار %s به Dead-Letter رفت: %s", task.id, error[:200])
            return TaskState.DEAD.value

    # ------------------------------------------------------------------ #
    def release_expired_leases(self) -> int:
        with self.database.session() as session:
            now = utcnow()
            rows = (
                session.execute(
                    select(QueueTask).where(
                        QueueTask.state == TaskState.LEASED.value,
                        QueueTask.lease_expires_at.is_not(None),
                        QueueTask.lease_expires_at < now,
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                row.state = TaskState.QUEUED.value
                row.worker_id = None
                row.lease_expires_at = None
                row.available_at = now
            return len(rows)

    def stats(self) -> QueueStats:
        with self.database.session() as session:
            rows = dict(
                session.execute(
                    select(QueueTask.state, func.count(QueueTask.id))
                    .where(QueueTask.queue == self.queue_name)
                    .group_by(QueueTask.state)
                ).all()
            )
        return QueueStats(
            queued=int(rows.get(TaskState.QUEUED.value, 0)),
            leased=int(rows.get(TaskState.LEASED.value, 0)),
            done=int(rows.get(TaskState.DONE.value, 0)),
            failed=int(rows.get(TaskState.FAILED.value, 0)),
            dead=int(rows.get(TaskState.DEAD.value, 0)),
        )

    def dead_letters(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.session() as session:
            rows = (
                session.execute(
                    select(QueueTask)
                    .where(QueueTask.state == TaskState.DEAD.value)
                    .order_by(QueueTask.id.desc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            return [
                {"id": r.id, "type": r.task_type, "error": r.error, "attempts": r.attempts,
                 "payload": r.payload}
                for r in rows
            ]

    def purge_done(self, older_than_hours: int = 48) -> int:
        with self.database.session() as session:
            cutoff = datetime.utcnow() - timedelta(hours=older_than_hours)
            return session.query(QueueTask).filter(
                QueueTask.state == TaskState.DONE.value, QueueTask.finished_at < cutoff
            ).delete(synchronize_session=False)
