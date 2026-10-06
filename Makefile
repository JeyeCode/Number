.PHONY: help venv install install-all init demo run serve test test-live lint clean fetch-geo docker-build docker-up

PY := .venv/bin/python
PIP := .venv/bin/pip

help:
	@echo "NumberBank — دستورات موجود:"
	@echo "  make install      نصب محیط مجازی و وابستگی‌های اصلی"
	@echo "  make install-all  نصب همه وابستگی‌ها (API + Excel + Dev)"
	@echo "  make init         ساخت دیتابیس + بارگذاری داده‌های مرجع (شهرها، دسته‌ها، منابع)"
	@echo "  make fetch-geo    دریافت داده کامل تقسیمات کشوری (۱۴۸۱ شهر) از منبع رسمی"
	@echo "  make demo         اجرای نمونه کاملاً آفلاین روی Fixture ها (بدون اینترنت)"
	@echo "  make run          یک Job کشف واقعی از اینترنت (شهر نمونه)"
	@echo "  make serve        اجرای API + داشبورد روی 0.0.0.0:8000"
	@echo "  make test         اجرای تست‌ها (فقط آفلاین)"
	@echo "  make test-live    اجرای تست‌های نیازمند اینترنت واقعی"
	@echo "  make clean        حذف دیتابیس و خروجی‌های تولیدشده"

venv:
	test -d .venv || python3 -m venv .venv
	$(PIP) install --quiet --upgrade pip

install: venv
	$(PIP) install -e .

install-all: venv
	$(PIP) install -e ".[api,excel,dev,postgres,redis]"

init:
	$(PY) -m numberbank init --reset

fetch-geo:
	$(PY) -m numberbank fetch-geo

demo:
	$(PY) -m numberbank demo

run:
	$(PY) -m numberbank discover --province "خوزستان" --cities 2 --target 40 --sources duckduckgo_html,mojeek_html

serve:
	$(PY) -m numberbank serve

test:
	$(PY) -m pytest tests/unit tests/integration -v

test-live:
	$(PY) -m pytest tests/live -v -m live

lint:
	.venv/bin/ruff check src tests 2>/dev/null || echo "ruff نصب نیست (make install-all)"

clean:
	rm -rf data/numberbank.db* data/logs data/exports data/cache .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

docker-build:
	docker build -t numberbank:1.0.0 .

docker-up:
	docker compose up -d --build
