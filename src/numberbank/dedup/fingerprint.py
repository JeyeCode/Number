"""اثرانگشت‌ها و کلیدهای انسداد (Blocking) برای یکتاسازی."""

from __future__ import annotations

from hashlib import sha1

from ..relevance.lexicon import GENERIC_HOSTS
from ..text.normalize import domain_of, name_key, registrable_domain


def _h(*parts: str | None) -> str:
    raw = "\u0001".join((p or "").strip().lower() for p in parts)
    return sha1(raw.encode("utf-8")).hexdigest()[:32]


def business_fingerprint(
    *,
    name: str | None,
    city_name: str | None,
    domain: str | None = None,
    phone_e164: str | None = None,
) -> str:
    """اثرانگشت یکتای کسب‌وکار.

    اولویت: نام+شهر ← دامنه ← شماره. اگر نام موجود نباشد از دامنه/شماره استفاده می‌شود
    و در نهایت به هش محتوای موجود تکیه می‌کنیم (هیچ داده‌ای جعل نمی‌شود).
    """
    nk = name_key(name)
    ck = name_key(city_name)
    reg = registrable_domain(domain) if domain else None
    if reg and reg in GENERIC_HOSTS:
        reg = None
    if nk:
        return "b:" + _h(nk, ck)
    if reg:
        return "d:" + _h(reg)
    if phone_e164:
        return "p:" + _h(phone_e164)
    return "x:" + _h(str(name), str(city_name), str(domain), str(phone_e164))


def phone_fingerprint(e164: str) -> str:
    return "ph:" + sha1((e164 or "").encode("utf-8")).hexdigest()[:32]


def query_fingerprint(source_key: str, query_text: str) -> str:
    return sha1(f"{source_key}\u0001{query_text.strip().lower()}".encode()).hexdigest()[:32]


def observation_fingerprint(*, url: str | None, phone_e164: str | None, business_fp: str | None) -> str:
    url_dom = domain_of(url) or ""
    raw = f"{url_dom}\u0001{url or ''}\u0001{phone_e164 or ''}\u0001{business_fp or ''}"
    return sha1(raw.encode("utf-8")).hexdigest()[:32]


def blocking_keys(
    *,
    name: str | None,
    city_name: str | None,
    domain: str | None,
    phones: list[str],
    address: str | None = None,
) -> set[str]:
    """کلیدهای انسداد برای جستجوی جفت‌های مشکوک به تکرار (به‌جای مقایسه O(n²))."""
    keys: set[str] = set()
    nk = name_key(name)
    ck = name_key(city_name)
    if nk:
        keys.add(f"n:{nk[:14]}")
        keys.add(f"n2:{nk[:6]}")
    if nk and ck:
        keys.add(f"nc:{nk[:12]}|{ck[:8]}")
    reg = registrable_domain(domain) if domain else None
    if reg and reg not in GENERIC_HOSTS:
        keys.add(f"d:{reg}")
    for p in phones:
        keys.add(f"p:{p}")
    if address:
        ak = name_key(address)
        if len(ak) >= 10:
            keys.add(f"a:{ak[:16]}")
    return keys


def phone_blocking_keys(phones: list[str]) -> set[str]:
    return {f"p:{p}" for p in phones}
