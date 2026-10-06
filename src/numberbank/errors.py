"""خطاهای دامنه‌ای سامانه (همه قابل گرفتن و لاگ‌شدن)."""

from __future__ import annotations


class NumberBankError(Exception):
    """پایه همه خطاهای سامانه."""


class ConfigError(NumberBankError):
    pass


class RobotsDisallowed(NumberBankError):
    """دسترسی به URL توسط robots.txt مجاز نیست."""

    def __init__(self, url: str, user_agent: str = "*") -> None:
        super().__init__(f"robots.txt دسترسی به {url} را برای {user_agent} مجاز نمی‌داند")
        self.url = url


class FetchError(NumberBankError):
    def __init__(self, url: str, message: str, status_code: int | None = None) -> None:
        super().__init__(f"{message} :: {url}")
        self.url = url
        self.status_code = status_code


class SourceUnavailable(NumberBankError):
    """منبع در دسترس نیست (قطعی/مسدود/کلید نامعتبر)."""


class ParseError(NumberBankError):
    pass


class RateLimited(NumberBankError):
    """منبع ما را محدود کرده است؛ باید بعداً تلاش شود."""


class DiscoveryError(NumberBankError):
    """خطای منطقی در برنامه‌ریزی/اجرای یک Job کشف داده (مثلاً Job ناموجود)."""


class StorageError(NumberBankError):
    """خطا در لایه ذخیره‌سازی (برخورد یکتایی، داده ناسازگار)."""


class ExportError(NumberBankError):
    """خطا در تولید خروجی (قالب ناشناخته/نوشتن فایل)."""
