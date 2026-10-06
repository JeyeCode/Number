"""لایه HTTP: Timeout، Retry، Rate-Limit، Robots، Circuit-Breaker و کش."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx
from sqlalchemy import delete, select

from ..config import Settings, get_settings
from ..errors import RobotsDisallowed, SourceUnavailable
from ..logging_setup import get_logger
from ..text.normalize import domain_of, url_hash_key
from .ratelimit import RateLimiter
from .robots import RobotsPolicy

log = get_logger("http")


@dataclass
class FetchResult:
    url: str
    final_url: str
    status_code: int | None = None
    text: str | None = None
    content_type: str | None = None
    ok: bool = False
    error: str | None = None
    robots_allowed: bool | None = None
    from_cache: bool = False
    elapsed_ms: int = 0
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def domain(self) -> str | None:
        return domain_of(self.final_url or self.url)


BREAKER_THRESHOLD = 5
BREAKER_COOLDOWN = 120.0


class Fetcher:
    """کلاینت HTTP مشترک با کنترل‌های ادب شبکه."""

    def __init__(self, settings: Settings | None = None, *, database=None) -> None:
        self.settings = settings or get_settings()
        self.database = database
        self.rate_limiter = RateLimiter(self.settings.per_domain_rps)
        self.robots = RobotsPolicy(self.settings.user_agent, enabled=self.settings.respect_robots)
        self._client: httpx.AsyncClient | None = None
        self._sem = asyncio.Semaphore(max(1, self.settings.global_concurrency))
        self._failures: dict[str, int] = {}
        self._blocked_until: dict[str, float] = {}
        self._robots_lock = asyncio.Lock()
        self.stats = {"requests": 0, "cache_hits": 0, "errors": 0, "blocked_by_robots": 0}

    # ------------------------------------------------------------------ #
    async def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            limits = httpx.Limits(
                max_connections=max(4, self.settings.global_concurrency * 2),
                max_keepalive_connections=max(2, self.settings.global_concurrency),
            )
            kwargs: dict = {
                "timeout": httpx.Timeout(self.settings.request_timeout),
                "limits": limits,
                "follow_redirects": True,
                "headers": {
                    "User-Agent": self.settings.user_agent,
                    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.6",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
                "trust_env": False,
            }
            if self.settings.proxy_url:
                kwargs["proxy"] = self.settings.proxy_url
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    # ------------------------------------------------------------------ #
    def _breaker_check(self, domain: str) -> None:
        until = self._blocked_until.get(domain)
        if until and time.monotonic() < until:
            raise SourceUnavailable(
                f"دامنه {domain} به‌دلیل خطاهای پی‌درپی تا {int(until - time.monotonic())} ثانیه مسدود است"
            )
        if until and time.monotonic() >= until:
            self._blocked_until.pop(domain, None)
            self._failures[domain] = 0

    def _record_failure(self, domain: str) -> None:
        if not domain:
            return
        self._failures[domain] = self._failures.get(domain, 0) + 1
        if self._failures[domain] >= BREAKER_THRESHOLD:
            self._blocked_until[domain] = time.monotonic() + BREAKER_COOLDOWN
            log.warning("Circuit breaker برای %s فعال شد (%s خطای پی‌درپی)", domain, self._failures[domain])

    def _record_success(self, domain: str) -> None:
        if domain:
            self._failures[domain] = 0

    # ------------------------------------------------------------------ #
    async def ensure_robots(self, url: str) -> None:
        if not self.settings.respect_robots:
            return
        robots_url = self.robots.robots_url(url)
        if not robots_url:
            return
        domain = domain_of(robots_url) or ""
        if self.robots.is_loaded(domain):
            return
        async with self._robots_lock:
            if self.robots.is_loaded(domain):
                return
            try:
                client = await self.client()
                resp = await client.get(robots_url, timeout=8.0)
                if resp.status_code == 200:
                    self.robots.load_from_text(domain, resp.text)
                    delay = self.robots.crawl_delay(domain)
                    if delay:
                        self.rate_limiter.set_rate(domain, 1.0 / max(delay, 0.5))
                else:
                    self.robots.mark_unavailable(domain)
            except Exception as exc:  # robots در دسترس نیست ⇒ با احتیاط ادامه
                log.debug("robots.txt در دسترس نبود برای %s: %s", domain, exc)
                self.robots.mark_unavailable(domain)
            if not self.robots.allowed(url):
                self.stats["blocked_by_robots"] += 1
                raise RobotsDisallowed(url, self.settings.user_agent)

    # ------------------------------------------------------------------ #
    async def fetch(
        self,
        url: str,
        *,
        purpose: str = "page",
        check_robots: bool | None = None,
        use_cache: bool = True,
        headers: dict[str, str] | None = None,
        method: str = "GET",
        data: dict[str, str] | None = None,
    ) -> FetchResult:
        """دریافت یک URL با همه کنترل‌ها. هیچ استثنایی عملیات را متوقف نمی‌کند."""
        started = time.monotonic()
        domain = domain_of(url) or ""
        check = self.settings.respect_robots if check_robots is None else check_robots
        result = FetchResult(url=url, final_url=url)

        if check:
            try:
                await self.ensure_robots(url)
                result.robots_allowed = True
            except RobotsDisallowed as exc:
                result.error = str(exc)
                result.robots_allowed = False
                log.info("robots.txt مانع شد: %s", url)
                return result

        if use_cache and self.database is not None:
            cached = await self._cache_get(url)
            if cached is not None:
                self.stats["cache_hits"] += 1
                cached.elapsed_ms = int((time.monotonic() - started) * 1000)
                return cached

        try:
            self._breaker_check(domain)
        except SourceUnavailable as exc:
            result.error = str(exc)
            return result

        await self.rate_limiter.acquire(domain)
        last_error: str | None = None
        for attempt in range(1, self.settings.max_retries + 1):
            try:
                async with self._sem:
                    client = await self.client()
                    self.stats["requests"] += 1
                    if method.upper() == "POST":
                        resp = await client.post(url, data=data, headers=headers)
                    else:
                        resp = await client.get(url, headers=headers)
                content_type = resp.headers.get("content-type", "")
                result.status_code = resp.status_code
                result.content_type = content_type
                result.final_url = str(resp.url)
                result.headers = {k.lower(): v for k, v in resp.headers.items()}
                if resp.status_code >= 400:
                    last_error = f"HTTP {resp.status_code}"
                    if resp.status_code in (403, 429, 451):
                        self._record_failure(domain)
                        if resp.status_code == 429:
                            await asyncio.sleep(min(5.0 * attempt, 20.0))
                    if resp.status_code >= 500 and attempt < self.settings.max_retries:
                        await asyncio.sleep(min(1.5 * attempt, 8.0))
                        continue
                    break
                body = resp.content[: self.settings.max_page_bytes]
                result.text = resp.text if len(resp.content) <= self.settings.max_page_bytes else body.decode(
                    resp.encoding or "utf-8", errors="replace"
                )
                result.ok = True
                result.error = None
                self._record_success(domain)
                self.stats["errors"] -= 0
                break
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < self.settings.max_retries:
                    await asyncio.sleep(min(1.2 * attempt, 6.0))
                    continue
            except Exception as exc:  # pragma: no cover - محافظت نهایی
                last_error = f"{type(exc).__name__}: {exc}"
                break

        if not result.ok:
            result.error = last_error or "خطای نامشخص"
            self.stats["errors"] += 1
            self._record_failure(domain)
            log.debug("دریافت ناموفق %s: %s", url, result.error)
        else:
            if use_cache and self.database is not None:
                await self._cache_put(result)

        result.elapsed_ms = int((time.monotonic() - started) * 1000)
        return result

    # ------------------------------------------------------------------ #
    async def _cache_get(self, url: str) -> FetchResult | None:
        from ..domain.models import CrawlCache  # ورود تنبل برای جلوگیری از چرخه

        def _query():
            from datetime import datetime

            with self.database.session() as s:
                row = s.execute(
                    select(CrawlCache).where(CrawlCache.url_hash == url_hash_key(url))
                ).scalar_one_or_none()
                if row is None:
                    return None
                if row.expires_at and row.expires_at < datetime.utcnow():
                    s.execute(delete(CrawlCache).where(CrawlCache.id == row.id))
                    return None
                body = row.body or b""
                return FetchResult(
                    url=row.url,
                    final_url=row.url,
                    status_code=row.status_code,
                    text=body.decode(row.encoding or "utf-8", errors="replace") if body else None,
                    content_type=row.content_type,
                    ok=bool(row.status_code == 200),
                    error=row.error,
                    robots_allowed=row.robots_allowed,
                    from_cache=True,
                )

        try:
            return await asyncio.to_thread(_query)
        except Exception as exc:  # کش هرگز نباید جریان اصلی را بشکند
            log.debug("خطای خواندن کش برای %s: %s", url, exc)
            return None

    async def _cache_put(self, result: FetchResult) -> None:
        from datetime import datetime, timedelta

        from ..domain.models import CrawlCache

        def _write():
            with self.database.session() as s:
                row = s.execute(
                    select(CrawlCache).where(CrawlCache.url_hash == url_hash_key(result.url))
                ).scalar_one_or_none()
                expires = datetime.utcnow() + timedelta(hours=self.settings.http_cache_ttl_hours)
                body = (result.text or "").encode("utf-8")[: self.settings.max_page_bytes]
                if row is None:
                    s.add(
                        CrawlCache(
                            url_hash=url_hash_key(result.url),
                            url=result.url,
                            status_code=result.status_code,
                            content_type=result.content_type,
                            body=body,
                            encoding="utf-8",
                            robots_allowed=result.robots_allowed,
                            expires_at=expires,
                        )
                    )
                else:
                    row.status_code = result.status_code
                    row.content_type = result.content_type
                    row.body = body
                    row.expires_at = expires
                    row.error = None

        try:
            await asyncio.to_thread(_write)
        except Exception as exc:
            log.debug("خطای نوشتن کش برای %s: %s", result.url, exc)

    async def purge_cache(self) -> int:
        from ..domain.models import CrawlCache

        def _purge():
            with self.database.session() as s:
                rows = s.query(CrawlCache).delete()
                return rows

        return await asyncio.to_thread(_purge)

    # ------------------------------------------------------------------ #
    async def fetch_text(self, url: str, **kwargs) -> str | None:
        res = await self.fetch(url, **kwargs)
        return res.text if res.ok else None

    def stats_snapshot(self) -> dict[str, int]:
        return dict(self.stats)


def is_probably_html(result: FetchResult) -> bool:
    ctype = (result.content_type or "").lower()
    if "html" in ctype or "xml" in ctype:
        return True
    if not ctype and result.text:
        head = result.text[:200].lower()
        return "<html" in head or "<!doctype" in head
    return False


def domain_rps_hint(url: str) -> float:
    try:
        return 1.0 if urlparse(url).netloc else 1.0
    except ValueError:
        return 1.0
