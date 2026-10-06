"""احترام به robots.txt با کش دامنه‌ای."""

from __future__ import annotations

import time
import urllib.robotparser
from urllib.parse import urlparse

from ..logging_setup import get_logger

log = get_logger("robots")

TTL_SECONDS = 6 * 3600


class RobotsPolicy:
    """سیاست robots.txt هر دامنه؛ در صورت خطا **محافظه‌کارانه** عمل می‌کند."""

    def __init__(self, user_agent: str, *, enabled: bool = True) -> None:
        self.user_agent = user_agent
        self.enabled = enabled
        self._cache: dict[str, tuple[float, urllib.robotparser.RobotFileParser | None]] = {}
        self._crawl_delay: dict[str, float] = {}

    def _get(self, domain: str):
        entry = self._cache.get(domain)
        if entry and (time.monotonic() - entry[0]) < TTL_SECONDS:
            return entry[1]
        return None

    def store(self, domain: str, parser: urllib.robotparser.RobotFileParser | None) -> None:
        self._cache[domain] = (time.monotonic(), parser)
        if parser is not None:
            try:
                delay = parser.crawl_delay(self.user_agent) or parser.crawl_delay("*")
                if delay:
                    self._crawl_delay[domain] = float(delay)
            except Exception:  # pragma: no cover
                pass

    def load_from_text(self, domain: str, text: str) -> None:
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(text.splitlines())
        self.store(domain, parser)

    def mark_unavailable(self, domain: str) -> None:
        """robots.txt در دسترس نبود ⇒ اجازه با احتیاط (رفتار متعارف خزنده‌ها برای ۴۰۴)."""
        self.store(domain, None)

    def crawl_delay(self, domain: str) -> float | None:
        return self._crawl_delay.get(domain)

    def allowed(self, url: str) -> bool:
        if not self.enabled:
            return True
        try:
            parsed = urlparse(url)
            domain = parsed.netloc
        except ValueError:
            return False
        if not domain:
            return False
        parser = self._get(domain)
        if parser is None and domain not in self._cache:
            return True  # هنوز بارگذاری نشده ⇒ در لایه HTTP بارگذاری می‌شود
        if parser is None:
            return True
        try:
            return parser.can_fetch(self.user_agent, url)
        except Exception:  # pragma: no cover
            return True

    def robots_url(self, url: str) -> str | None:
        try:
            p = urlparse(url)
        except ValueError:
            return None
        if not p.scheme or not p.netloc:
            return None
        return f"{p.scheme}://{p.netloc}/robots.txt"

    def is_loaded(self, domain: str) -> bool:
        return domain in self._cache
