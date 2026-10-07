"""منابع موتور جستجوی HTML (رایگان، بدون کلید) — با احترام به نرخ و بدون دور زدن محدودیت‌ها."""

from __future__ import annotations

from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from ...domain.enums import SourceKind
from ...errors import SourceUnavailable
from ...logging_setup import get_logger
from ...text.htmlparse import HTMLParser
from ...text.normalize import collapse_ws
from ..base import SearchPage, SearchQuerySpec, SearchResultItem, SourceAdapter

log = get_logger("sources.search")


class DuckDuckGoHTMLSource(SourceAdapter):
    key = "duckduckgo_html"
    name = "DuckDuckGo (HTML)"
    kind = SourceKind.SEARCH_ENGINE
    reliability = 0.62
    official = False
    check_robots_for_search = False

    BASE = "https://html.duckduckgo.com/html/"
    MAX_PAGE = 5

    async def search(self, query: SearchQuerySpec, fetcher: Fetcher) -> SearchPage:  # noqa: F821
        if query.page > self.MAX_PAGE:
            return SearchPage(page=query.page, has_more=False)
        url = f"{self.BASE}?q={quote_plus(query.text)}"
        data = {"q": query.text, "kl": "ir-fa"} if query.page == 1 else {"q": query.text, "s": str((query.page - 1) * 20)}
        result = await fetcher.fetch(
            url, method="POST", data=data, purpose="search", check_robots=self.check_robots_for_search
        )
        if not result.ok or not result.text:
            self.error_count += 1
            self.last_error = result.error
            raise SourceUnavailable(f"DuckDuckGo پاسخ نداد: {result.error}")
        self.success_count += 1
        return self._parse(result.text, query)

    def _parse(self, html: str, query: SearchQuerySpec) -> SearchPage:
        tree = HTMLParser(html)
        items: list[SearchResultItem] = []
        nodes = tree.css(".result, .web-result, div[data-testid=result]")
        for idx, node in enumerate(nodes[: query.results_per_page]):
            link = node.css_first("a.result__a") or node.css_first("h2 a") or node.css_first("a[href]")
            if link is None:
                continue
            href = link.attributes.get("href") or ""
            url = self._clean_url(href)
            if not url:
                continue
            title = collapse_ws(link.text() or "")
            sn = node.css_first(".result__snippet") or node.css_first("a.result__snippet") or node.css_first("div")
            snippet = collapse_ws(sn.text() if sn is not None else "")
            items.append(
                SearchResultItem(
                    url=url,
                    title=title,
                    snippet=snippet,
                    rank=idx + 1,
                    source_key=self.key,
                    extra={"engine_page": query.page},
                )
            )
        return SearchPage(items=items, page=query.page, has_more=len(items) >= query.results_per_page,
                          raw_count=len(nodes))

    @staticmethod
    def _clean_url(href: str) -> str | None:
        if not href:
            return None
        if href.startswith("//duckduckgo.com/l/") or "duckduckgo.com/l/" in href:
            qs = parse_qs(urlparse("https:" + href if href.startswith("//") else href).query)
            if "uddg" in qs:
                return unquote(qs["uddg"][0])
        if href.startswith("http"):
            return href
        return None


class BingHTMLSource(SourceAdapter):
    key = "bing_html"
    name = "Bing (HTML)"
    kind = SourceKind.SEARCH_ENGINE
    reliability = 0.62
    check_robots_for_search = False
    MAX_PAGE = 5

    BASE = "https://www.bing.com/search"

    async def search(self, query: SearchQuerySpec, fetcher) -> SearchPage:  # noqa: ANN001
        if query.page > self.MAX_PAGE:
            return SearchPage(page=query.page, has_more=False)
        first = (query.page - 1) * 10 + 1
        url = f"{self.BASE}?q={quote_plus(query.text)}&first={first}&setlang=fa&cc=IR"
        result = await fetcher.fetch(url, purpose="search", check_robots=self.check_robots_for_search)
        if not result.ok or not result.text:
            self.error_count += 1
            self.last_error = result.error
            raise SourceUnavailable(f"Bing پاسخ نداد: {result.error}")
        self.success_count += 1
        return self._parse(result.text, query)

    def _parse(self, html: str, query: SearchQuerySpec) -> SearchPage:
        tree = HTMLParser(html)
        items: list[SearchResultItem] = []
        nodes = tree.css("li.b_algo")
        for idx, node in enumerate(nodes[: query.results_per_page]):
            link = node.css_first("h2 a") or node.css_first("a[href^=http]")
            if link is None:
                continue
            url = link.attributes.get("href")
            if not url or not url.startswith("http"):
                continue
            title = collapse_ws(link.text() or "")
            sn = node.css_first("p") or node.css_first(".b_caption")
            snippet = collapse_ws(sn.text() if sn is not None else "")
            items.append(
                SearchResultItem(url=url, title=title, snippet=snippet, rank=idx + 1, source_key=self.key)
            )
        return SearchPage(items=items, page=query.page, has_more=bool(items), raw_count=len(nodes))


