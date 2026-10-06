"""رجیستری منابع: ساخت، فعال/غیرفعال‌سازی و انتخاب منابع یک Job."""

from __future__ import annotations

from collections.abc import Iterable

from ..config import Settings, get_settings
from ..logging_setup import get_logger
from .base import SourceAdapter
from .business_directories.configurable import ConfigurableDirectorySource, load_directory_configs
from .fixtures.offline import FixtureSearchSource, fixtures_available
from .open_data.overpass import OverpassDirectorySource
from .search_engines.api_engines import BingAPISource, GoogleCSESource, SerpAPISource
from .search_engines.html_engines import (
    BingHTMLSource,
    DuckDuckGoHTMLSource,
    MojeekHTMLSource,
    SearxSource,
)

log = get_logger("sources.registry")


class SourceRegistry:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._sources: dict[str, SourceAdapter] = {}
        self._build()

    def _build(self) -> None:
        s = self.settings
        add = self._add

        # منابع رسمی/کلیددار
        add(BingAPISource(s))
        add(GoogleCSESource(s))
        add(SerpAPISource(s))

        # داده باز (OSM)
        add(OverpassDirectorySource(s))

        # موتورهای HTML (در صورت فعال بودن)
        if s.enable_unofficial_scrapers:
            add(DuckDuckGoHTMLSource(s))
            add(BingHTMLSource(s))
            add(MojeekHTMLSource(s))
            add(SearxSource(s))

        # دایرکتوری‌های مبتنی بر پیکربندی
        for cfg in load_directory_configs():
            add(ConfigurableDirectorySource(cfg, s))

        # منابع آزمایشی (فقط اگر فیچر موجود باشد)
        if fixtures_available():
            add(FixtureSearchSource("fixture_search_a", s))
            add(FixtureSearchSource("fixture_search_b", s))

    def _add(self, source: SourceAdapter) -> None:
        if source.key in self._sources:
            log.warning("منبع تکراری نادیده گرفته شد: %s", source.key)
            return
        self._sources[source.key] = source

    # ------------------------------------------------------------------ #
    def get(self, key: str) -> SourceAdapter | None:
        return self._sources.get(key)

    def all(self) -> list[SourceAdapter]:
        return list(self._sources.values())

    def available(self) -> list[SourceAdapter]:
        return [s for s in self._sources.values() if s.available()[0]]

    def keys(self) -> list[str]:
        return list(self._sources.keys())

    def resolve(self, keys: Iterable[str] | None, *, default_to_available: bool = True) -> list[SourceAdapter]:
        """انتخاب منابع بر اساس کلیدهای درخواستی کاربر."""
        if keys:
            selected = [self._sources[k] for k in keys if k in self._sources]
            missing = [k for k in keys if k not in self._sources]
            if missing:
                log.warning("منابع ناشناخته نادیده گرفته شدند: %s", missing)
            return selected
        if default_to_available:
            return self.available()
        return []

    def search_sources(self) -> list[SourceAdapter]:
        return [s for s in self.available() if s.supports_search]

    def describe_all(self) -> list[dict]:
        return [s.describe() for s in self._sources.values()]

    def summary(self) -> dict:
        all_sources = self.all()
        avail = [s for s in all_sources if s.available()[0]]
        return {
            "total": len(all_sources),
            "available": len(avail),
            "by_kind": {
                kind: len([s for s in avail if s.kind.value == kind])
                for kind in {s.kind.value for s in avail}
            },
            "sources": self.describe_all(),
        }


def sync_sources_to_db(registry: SourceRegistry, database) -> int:
    """ثبت/به‌روزرسانی منابع در جدول ``sources``."""
    from ..domain.models import Source

    count = 0
    with database.session() as session:
        by_key = {row.key: row for row in session.query(Source).all()}
        for src in registry.all():
            ok, _reason = src.available()
            row = by_key.get(src.key)
            if row is None:
                session.add(
                    Source(
                        key=src.key,
                        name=src.name,
                        kind=src.kind.value,
                        base_url=getattr(src, "BASE", None) or getattr(getattr(src, "config", None), "search_url", None),
                        reliability=src.reliability,
                        requires_key=src.requires_key,
                        official=src.official,
                        enabled=ok,
                        notes=getattr(getattr(src, "config", None), "notes", None),
                    )
                )
            else:
                row.name = src.name
                row.kind = src.kind.value
                row.reliability = src.reliability
                row.requires_key = src.requires_key
                row.official = src.official
                row.enabled = ok
            count += 1
    return count
