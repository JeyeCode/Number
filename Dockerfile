# NumberBank — تصویر اجرایی (API + داشبورد + CLI)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NUMBERBANK_DATA_DIR=/data \
    NUMBERBANK_DATABASE_URL=sqlite:////data/numberbank.db \
    NUMBERBANK_HOST=0.0.0.0 \
    NUMBERBANK_PORT=8000

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e ".[api,excel]" && mkdir -p /data

# بارگذاری داده مرجع و ساخت جداول در اولین اجرا
RUN numberbank init || true

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status==200 else 1)"

CMD ["numberbank", "serve", "--host", "0.0.0.0", "--port", "8000"]
