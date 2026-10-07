"""آزمون‌های زنده (شبکه واقعی) — به‌صورت پیش‌فرض اجرا نمی‌شوند.

اجرا: ``NUMBERBANK_RUN_LIVE=1 pytest -m live``
این آزمون‌ها به اینترنت آزاد نیاز دارند و در محیط‌های محدود (sandbox) رد می‌شوند.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.live

RUN_LIVE = os.environ.get("NUMBERBANK_RUN_LIVE") == "1"

skip_unless_live = pytest.mark.skipif(
    not RUN_LIVE, reason="آزمون زنده؛ برای اجرا NUMBERBANK_RUN_LIVE=1 را تنظیم کنید"
)


@skip_unless_live
def test_search_engine_source_returns_results() -> None:
    import asyncio

    from numberbank.config import get_settings
    from numberbank.crawl.http import Fetcher
    from numberbank.sources.base import SearchQuerySpec
    from numberbank.sources.search_engines.html_engines import DuckDuckGoHTMLSource

    async def run():
        settings = get_settings()
        source = DuckDuckGoHTMLSource(settings)
        async with Fetcher(settings) as fetcher:
            page = await source.search(
                SearchQuerySpec(text="عمده فروشی مواد شوینده یزد", results_per_page=10), fetcher
            )
            return page

    page = asyncio.run(run())
    assert page.items, "هیچ نتیجه‌ای از موتور جستجو دریافت نشد"


@skip_unless_live
def test_registry_reports_available_sources() -> None:
    from numberbank.sources.registry import SourceRegistry

    registry = SourceRegistry()
    available = registry.available()
    assert available, "هیچ منبع فعالی در دسترس نیست"
    keys = {s.key for s in available}
    assert "fixture_search_a" not in keys, "منبع آزمایشی نباید در حالت زنده فعال باشد"
