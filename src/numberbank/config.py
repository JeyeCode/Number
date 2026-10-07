"""تنظیمات مرکزی سامانه NumberBank.

همه مقادیر از متغیرهای محیطی با پیشوند ``NUMBERBANK_`` یا فایل ``.env`` خوانده می‌شوند.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NUMBERBANK_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- مسیرها ---
    data_dir: Path = DEFAULT_DATA_DIR
    database_url: str = "sqlite:///data/numberbank.db"
    log_dir: Path = DEFAULT_DATA_DIR / "logs"
    export_dir: Path = DEFAULT_DATA_DIR / "exports"
    log_level: str = "INFO"

    # --- پایگاه داده ---
    db_echo: bool = False
    db_pool_size: int = 10

    # --- HTTP / ادب شبکه ---
    user_agent: str = (
        "NumberBankBot/1.0 (+https://github.com/JeyeCode/Number; contact: admin@example.com)"
    )
    request_timeout: float = 20.0
    max_retries: int = 3
    respect_robots: bool = True
    per_domain_rps: float = 1.0
    global_concurrency: int = 8
    proxy_url: str | None = None
    http_cache_ttl_hours: int = 72
    max_page_bytes: int = 2_000_000

    # --- موتورهای جستجو ---
    bing_api_key: str | None = None
    google_cse_key: str | None = None
    google_cse_cx: str | None = None
    serpapi_key: str | None = None
    searx_instances: str = "https://searx.be"
    enable_unofficial_scrapers: bool = True
    overpass_endpoints: str = (
        "https://overpass-api.de/api/interpreter,"
        "https://overpass.kumi.systems/api/interpreter,"
        "https://overpass.private.coffee/api/interpreter"
    )
    enable_overpass: bool = True

    # --- صف / کارگرها ---
    queue_backend: str = "sqlite"  # sqlite | redis
    redis_url: str = "redis://localhost:6379/0"
    workers: int = 8
    job_time_limit: int = 1800
    max_attempts: int = 3
    lease_seconds: int = 120

    # --- پیش‌فرض‌های کشف ---
    min_confidence: int = 70
    default_target: int = 1000
    max_pages_per_query: int = 3
    results_per_page: int = 20
    query_refresh_days: int = 14
    revalidate_after_days: int = 90
    max_pages_per_job: int = 4000
    fetch_candidate_pages: bool = True
    max_candidate_pages_per_query: int = 6

    # --- حالت اجرا ---
    offline: bool = False
    include_synthetic_by_default: bool = False

    # --- سرور ---
    host: str = "0.0.0.0"
    port: int = 8000

    # --- امتیازدهی (قابل تنظیم بدون تغییر کد) ---
    weight_source: float = 0.35
    weight_corroboration: float = 0.25
    weight_fields: float = 0.20
    weight_phone: float = 0.20
    relevance_gate: int = 30
    relevance_gate_confidence_cap: int = 25
    dedup_merge_threshold: float = 0.80
    dedup_review_threshold: float = 0.62

    @field_validator("data_dir", "log_dir", "export_dir", mode="before")
    @classmethod
    def _abs_path(cls, v):
        if v in (None, ""):
            return v
        p = Path(v)
        return p if p.is_absolute() else (PROJECT_ROOT / p)

    @property
    def searx_list(self) -> list[str]:
        return [s.strip().rstrip("/") for s in self.searx_instances.split(",") if s.strip()]

    @property
    def overpass_list(self) -> list[str]:
        return [s.strip() for s in self.overpass_endpoints.split(",") if s.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def resolve_sqlite_path(self) -> Path | None:
        """مسیر فایل SQLite را برمی‌گرداند (برای ساخت پوشه والد)."""
        if not self.is_sqlite:
            return None
        raw = self.database_url.split("///", 1)[-1]
        if raw in ("", ":memory:"):
            return None
        p = Path(raw)
        return p if p.is_absolute() else (PROJECT_ROOT / p)

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.log_dir, self.export_dir):
            Path(d).mkdir(parents=True, exist_ok=True)

    def with_db(self, database_url: str) -> Settings:
        return self.model_copy(update={"database_url": database_url})


@lru_cache(maxsize=4)
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s


def reset_settings_cache() -> None:
    get_settings.cache_clear()
