#!/usr/bin/env bash
# Boots the whole hosted app in one container:
#   1. ingest API  (api_server.py)      on 127.0.0.1:8000
#   2. Streamlit   (app.py)             on 127.0.0.1:8501
#   3. nginx reverse proxy              on 0.0.0.0:${PORT}   (the public port)
set -e

PORT="${PORT:-8080}"
export DB_PATH="${DB_PATH:-/data/dashin.db}"
mkdir -p "$(dirname "$DB_PATH")"

echo "[start] DB_PATH=$DB_PATH  public PORT=$PORT"

# 1. Ingest API (background) — receives scraper pushes.
PORT=8000 HOST=127.0.0.1 python api_server.py &
API_PID=$!

# 2. Streamlit dashboard (background) — internal port, fronted by nginx.
streamlit run app.py \
    --server.port 8501 \
    --server.address 127.0.0.1 \
    --server.headless true \
    --server.enableCORS false \
    --server.enableXsrfProtection false &
ST_PID=$!

# 3. nginx (foreground) — single public entrypoint. Render the port into config.
export PORT
envsubst '${PORT}' < deploy/nginx.conf.template > /etc/nginx/conf.d/default.conf
rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true

# If either backend dies, take the container down so the platform restarts it.
trap "kill $API_PID $ST_PID 2>/dev/null || true" EXIT
echo "[start] nginx listening on :$PORT"
nginx -g "daemon off;"
