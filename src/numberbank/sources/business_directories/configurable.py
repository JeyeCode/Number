"""منابع دایرکتوری تجاری مبتنی بر پیکربندی — افزودن منبع جدید بدون تغییر کد.

فایل ``data/reference/directories.json``:
[
  {
    "key": "my_directory",
    "name": "دایرکتوری نمونه",
    "kind": "BUSINESS_DIRECTORY",
    "reliability": 0.7,
    "search_url": "https://example.ir/search?q={query}&page={page}",
    "list_selector": "div.company-item",
    "enabled": true,
    "notes": "توضیح منبع و شرایط استفاده"
  }
]
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus

from ...config import get_settings
from ...domain.enums import SourceKind
from ...errors import SourceUnavailable
from ...extract.page import extract_from_page
from ...logging_setup import get_logger
from ..base import SearchPage, SearchQuerySpec, SearchResultItem, SourceAdapter

log = get_logger("sources.directories")


@dataclass
class DirectoryConfig:
    key: str
    name: str
    search_url: str
    list_selector: str | None = None
    reliability: float = 0.65
    kind: str = "BUSINESS_DIRECTORY"
    enabled: bool = True
    notes: str | None = None
    domains: list[str] | None = None


def load_directory_configs() -> list[DirectoryConfig]:
    path = Path(get_settings().data_dir) / "reference" / "directories.json"
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("فایل دایرکتوری‌ها خوانده نشد: %s", exc)
        return []
    out: list[DirectoryConfig] = []
    for item in raw if isinstance(raw, list) else raw.get("directories", []):
        if not item.get("key") or not item.get("search_url"):
            continue
        out.append(
            DirectoryConfig(
                key=item["key"],
                name=item.get("name", item["key"]),
                search_url=item["search_url"],
                list_selector=item.get("list_selector"),
                reliability=float(item.get("reliability", 0.65)),
                kind=item.get("kind", "BUSINESS_DIRECTORY"),
                enabled=bool(item.get("enabled", True)),
                notes=item.get("notes"),
                domains=item.get("domains"),
            )
        )
    return out


class ConfigurableDirectorySource(SourceAdapter):
    """اجرای جستجو روی یک دایرکتوری و استخراج ساختاریافته از صفحه نتایج."""

    def __init__(self, config: DirectoryConfig, settings=None) -> None:
        super().__init__(settings)
        self.config = config
        self.key = config.key
        self.name = config.name
        self.kind = SourceKind(config.kind) if config.kind in SourceKind._value2member_map_ else SourceKind.BUSINESS_DIRECTORY
        self.reliability = config.reliability
        self.check_robots_for_search = True
        self._emitted: list[SearchResultItem] = []

    def available(self) -> tuple[bool, str]:
        if not self.config.enabled:
            return False, "منبع در پیکربندی غیرفعال است"
        if "{query}" not in self.config.search_url:
            return False, "search_url باید شامل {query} باشد"
        return True, ""

    async def search(self, query: SearchQuerySpec, fetcher) -> SearchPage:  # noqa: ANN001
        ok, reason = self.available()
        if not ok:
            raise SourceUnavailable(reason)
        url = self.config.search_url.format(query=quote_plus(query.text), page=query.page)
        result = await fetcher.fetch(url, purpose="search", check_robots=self.config.enabled)
        if not result.ok or not result.text:
            self.error_count += 1
            self.last_error = result.error
            raise SourceUnavailable(f"{self.name} پاسخ نداد: {result.error}")

        candidates = extract_from_page(
            result.text,
            result.final_url,
            query_context=query.context,
            directory_selector=self.config.list_selector,
            max_candidates=max(5, query.results_per_page),
            source_kind=self.kind.value,
        )
        items: list[SearchResultItem] = []
        for cand in candidates:
            if not cand.name:
                continue
            items.append(
                SearchResultItem(
                    url=cand.source_url or result.final_url,
                    title=cand.name,
                    snippet=cand.snippet or "",
                    rank=len(items) + 1,
                    source_key=self.key,
                    extra={
                        "structured": True,
                        "name": cand.name,
                        "phones": cand.phone_values(),
                        "address": cand.address,
                        "city": cand.city,
                        "province": cand.province,
                        "website": cand.website,
                        "owner_name": cand.owner_name,
                        "candidate": cand,
                    },
                )
            )
        self.success_count += 1
        return SearchPage(items=items, page=query.page, has_more=bool(items), raw_count=len(candidates))
