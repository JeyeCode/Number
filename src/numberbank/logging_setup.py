"""لاگ‌گیری ساخت‌یافته (کنسول + فایل JSONL + رویدادهای دیتابیس)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.logging import RichHandler

from .config import Settings

console = Console(stderr=False)
_stdout_console = Console()
_configured = False


class JsonlFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "context", None)
        if extra:
            payload["context"] = extra
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(settings: Settings, verbose: bool = False) -> None:
    global _configured
    if _configured:
        return
    level = getattr(logging, (settings.log_level or "INFO").upper(), logging.INFO)
    if verbose:
        level = logging.DEBUG

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    rich_handler = RichHandler(console=console, rich_tracebacks=True, show_path=False, markup=False)
    rich_handler.setLevel(level)
    rich_handler.setFormatter(logging.Formatter("%(name)-24s %(message)s", datefmt="%H:%M:%S"))
    root.addHandler(rich_handler)

    try:
        Path(settings.log_dir).mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(Path(settings.log_dir) / "numberbank.jsonl", encoding="utf-8")
        fh.setLevel(level)
        fh.setFormatter(JsonlFormatter())
        root.addHandler(fh)
    except OSError:  # پوشه لاگ غیرقابل نوشتن => فقط کنسول
        pass

    for noisy in ("httpx", "httpcore", "asyncio", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"numberbank.{name}")


def log_event(logger: logging.Logger, level: int, message: str, **context: Any) -> None:
    logger.log(level, message, extra={"context": context})


def print_banner(text: str) -> None:
    _stdout_console.print(f"[bold cyan]{text}[/bold cyan]")
