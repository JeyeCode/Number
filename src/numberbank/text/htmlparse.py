"""لایه سازگاری پارسر HTML (selectolax با پشتیبانی از نسخه‌های مختلف)."""

from __future__ import annotations

try:  # selectolax >= 1.0
    from selectolax.lexbor import LexborHTMLParser as HTMLParser  # type: ignore
    from selectolax.lexbor import LexborNode as Node  # type: ignore

    BACKEND = "lexbor"
except ImportError:  # pragma: no cover - نسخه‌های قدیمی‌تر
    try:
        from selectolax.parser import HTMLParser, Node  # type: ignore

        BACKEND = "modest"
    except ImportError:  # pragma: no cover - نبود selectolax
        HTMLParser = None  # type: ignore
        Node = None  # type: ignore
        BACKEND = "none"

__all__ = ["HTMLParser", "Node", "BACKEND", "parse_html", "parse_available"]


def parse_available() -> bool:
    return HTMLParser is not None


def parse_html(html: str):
    """پارس HTML؛ در نبود selectolax، None برمی‌گرداند (فراخوان باید محافظت کند)."""
    if HTMLParser is None:  # pragma: no cover
        return None
    return HTMLParser(html or "")
