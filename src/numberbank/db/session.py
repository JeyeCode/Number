"""نشست پایگاه داده: موتور، ساخت جداول، و Context Manager تراکنش."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings, get_settings
from ..domain.models import Base, SchemaMeta
from ..logging_setup import get_logger

log = get_logger("db")
SCHEMA_VERSION = "1.0.0"


def build_engine(settings: Settings | None = None) -> Engine:
    settings = settings or get_settings()
    url = settings.database_url
    kwargs: dict = {"echo": settings.db_echo, "future": True}

    if settings.is_sqlite:
        path = settings.resolve_sqlite_path()
        if path is not None:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    else:
        kwargs.update(pool_size=settings.db_pool_size, pool_pre_ping=True, max_overflow=10)

    engine = create_engine(url, **kwargs)

    if settings.is_sqlite:

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - وابسته به درایور
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

    return engine


class Database:
    """پوشش سبک روی Engine برای مدیریت نشست‌ها."""

    def __init__(self, settings: Settings | None = None, engine: Engine | None = None) -> None:
        self.settings = settings or get_settings()
        self.engine = engine or build_engine(self.settings)
        # autoflush روشن است تا بررسی‌های «وجود دارد؟» ردیف‌های در انتظار را هم ببینند
        self.session_factory = sessionmaker(
            bind=self.engine, autoflush=True, expire_on_commit=False, class_=Session
        )

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)
        _stamp_schema(self.engine)
        log.debug("جداول پایگاه داده ساخته/بررسی شد")

    @contextlib.contextmanager
    def session(self) -> Iterator[Session]:
        s = self.session_factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def healthcheck(self) -> bool:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception as exc:  # pragma: no cover
            log.error("اتصال به پایگاه داده ناموفق: %s", exc)
            return False

    def dispose(self) -> None:
        self.engine.dispose()


def _stamp_schema(engine: Engine) -> None:
    with Session(engine) as s:
        exists = s.query(SchemaMeta).filter_by(version=SCHEMA_VERSION).first()
        if not exists:
            s.add(SchemaMeta(version=SCHEMA_VERSION, note="ایجاد اولیه جداول"))
            s.commit()


_shared: dict[str, Database] = {}


def get_database(settings: Settings | None = None) -> Database:
    """Database مشترک برای هر URL (جلوگیری از ساخت موتور تکراری)."""
    settings = settings or get_settings()
    key = settings.database_url
    if key not in _shared:
        db = Database(settings)
        db.create_all()
        _shared[key] = db
    return _shared[key]


def reset_databases() -> None:
    for db in _shared.values():
        db.dispose()
    _shared.clear()
