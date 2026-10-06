"""فرهنگ جغرافیایی: تطبیق نام استان/شهرستان/شهر در متن و نگاشت پیش‌شماره."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..config import get_settings
from ..logging_setup import get_logger
from ..phones.patterns import AREA_CODE_TO_PROVINCE, PROVINCE_TO_AREA_CODE
from ..text.normalize import normalize_fa

log = get_logger("geo")

BUILTIN_FILE = "builtin_locations.json"
FULL_FILE = "full_locations.json"

CONTEXT_MARKERS = ("استان", "شهرستان", "شهر", "بخش", "دهستان", "واقع در", "واقع در استان")
# نام‌های کوتاه/عمومی که فقط با نشانه زمینه پذیرفته می‌شوند
SHORT_OR_AMBIGUOUS = {
    "نور", "بن", "ری", "لالی", "مهر", "جم", "قیر", "خوانسار", "راور", "مانه",
    "بهاباد", "اردل", "سامان", "بیضا", "خرامه", "کوار", "سرباز", "سرخه", "طالقان",
    "انار", "رابر", "کوهین", "شال", "گرمدره", "شهرضا",
}


@dataclass(frozen=True)
class LocationInfo:
    id: int
    kind: str  # province | county | city
    name: str
    name_key: str
    province: str | None = None
    county: str | None = None
    province_area_code: str | None = None

    @property
    def area_code(self) -> str | None:
        return self.province_area_code


@dataclass
class LocationMatch:
    province: str | None = None
    county: str | None = None
    city: str | None = None
    matched_names: list[str] = None  # type: ignore[assignment]
    method: str = "text_scan"
    confidence: int = 50

    def __post_init__(self) -> None:
        if self.matched_names is None:
            self.matched_names = []

    @property
    def area_code(self) -> str | None:
        if self.province:
            return PROVINCE_TO_AREA_CODE.get(self.province)
        return None

    @property
    def is_empty(self) -> bool:
        return not (self.province or self.county or self.city)


def _nospace(text: str) -> str:
    return re.sub(r"[\s\u200c\-\u00a0]+", "", normalize_fa(text or "", zwnj="remove"))


class Gazetteer:
    """فرهنگ جغرافیایی با تطبیق چندکلمه‌ای مقاوم به نیم‌فاصله."""

    def __init__(self) -> None:
        self.locations: list[LocationInfo] = []
        self.by_nospace: dict[str, list[LocationInfo]] = {}
        self._regex_safe: re.Pattern[str] | None = None
        self._regex_short: re.Pattern[str] | None = None
        self.dataset_version: str | None = None

    # ------------------------------------------------------------------ #
    def add(self, loc: LocationInfo) -> None:
        self.locations.append(loc)
        self.by_nospace.setdefault(_nospace(loc.name), []).append(loc)
        self._regex_safe = None
        self._regex_short = None

    def _build_regexes(self) -> None:
        """یک الگوی واحد روی «متن بدون جداکننده» تا نام‌های چندکلمه‌ای هم تطبیق یابند.

        نام‌ها بدون فاصله ذخیره می‌شوند («بندرگز») و متن ورودی ممکن است با فاصله/
        نیم‌فاصله باشد («بندر گز»). با حذف جداکننده‌ها از متن و نگاشت اندیس‌ها،
        هر دو شکل و نیز تفاوت نیم‌فاصله پوشش داده می‌شود.
        """
        keys = sorted(self.by_nospace, key=len, reverse=True)
        self._regex_safe = _build_alternation(keys)
        self._regex_short = None

    # ------------------------------------------------------------------ #
    def find_locations(self, text: str, *, allow_short: bool = True, limit: int = 12) -> list[LocationInfo]:
        """همه مکان‌های قابل تطبیق در متن (بدون تصمیم‌گیری درباره بهترین)."""
        return [info for info, _start, _end in self.iter_matches(text, allow_short=allow_short, limit=limit)]

    def iter_matches(
        self, text: str, *, allow_short: bool = True, limit: int = 24
    ) -> list[tuple[LocationInfo, int, int]]:
        """تطبیق مکان‌ها همراه با موقعیت آن‌ها در متن اصلی (برای انتخاب هوشمند)."""
        if not text:
            return []
        if self._regex_safe is None:
            self._build_regexes()
        norm = normalize_fa(text, zwnj="space")
        flat, index_map = _flatten_with_map(norm)
        if not flat:
            return []
        found: list[tuple[LocationInfo, int, int]] = []
        seen: set[tuple[str, str]] = set()
        regex = self._regex_safe
        if regex is None:
            return []

        for m in regex.finditer(flat):
            key = m.group(0)
            infos = self.by_nospace.get(key, [])
            if not infos:
                continue
            # نگاشت بازه تطبیق روی «متن بدون جداکننده» به متن اصلی
            orig_start = index_map[m.start()]
            orig_end = index_map[m.end() - 1] + 1
            if not _standalone(norm, orig_start, orig_end):
                continue  # بخشی از یک واژه بلندتر (مثل «خاشک»)
            needs_context = (
                len(key) <= 3 and key in _SHORT_AMBIGUOUS_KEYS
            ) or any(i.name in AMBIGUOUS_NAMES for i in infos)
            if needs_context and not _has_context(norm, orig_start, orig_end):
                continue
            if not allow_short and len(key) < 4:
                continue
            for info in infos:
                token = (info.kind, info.name)
                if token in seen:
                    continue
                seen.add(token)
                found.append((info, orig_start, orig_end))
            if len(found) >= limit:
                return found
        return found

    # ------------------------------------------------------------------ #
    def locate(self, text: str, *, hint_city: str | None = None, hint_province: str | None = None) -> LocationMatch:
        """بهترین مکان استخراج‌شده از متن — با اولویت نشانه‌های صریح و هم‌خوانی استان/شهرستان.

        ترتیب تصمیم برای شهر:
        ۱) شهر پرس‌وجو اگر در متن دیده شده باشد ۲) شهری با نشانه صریح «شهر ...»
        ۳) شهری هم‌نام با «شهرستان ...» صریح ۴) نخستین شهر یافت‌شده.
        استان و شهرستان سپس از روی همان شهر انتخاب می‌شوند تا ناسازگاری پیش نیاید.
        """
        raw = text or ""
        matches = self.iter_matches(raw)
        norm = normalize_fa(raw, zwnj="space")
        provinces = [i.name for i, _s, _e in matches if i.kind == "province"]
        counties = [i.name for i, _s, _e in matches if i.kind == "county"]
        city_infos = [i for i, _s, _e in matches if i.kind == "city"]
        cities = [i.name for i in city_infos]
        names = [i.name for i, _s, _e in matches]
        explicit = False

        marked_city: str | None = None
        marked_county: str | None = None
        for info, start_pos, _end in matches:
            window = norm[max(0, start_pos - 24) : start_pos]
            if re.search(r"(شهرستان|بخش)\s*$", window) and info.kind in ("county", "city"):
                marked_county = marked_county or info.name
                explicit = True
            if re.search(r"(?<!شهرستان\s)شهر\s*$", window) and info.kind == "city":
                marked_city = marked_city or info.name
                explicit = True
        if any(re.search(re.escape(m) + r"[\s\u200c]*" + re.escape(info.name), norm)
               for m in CONTEXT_MARKERS for info, _s, _e in matches):
            explicit = True

        city = None
        if hint_city and hint_city in cities:
            city = hint_city
            explicit = True
        elif hint_city and _nospace(hint_city) in {_nospace(m) for m in names}:
            city = hint_city
        elif marked_city:
            city = marked_city
        elif marked_county and marked_county in cities:
            city = marked_county
        elif cities:
            city = cities[0]

        # استان/شهرستان از روی شهر انتخاب‌شده (سازگاری درون‌رکوردی)
        chosen: LocationInfo | None = None
        if city:
            same = [i for i in city_infos if i.name == city]
            if hint_province:
                same.sort(key=lambda i: 0 if i.province == hint_province else 1)
            chosen = same[0] if same else None

        province = None
        if hint_province and hint_province in provinces:
            province = hint_province
        elif chosen and chosen.province:
            province = chosen.province
        elif provinces:
            province = provinces[0]

        county = None
        if chosen and chosen.county:
            county = chosen.county
        elif marked_county and marked_county in counties:
            county = marked_county
        elif counties:
            county = counties[0]

        confidence = 70 if explicit and city else (55 if city else (40 if province else 15))
        return LocationMatch(
            province=province,
            county=county,
            city=city,
            matched_names=names[:10],
            method="explicit_marker" if explicit else "text_scan",
            confidence=confidence,
        )

    def resolve_city(self, city_name: str | None) -> LocationInfo | None:
        if not city_name:
            return None
        for info in self.by_nospace.get(_nospace(city_name), []):
            if info.kind == "city":
                return info
        return None

    def area_code_for_city(self, city_name: str | None, province: str | None = None) -> str | None:
        info = self.resolve_city(city_name)
        if info and info.province_area_code:
            return info.province_area_code
        if province:
            return PROVINCE_TO_AREA_CODE.get(province)
        return None

    def province_area_code(self, province: str | None) -> str | None:
        return PROVINCE_TO_AREA_CODE.get(province or "")

    @property
    def city_count(self) -> int:
        return sum(1 for x in self.locations if x.kind == "city")

    @property
    def province_count(self) -> int:
        return sum(1 for x in self.locations if x.kind == "province")

    @property
    def county_count(self) -> int:
        return sum(1 for x in self.locations if x.kind == "county")


def _build_alternation(names: list[str]) -> re.Pattern[str] | None:
    if not names:
        return None
    parts = []
    for n in names[:4000]:
        parts.append(re.escape(n))
    try:
        return re.compile("|".join(parts))
    except re.error:  # pragma: no cover
        return None


# نام‌های استان/شهرستان که واژه عمومی هم هستند ⇒ نیازمند نشانه زمینه («استان ...»)
AMBIGUOUS_NAMES = frozenset(
    {
        "مرکزی", "فارس", "قم", "البرز", "ایلام", "گلستان", "قزوین", "سمنان",
        "خراسان رضوی", "خراسان شمالی", "خراسان جنوبی", "آذربایجان شرقی",
        "آذربایجان غربی", "سیستان و بلوچستان", "چهارمحال و بختیاری",
        "کهگیلویه و بویراحمد", "همدان", "بوشهر", "یزد", "کرمان", "خوزستان",
    }
)
_SEPARATORS = " \t\n\r\u200c\u200d\u200e\u200f\u00a0-_.،,()[]/\\+*"


_SHORT_AMBIGUOUS_KEYS = frozenset(_nospace(n) for n in SHORT_OR_AMBIGUOUS)


def _flatten_with_map(text: str) -> tuple[str, list[int]]:
    """حذف جداکننده‌ها و نگه‌داشتن نقشه اندیس‌ها به متن اصلی."""
    chars: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(text):
        if ch in _SEPARATORS or ch.isspace():
            continue
        chars.append(ch)
        idx.append(i)
    return "".join(chars), idx


def _standalone(text: str, start: int, end: int) -> bool:
    """آیا بازه یافته‌شده در متن اصلی یک واژه کامل است (نه بخشی از واژه بلندتر)؟"""
    before = text[start - 1] if start > 0 else " "
    after = text[end] if end < len(text) else " "
    return (before in _SEPARATORS or before.isspace()) and (after in _SEPARATORS or after.isspace())


def _has_context(text: str, start: int, end: int, window: int = 18) -> bool:
    ctx = text[max(0, start - window) : min(len(text), end + window)]
    return any(marker in ctx for marker in CONTEXT_MARKERS)


# --------------------------------------------------------------------------- #
# بارگذاری داده مرجع
# --------------------------------------------------------------------------- #
def _reference_dir() -> Path:
    return Path(get_settings().data_dir) / "reference"


def load_builtin_gazetteer() -> Gazetteer:
    gz = Gazetteer()
    path = _reference_dir() / BUILTIN_FILE
    if not path.exists():
        log.warning("فایل داده مرجع مکان‌ها یافت نشد: %s", path)
        return gz
    data = json.loads(path.read_text(encoding="utf-8"))
    gz.dataset_version = (data.get("meta") or {}).get("version")
    next_id = 1
    for prov in data.get("provinces", []):
        pname = prov["name"]
        pcode = (prov.get("area_codes") or [None])[0]
        pid = next_id
        next_id += 1
        gz.add(LocationInfo(id=pid, kind="province", name=pname, name_key=_nospace(pname),
                            province=pname, province_area_code=pcode))
        for county in prov.get("counties", []):
            cname = county["name"]
            cid = next_id
            next_id += 1
            gz.add(LocationInfo(id=cid, kind="county", name=cname, name_key=_nospace(cname),
                                province=pname, county=cname, province_area_code=pcode))
            for city in county.get("cities", []):
                gz.add(LocationInfo(id=next_id, kind="city", name=city, name_key=_nospace(city),
                                    province=pname, county=cname, province_area_code=pcode))
                next_id += 1
    log.debug("فرهنگ جغرافیایی: %s استان، %s شهرستان، %s شهر",
              gz.province_count, gz.county_count, gz.city_count)
    return gz


def load_full_gazetteer_if_present() -> Gazetteer | None:
    """اگر داده کامل (fetch-geo) موجود باشد، آن را ترجیح می‌دهیم."""
    path = _reference_dir() / FULL_FILE
    if not path.exists():
        return None
    try:
        gz = Gazetteer()
        data = json.loads(path.read_text(encoding="utf-8"))
        gz.dataset_version = (data.get("meta") or {}).get("dataset_version", "official")
        next_id = 1
        provinces = {p["id"]: p for p in data.get("provinces", [])}
        counties = {c["id"]: c for c in data.get("counties", [])}
        for p in provinces.values():
            code = p.get("area_code") or p.get("tel_prefix")
            gz.add(LocationInfo(id=next_id, kind="province", name=p["name"], name_key=_nospace(p["name"]),
                                province=p["name"], province_area_code=code))
            next_id += 1
        for c in counties.values():
            prov = provinces.get(c.get("province_id"), {})
            gz.add(LocationInfo(id=next_id, kind="county", name=c["name"], name_key=_nospace(c["name"]),
                                province=prov.get("name"), county=c["name"],
                                province_area_code=prov.get("area_code") or prov.get("tel_prefix")))
            next_id += 1
        for c in data.get("cities", []):
            prov = provinces.get(c.get("province_id"), {})
            county = counties.get(c.get("county_id"), {})
            gz.add(LocationInfo(id=next_id, kind="city", name=c["name"], name_key=_nospace(c["name"]),
                                province=prov.get("name"), county=county.get("name"),
                                province_area_code=prov.get("area_code") or prov.get("tel_prefix")))
            next_id += 1
        return gz
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        log.warning("بارگذاری داده کامل مکان‌ها ناموفق بود: %s", exc)
        return None


@lru_cache(maxsize=1)
def get_gazetteer() -> Gazetteer:
    gz = load_full_gazetteer_if_present()
    if gz is not None and gz.city_count > 0:
        return gz
    return load_builtin_gazetteer()


def reset_gazetteer_cache() -> None:
    get_gazetteer.cache_clear()


def area_code_province(code: str) -> str | None:
    return AREA_CODE_TO_PROVINCE.get(code)
