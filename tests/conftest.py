"""تجهیزات مشترک آزمون‌ها: دیتابیس موقت، تنظیمات آفلاین و پیکره آزمایشی."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("NUMBERBANK_OFFLINE", "true")
os.environ.setdefault("NUMBERBANK_LOG_LEVEL", "WARNING")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


@pytest.fixture()
def temp_settings(tmp_path: Path, repo_root: Path):
    """تنظیمات با دیتابیس و شاخه داده موقت (بدون آسیب به داده واقعی)."""
    from numberbank.config import Settings

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    reference = data_dir / "reference"
    reference.mkdir(parents=True, exist_ok=True)
    for name in ("builtin_locations.json", "categories.json", "directories.json", "blocklist.json"):
        src = repo_root / "data" / "reference" / name
        if src.exists():
            shutil.copy2(src, reference / name)
    fixtures_src = repo_root / "data" / "fixtures"
    fixtures_dst = data_dir / "fixtures"
    if fixtures_src.exists() and not fixtures_dst.exists():
        shutil.copytree(fixtures_src, fixtures_dst)

    settings = Settings(
        offline=True,
        data_dir=str(data_dir),
        database_url=f"sqlite:///{data_dir / 'test.db'}",
        log_level="WARNING",
        workers=2,
    )
    yield settings


@pytest.fixture()
def database(temp_settings):
    from numberbank.db.session import Database, reset_databases

    reset_databases()
    db = Database(temp_settings)
    db.create_all()
    yield db
    db.dispose()
    reset_databases()


@pytest.fixture()
def seeded(database, temp_settings):
    """دیتابیس با داده مرجع (استان/شهرستان/شهر، دسته‌ها، منابع) بارگذاری می‌شود."""
    from numberbank.services.reference_service import seed_all

    seed_all(database=database, settings=temp_settings)
    return database
