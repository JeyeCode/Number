"""قرارداد مشترک منابع (Adapter Pattern) — افزودن منبع جدید = افزودن یک کلاس."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..crawl.http import Fetcher
from ..domain.enums import SourceKind


@dataclass
class SearchQuerySpec:
    """پرس‌وجویی که به یک منبع فرستاده می‌شود."""

    text: str
    page: int = 1
    results_per_page: int = 20
    province: str | None = None
    county: str | None = None
    city: str | None = None
    category: str | None = None
    business_type: str | None = None
    language: str = "fa"

    @property
    def context(self) -> dict[str, Any]:
        return {
            "province": self.province,
            "county": self.county,
            "city": self.city,
            "category": self.category,
            "business_type": self.business_type,
        }


@dataclass
class SearchResultItem:
    url: str
    title: str = ""
    snippet: str = ""
    rank: int = 0
    source_key: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchPage:
    items: list[SearchResultItem] = field(default_factory=list)
    page: int = 1
    has_more: bool = False
    error: str | None = None
    raw_count: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None


class SourceAdapter(ABC):
    """پایه همه منابع: موتور جستجو، دایرکتوری، داده باز، شبکه اجتماعی."""

    key: str = "base"
    name: str = "منبع پایه"
    kind: SourceKind = SourceKind.SEARCH_ENGINE
    reliability: float = 0.5
    requires_key: bool = False
    official: bool = False
    supports_search: bool = True
    supports_page_fetch: bool = True
    # منابعی که با رابط کاربری جستجو کار می‌کنند (نه خزش انبوه) از بررسی robots معاف‌اند
    check_robots_for_search: bool = False
    check_robots_for_pages: bool = True

    def __init__(self, settings=None) -> None:
        from ..config import get_settings

        self.settings = settings or get_settings()
        self.last_error: str | None = None
        self.success_count = 0
        self.error_count = 0

    # ------------------------------------------------------------------ #
    def available(self) -> tuple[bool, str]:
        """آیا منبع قابل استفاده است؟ (کلید/پیکربندی موجود؟)"""
        return True, ""

    @abstractmethod
    async def search(self, query: SearchQuerySpec, fetcher: Fetcher) -> SearchPage:
        """جستجو و بازگرداندن نتایج."""

    async def fetch_page(self, url: str, fetcher: Fetcher) -> str | None:
        result = await fetcher.fetch(url, purpose="page", check_robots=self.check_robots_for_pages)
        if result.ok and result.text:
            return result.text
        self.last_error = result.error
        return None

    def describe(self) -> dict[str, Any]:
        ok, reason = self.available()
        return {
            "key": self.key,
            "name": self.name,
            "kind": self.kind.value,
            "kind_label": self.kind.label_fa,
            "reliability": self.reliability,
            "requires_key": self.requires_key,
            "official": self.official,
            "available": ok,
            "reason": reason,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "last_error": self.last_error,
            "check_robots_for_search": self.check_robots_for_search,
        }
