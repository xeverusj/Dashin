#!/bin/bash
# Railway / general startup script for Dashin Research
set -e

# Initialise / migrate the database on every deploy
echo "[startup] Initialising database..."
python core/db.py

# Start Streamlit on the port provided by the platform
echo "[startup] Starting Streamlit on port ${PORT:-8080}..."
exec streamlit run app.py \
  --server.port "${PORT:-8080}" \
    --server.address 0.0.0.0 \
      --server.headless true \
        --browser.gatherUsageStats false
