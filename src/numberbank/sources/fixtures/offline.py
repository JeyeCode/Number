"""منابع آزمایشی آفلاین (Fixture) — برای تست قطعی، سریع و بدون شبکه.

این منابع داده **مصنوعی** تولید می‌کنند و صریحاً با ``is_synthetic`` علامت می‌خورند؛
هرگز با داده واقعی اشتباه گرفته نمی‌شوند و در خروجی پیش‌فرض حذف می‌شوند.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...config import get_settings
from ...domain.enums import SourceKind
from ...errors import SourceUnavailable
from ...logging_setup import get_logger
from ...text.normalize import normalize_fa, search_text
from ..base import SearchPage, SearchQuerySpec, SearchResultItem, SourceAdapter

log = get_logger("sources.fixtures")


def fixtures_dir() -> Path:
    settings = get_settings()
    return Path(settings.data_dir) / "fixtures"


@dataclass
class FixtureIndex:
    pages: dict[str, dict[str, Any]]
    sources: dict[str, list[dict[str, Any]]]


def load_index() -> FixtureIndex | None:
    path = fixtures_dir() / "index.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("index فیچر خوانده نشد: %s", exc)
        return None
    return FixtureIndex(pages=data.get("pages", {}), sources=data.get("sources", {}))


class FixtureSearchSource(SourceAdapter):
    """موتور جستجوی شبیه‌سازی‌شده روی فیچرهای محلی (فقط حالت آفلاین/دمو)."""

    kind = SourceKind.FIXTURE
    check_robots_for_search = False
    check_robots_for_pages = False
    reliability = 0.7

    def __init__(self, source_key: str = "fixture_search_a", settings=None) -> None:
        super().__init__(settings)
        self.key = source_key
        self.name = f"منبع آزمایشی {source_key}"
        self._index: FixtureIndex | None = None

    def available(self) -> tuple[bool, str]:
        idx = self._load()
        if idx is None:
            return False, "فایل فیچر یافت نشد (ابتدا `numberbank demo` یا اسکریپت ساخت فیچر را اجرا کنید)"
        if self.key not in idx.sources:
            return False, f"کلید {self.key} در index فیچر وجود ندارد"
        return True, ""

    def _load(self) -> FixtureIndex | None:
        if self._index is None:
            self._index = load_index()
        return self._index

    async def search(self, query: SearchQuerySpec, fetcher) -> SearchPage:  # noqa: ANN001
        idx = self._load()
        if idx is None or self.key not in idx.sources:
            raise SourceUnavailable("منبع آزمایشی در دسترس نیست")
        entries = idx.sources[self.key]
        q_norm = normalize_fa(query.text, zwnj="space").lower()
        tokens = [t for t in q_norm.split() if len(t) >= 2]

        scored: list[tuple[int, dict[str, Any]]] = []
        for entry in entries:
            hay = normalize_fa(
                " ".join(
                    str(entry.get(k, ""))
                    for k in ("title", "snippet", "city", "province", "category", "keywords")
                ),
                zwnj="space",
            ).lower()
            score = sum(2 if t in hay else 0 for t in tokens)
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda x: -x[0])

        per_page = max(5, query.results_per_page)
        start = (query.page - 1) * per_page
        window = scored[start : start + per_page]
        items = [
            SearchResultItem(
                url=e["url"],
                title=e.get("title", ""),
                snippet=e.get("snippet", ""),
                rank=i + 1,
                source_key=self.key,
                extra={"fixture": True, "structured": bool(e.get("structured")),
                       "target_file": e.get("file")},
            )
            for i, (_, e) in enumerate(window)
        ]
        return SearchPage(items=items, page=query.page, has_more=len(scored) > start + per_page,
                          raw_count=len(scored))

    async def fetch_page(self, url: str, fetcher) -> str | None:  # noqa: ANN001
        return load_fixture_page(url)


def load_fixture_page(url: str) -> str | None:
    """خواندن HTML فیچر از مسیر محلی (بدون هیچ درخواست شبکه‌ای)."""
    idx = load_index()
    if idx is None:
        return None
    meta = idx.pages.get(url)
    if meta is None:
        # تطبیق نرم با انتهای مسیر
        for key, value in idx.pages.items():
            if key.endswith(url.split("/")[-1]):
                meta = value
                break
    if meta is None:
        return None
    path = fixtures_dir() / meta.get("file", "")
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def fixtures_available() -> bool:
    return (fixtures_dir() / "index.json").exists()


def fixture_source_keys() -> list[str]:
    idx = load_index()
    return sorted(idx.sources.keys()) if idx else []


def fixture_queries_preview() -> list[str]:
    idx = load_index()
    if not idx:
        return []
    keywords: set[str] = set()
    for entries in idx.sources.values():
        for e in entries:
            keywords.add(search_text(str(e.get("keywords", ""))[:60]))
    return sorted(keywords)[:20]
