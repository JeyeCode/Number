"""منبع داده باز OpenStreetMap از طریق Overpass API.

داده OSM عمومی، قابل استفاده و دارای ویژگی‌های تماس کسب‌وکارها (تگ ``phone``) است و
پوشش خوبی در شهرهای کوچک دارد. داده تحت مجوز ODbL است و ارجاع به OpenStreetMap الزامی است.
"""

from __future__ import annotations

import json
from urllib.parse import quote

from ...domain.enums import SourceKind
from ...errors import SourceUnavailable
from ...logging_setup import get_logger
from ...text.normalize import collapse_ws
from ..base import SearchPage, SearchQuerySpec, SearchResultItem, SourceAdapter

log = get_logger("sources.overpass")

# نگاشت دسته‌های هدف به تگ‌های OSM
CATEGORY_SHOP_TAGS = {
    "DETERGENT": ["chemist", "department_store", "variety_store", "wholesale", "hardware", "houseware"],
    "HYGIENE": ["chemist", "medical_supply", "beauty", "department_store", "variety_store"],
    "COSMETIC": ["cosmetics", "perfumery", "beauty", "chemist", "hairdresser_supply"],
    "CELLULOSE": ["houseware", "department_store", "variety_store", "stationery", "wholesale"],
}
DEFAULT_TAGS = ["chemist", "cosmetics", "perfumery", "wholesale", "variety_store",
                "department_store", "houseware", "supermarket"]


class OverpassDirectorySource(SourceAdapter):
    """کسب‌وکارهای دارای شماره تماس در OSM برای یک شهر."""

    key = "osm_overpass"
    name = "OpenStreetMap (Overpass API)"
    kind = SourceKind.OPEN_DATA
    reliability = 0.82
    official = True
    check_robots_for_search = False
    check_robots_for_pages = True

    def available(self) -> tuple[bool, str]:
        if not self.settings.enable_overpass:
            return False, "NUMBERBANK_ENABLE_OVERPASS=false"
        if not self.settings.overpass_list:
            return False, "هیچ نقطه پایانی Overpass تنظیم نشده است"
        return True, ""

    def _build_query(self, city: str, category: str | None, limit: int) -> str:
        tags = CATEGORY_SHOP_TAGS.get(category or "", DEFAULT_TAGS)
        regex = "|".join(tags)
        city_escaped = city.replace('"', '\\"')
        return (
            "[out:json][timeout:90];\n"
            f'area["name"="{city_escaped}"]["boundary"="administrative"]->.a;\n'
            "(\n"
            f'  nwr(area.a)["shop"~"^({regex})$"]["phone"];\n'
            f'  nwr(area.a)["shop"~"^({regex})$"]["contact:phone"];\n'
            f'  nwr(area.a)["office"="company"]["phone"];\n'
            f'  nwr(area.a)["wholesale"]["phone"];\n'
            ");\n"
            f"out center tags {limit};"
        )

    async def search(self, query: SearchQuerySpec, fetcher) -> SearchPage:  # noqa: ANN001
        ok, reason = self.available()
        if not ok:
            raise SourceUnavailable(reason)
        city = query.city or query.province
        if not city:
            raise SourceUnavailable("برای Overpass حداقل نام شهر لازم است")
        limit = max(50, min(600, query.results_per_page * 6))
        body = self._build_query(city, query.category, limit)
        last_error: str | None = None
        for endpoint in self.settings.overpass_list:
            url = f"{endpoint}?data={quote(body)}"
            result = await fetcher.fetch(url, purpose="search", check_robots=False,
                                         headers={"Accept": "application/json"})
            if not result.ok or not result.text:
                last_error = result.error
                continue
            try:
                data = json.loads(result.text)
            except ValueError:
                last_error = "پاسخ نامعتبر JSON از Overpass"
                continue
            items = self._elements_to_items(data.get("elements", []), query)
            self.success_count += 1
            return SearchPage(items=items, page=1, has_more=False, raw_count=len(data.get("elements", [])),
                              meta={"endpoint": endpoint})
        self.error_count += 1
        self.last_error = last_error
        raise SourceUnavailable(f"همه نقاط پایانی Overpass ناموفق بودند: {last_error}")

    def _elements_to_items(self, elements: list[dict], query: SearchQuerySpec) -> list[SearchResultItem]:
        items: list[SearchResultItem] = []
        for i, el in enumerate(elements):
            tags = el.get("tags") or {}
            name = tags.get("name") or tags.get("name:fa") or tags.get("operator")
            phone = tags.get("phone") or tags.get("contact:phone") or tags.get("contact:mobile")
            if not name or not phone:
                continue
            address = collapse_ws(
                " ".join(
                    filter(
                        None,
                        [
                            tags.get("addr:province"),
                            tags.get("addr:city"),
                            tags.get("addr:district"),
                            tags.get("addr:street"),
                            tags.get("addr:housenumber"),
                        ],
                    )
                )
            )
            city = tags.get("addr:city") or query.city
            snippet_parts = [p for p in [f"تلفن: {phone}", address, tags.get("shop")] if p]
            items.append(
                SearchResultItem(
                    url=self._element_url(el),
                    title=name,
                    snippet=" | ".join(snippet_parts),
                    rank=i + 1,
                    source_key=self.key,
                    extra={
                        "osm_id": el.get("id"),
                        "osm_type": el.get("type"),
                        "tags": {k: v for k, v in tags.items() if k in
                                 ("name", "phone", "contact:phone", "shop", "addr:street", "addr:city",
                                  "addr:province", "website", "contact:website", "opening_hours")},
                        "structured": True,
                        "name": name,
                        "phone": phone,
                        "address": address or None,
                        "city": city,
                        "province": tags.get("addr:province") or query.province,
                        "website": tags.get("website") or tags.get("contact:website"),
                        "shop": tags.get("shop"),
                    },
                )
            )
        return items

    @staticmethod
    def _element_url(el: dict) -> str:
        osm_type = el.get("type", "node")
        osm_id = el.get("id", "")
        return f"https://www.openstreetmap.org/{osm_type}/{osm_id}"

    async def fetch_page(self, url: str, fetcher) -> str | None:  # noqa: ANN001
        """برای Overpass، خود عنصر داده باز به‌عنوان شاهد کافی است (بدون خزش صفحه HTML)."""
        return None