class MojeekHTMLSource(SourceAdapter):
    key = "mojeek_html"
    name = "Mojeek (HTML)"
    kind = SourceKind.SEARCH_ENGINE
    reliability = 0.6
    check_robots_for_search = False
    MAX_PAGE = 4

    BASE = "https://www.mojeek.com/search"

    async def search(self, query: SearchQuerySpec, fetcher) -> SearchPage:  # noqa: ANN001
        if query.page > self.MAX_PAGE:
            return SearchPage(page=query.page, has_more=False)
        url = f"{self.BASE}?q={quote_plus(query.text)}"
        if query.page > 1:
            url += f"&s={(query.page - 1) * 10}"
        result = await fetcher.fetch(url, purpose="search", check_robots=self.check_robots_for_search)
        if not result.ok or not result.text:
            self.error_count += 1
            self.last_error = result.error
            raise SourceUnavailable(f"Mojeek پاسخ نداد: {result.error}")
        self.success_count += 1
        return self._parse(result.text, query)

    def _parse(self, html: str, query: SearchQuerySpec) -> SearchPage:
        tree = HTMLParser(html)
        items: list[SearchResultItem] = []
        nodes = tree.css("ul.results-standard li, li.result")
        for idx, node in enumerate(nodes[: query.results_per_page]):
            link = node.css_first("a.ob, a.title, h2 a")
            if link is None:
                continue
            url = link.attributes.get("href")
            if not url or not url.startswith("http"):
                continue
            sn = node.css_first("p.s, .s")
            items.append(
                SearchResultItem(
                    url=url,
                    title=collapse_ws(link.text() or ""),
                    snippet=collapse_ws(sn.text() if sn is not None else ""),
                    rank=idx + 1,
                    source_key=self.key,
                )
            )
        return SearchPage(items=items, page=query.page, has_more=bool(items), raw_count=len(nodes))


class SearxSource(SourceAdapter):
    """متاک crawler خودمیزبان/عمومی — با پشتیبانی JSON و بازگشت به HTML."""

    key = "searx"
    name = "SearXNG (نمونه‌های عمومی)"
    kind = SourceKind.SEARCH_ENGINE
    reliability = 0.65
    check_robots_for_search = False

    async def search(self, query: SearchQuerySpec, fetcher) -> SearchPage:  # noqa: ANN001
        instances = self.settings.searx_list
        if not instances:
            raise SourceUnavailable("هیچ نمونه SearX پیکربندی نشده است")
        last_error = None
        for base in instances:
            try:
                page = await self._search_instance(base, query, fetcher)
                if page.items:
                    self.success_count += 1
                    return page
                last_error = "بدون نتیجه"
            except Exception as exc:
                last_error = str(exc)
                continue
        self.error_count += 1
        self.last_error = last_error
        raise SourceUnavailable(f"SearX در دسترس نبود: {last_error}")

    async def _search_instance(self, base: str, query: SearchQuerySpec, fetcher) -> SearchPage:  # noqa: ANN001
        url = f"{base}/search?q={quote_plus(query.text)}&language=fa&format=json&pageno={query.page}"
        result = await fetcher.fetch(url, purpose="search", check_robots=self.check_robots_for_search)
        if result.ok and result.text and result.text.lstrip().startswith("{"):
            import json

            try:
                data = json.loads(result.text)
                items = [
                    SearchResultItem(
                        url=it.get("url", ""),
                        title=it.get("title", ""),
                        snippet=it.get("content", ""),
                        rank=i + 1,
                        source_key=self.key,
                    )
                    for i, it in enumerate(data.get("results", [])[: query.results_per_page])
                    if it.get("url")
                ]
                return SearchPage(items=items, page=query.page, has_more=len(items) >= 10)
            except (ValueError, KeyError):
                pass
        html_url = f"{base}/search?q={quote_plus(query.text)}&language=fa&pageno={query.page}"
        res2 = await fetcher.fetch(html_url, purpose="search", check_robots=self.check_robots_for_search)
        if not res2.ok or not res2.text:
            raise SourceUnavailable(f"پاسخ نامعتبر از {base}")
        return self._parse_html(res2.text, query)

    def _parse_html(self, html: str, query: SearchQuerySpec) -> SearchPage:
        tree = HTMLParser(html)
        items: list[SearchResultItem] = []
        for idx, node in enumerate(tree.css("article.result, div.result")[: query.results_per_page]):
            link = node.css_first("h3 a, a.url")
            if link is None:
                continue
            url = link.attributes.get("href")
            if not url or not url.startswith("http"):
                continue
            sn = node.css_first("p.content")
            items.append(
                SearchResultItem(
                    url=url,
                    title=collapse_ws(link.text() or ""),
                    snippet=collapse_ws(sn.text() if sn is not None else ""),
                    rank=idx + 1,
                    source_key=self.key,
                )
            )
        return SearchPage(items=items, page=query.page, has_more=bool(items))


class LocalDomainsSearchSource(SourceAdapter):
    """جستجوی محدود به دامنه‌های مشخص با `site:` — برای تمرکز روی دایرکتوری‌های تجاری."""

    key = "site_search"
    name = "جستجوی دامنه‌محور (با موتور پایه)"
    kind = SourceKind.SEARCH_ENGINE
    reliability = 0.66
    check_robots_for_search = False

    def __init__(self, settings=None, engine=None, domains: list[str] | None = None) -> None:
        super().__init__(settings)
        self.engine = engine or DuckDuckGoHTMLSource(settings)
        self.domains = domains or []

    async def search(self, query: SearchQuerySpec, fetcher) -> SearchPage:  # noqa: ANN001
        if not self.domains:
            return SearchPage(page=query.page)
        combined: list[SearchResultItem] = []
        errors = 0
        for domain in self.domains[:3]:
            spec = SearchQuerySpec(
                text=f"site:{domain} {query.text}",
                page=query.page,
                results_per_page=max(5, query.results_per_page // 2),
                province=query.province,
                county=query.county,
                city=query.city,
                category=query.category,
                business_type=query.business_type,
            )
            try:
                page = await self.engine.search(spec, fetcher)
                combined.extend(page.items)
            except Exception:  # یک دامنه نباید کل منبع را از کار بیندازد
                errors += 1
        if not combined and errors:
            raise SourceUnavailable("جستجوی دامنه‌محور نتیجه‌ای نداشت")
        return SearchPage(items=combined[: query.results_per_page], page=query.page, has_more=False)
