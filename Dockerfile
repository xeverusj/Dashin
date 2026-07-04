# Dashin dashboard + ingest API — one image, one public URL.
# nginx routes /api/* to the ingest server (api_server.py) and everything else
# to Streamlit. Scrapers run on client machines, so NO browser is installed here
# (keeps the image small); the host only serves dashboards and receives pushes.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUTF8=1 \
    DB_PATH=/data/system/dashin.db \
    PORT=8080

# nginx (reverse proxy) + gettext-base (envsubst for the PORT template)
RUN apt-get update && apt-get install -y --no-install-recommends \
        nginx gettext-base curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# No `playwright install` — the server never opens a browser.
RUN pip install -r requirements.txt

COPY . .

# Persistent DB lives on a mounted volume at /data.
RUN mkdir -p /data && chmod +x deploy/start.sh

EXPOSE 8080

CMD ["bash", "deploy/start.sh"]
