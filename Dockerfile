FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CATALOG_DATABASE=/var/lib/nutrilite/products.sqlite3 \
    CATALOG_SEED=/app/data/raw/amway-all-products.json \
    HOST=0.0.0.0 \
    PORT=8000 \
    OPENAI_MODEL=gpt-5.4-mini \
    ANALYSIS_RATE_LIMIT_PER_HOUR=20 \
    MAX_REQUEST_BYTES=8500000 \
    MAX_IMAGE_BYTES=6000000

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 nutrilite \
    && useradd --uid 10001 --gid nutrilite --create-home nutrilite \
    && mkdir -p /var/lib/nutrilite \
    && chown -R nutrilite:nutrilite /var/lib/nutrilite

COPY requirements.txt schema.sql ./
COPY *.py ./
COPY data/raw/amway-all-products.json ./data/raw/amway-all-products.json
COPY web/ ./web/

USER nutrilite
EXPOSE 8000
VOLUME ["/var/lib/nutrilite"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"]

CMD ["python", "docker_entrypoint.py"]
