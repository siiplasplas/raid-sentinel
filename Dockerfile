# Raid Sentinel - tek asamali imaj.
#
# Calisma dizini /app; .env ve data/ oraya baglanir. Ayarlar calisma
# dizinine gore cozuldugu icin baska bir yol hesabi gerekmiyor.

FROM python:3.12-slim

# curl saglik kontrolu icin; geri kalan her sey saf Python.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Once bagimliliklar: kaynak degistiginde katman onbellegi bozulmasin.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# Kok olmayan kullanici. data/ ona ait olmali, aksi halde SQLite yazamaz.
RUN useradd --create-home --uid 10001 sentinel \
    && mkdir -p /app/data \
    && chown -R sentinel:sentinel /app
USER sentinel

# Kap icinde 0.0.0.0'a baglanmali; disariya acilmasi compose'daki port
# eslemesiyle kontrol ediliyor (varsayilan: yalnizca 127.0.0.1).
ENV HTTP_HOST=0.0.0.0 \
    HTTP_PORT=8787 \
    DB_PATH=/app/data/sentinel.db

EXPOSE 8787

# Kap sagligi = surec ayakta mi. /health ucu bunun yerine ESLESME
# durumunu raporluyor ve eslesmeden once bilerek 503 doner; onu dis
# izleme servisine baglayin, kap saglik kontroluna degil.
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8787/ >/dev/null || exit 1

CMD ["sentinel", "run"]
