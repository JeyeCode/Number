"""منابع رسمی دارای API (پایدارتر و مطابق شرایط استفاده سرویس)."""

from __future__ import annotations

import json
from urllib.parse import quote_plus

from ...domain.enums import SourceKind
from ...errors import SourceUnavailable
from ...logging_setup import get_logger
from ..base import SearchPage, SearchQuerySpec, SearchResultItem, SourceAdapter

log = get_logger("sources.api")


class BingAPISource(SourceAdapter):
    key = "bing_api"
    name = "Bing Web Search API"
    kind = SourceKind.SEARCH_ENGINE
    reliability = 0.85
    requires_key = True
    official = True
    check_robots_for_search = False

    ENDPOINT = "https://api.bing.microsoft.com/v7.0/search"

    def available(self) -> tuple[bool, str]:
        if not self.settings.bing_api_key:
            return False, "کلید NUMBERBANK_BING_API_KEY تنظیم نشده است"
        return True, ""

    async def search(self, query: SearchQuerySpec, fetcher) -> SearchPage:  # noqa: ANN001
        ok, reason = self.available()
        if not ok:
            raise SourceUnavailable(reason)
        offset = (query.page - 1) * query.results_per_page
        url = (
            f"{self.ENDPOINT}?q={quote_plus(query.text)}"
            f"&count={min(query.results_per_page, 50)}&offset={offset}&mkt=fa-IR&setLang=fa"
        )
        result = await fetcher.fetch(
            url,
            purpose="search",
            check_robots=False,
            headers={"Ocp-Apim-Subscription-Key": self.settings.bing_api_key or ""},
        )
        if not result.ok or not result.text:
            self.error_count += 1
            self.last_error = result.error
            raise SourceUnavailable(f"Bing API خطا داد: {result.error}")
        try:
            data = json.loads(result.text)
        except ValueError as exc:
            raise SourceUnavailable(f"پاسخ نامعتبر Bing API: {exc}") from exc
        items = []
        for i, it in enumerate(data.get("webPages", {}).get("value", [])):
            items.append(
                SearchResultItem(
                    url=it.get("url", ""),
                    title=it.get("name", ""),
                    snippet=it.get("snippet", ""),
                    rank=i + 1,
                    source_key=self.key,
                )
            )
        self.success_count += 1
        total = data.get("webPages", {}).get("totalEstimatedMatches", 0)
        return SearchPage(items=items, page=query.page, has_more=offset + len(items) < total, meta={"total": total})


class GoogleCSESource(SourceAdapter):
    key = "google_cse"
    name = "Google Programmable Search (CSE)"
    kind = SourceKind.SEARCH_ENGINE
    reliability = 0.88
    requires_key = True
    official = True
    check_robots_for_search = False

    ENDPOINT = "https://www.googleapis.com/customsearch/v1"

    def available(self) -> tuple[bool, str]:
        if not (self.settings.google_cse_key and self.settings.google_cse_cx):
            return False, "کلیدها/شناسه CSE تنظیم نشده است"
        return True, ""

    async def search(self, query: SearchQuerySpec, fetcher) -> SearchPage:  # noqa: ANN001
        ok, reason = self.available()
        if not ok:
            raise SourceUnavailable(reason)
        start = (query.page - 1) * 10 + 1
        url = (
            f"{self.ENDPOINT}?key={self.settings.google_cse_key}&cx={self.settings.google_cse_cx}"
            f"&q={quote_plus(query.text)}&num={min(query.results_per_page, 10)}&start={start}&hl=fa&gl=ir"
        )
        result = await fetcher.fetch(url, purpose="search", check_robots=False)
        if not result.ok or not result.text:
            self.error_count += 1
            self.last_error = result.error
            raise SourceUnavailable(f"Google CSE خطا داد: {result.error}")
        try:
            data = json.loads(result.text)
        except ValueError as exc:
            raise SourceUnavailable(f"پاسخ نامعتبر CSE: {exc}") from exc
        items = [
            SearchResultItem(
                url=it.get("link", ""),
                title=it.get("title", ""),
                snippet=it.get("snippet", ""),
                rank=i + 1,
                source_key=self.key,
            )
            for i, it in enumerate(data.get("items", []))
        ]
        self.success_count += 1
        return SearchPage(items=items, page=query.page, has_more=bool(data.get("queries", {}).get("nextPage")))


class SerpAPISource(SourceAdapter):
    key = "serpapi"
    name = "SerpAPI (Google/Bing)"
    kind = SourceKind.SEARCH_ENGINE
    reliability = 0.85
    requires_key = True
    official = True
    check_robots_for_search = False

    ENDPOINT = "https://serpapi.com/search.json"

    def available(self) -> tuple[bool, str]:
        if not self.settings.serpapi_key:
            return False, "کلید NUMBERBANK_SERPAPI_KEY تنظیم نشده است"
        return True, ""

    async def search(self, query: SearchQuerySpec, fetcher) -> SearchPage:  # noqa: ANN001
        ok, reason = self.available()
        if not ok:
            raise SourceUnavailable(reason)
        start = (query.page - 1) * 10
        url = (
            f"{self.ENDPOINT}?engine=google&q={quote_plus(query.text)}&google_domain=google.com"
            f"&hl=fa&gl=ir&start={start}&api_key={self.settings.serpapi_key}"
        )
        result = await fetcher.fetch(url, purpose="search", check_robots=False)
        if not result.ok or not result.text:
            self.error_count += 1
            self.last_error = result.error
            raise SourceUnavailable(f"SerpAPI خطا داد: {result.error}")
        try:
            data = json.loads(result.text)
        except ValueError as exc:
            raise SourceUnavailable(f"پاسخ نامعتبر SerpAPI: {exc}") from exc
        items = [
            SearchResultItem(
                url=it.get("link", ""),
                title=it.get("title", ""),
                snippet=it.get("snippet", ""),
                rank=i + 1,
                source_key=self.key,
            )
            for i, it in enumerate(data.get("organic_results", []))
        ]
        self.success_count += 1
        return SearchPage(items=items, page=query.page,
                          has_more=bool(data.get("serpapi_pagination", {}).get("next")))
