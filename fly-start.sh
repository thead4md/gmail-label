#!/bin/sh
# Fly.io container startup:
# 1. Restore latest DB snapshot from S3 via Litestream
# 2. Start Litestream in replica mode (keeps DB in sync with Mac Mini)
# 3. Launch Streamlit dashboard

set -e

DB_PATH="${MAILMIND_DB_PATH:-/data/mailmind.db}"

echo "[fly-start] Restoring DB from S3..."
litestream restore -config /etc/litestream.yml -if-replica-exists "$DB_PATH" || true

echo "[fly-start] Starting Litestream replica + Streamlit..."
exec litestream replicate -config /etc/litestream.yml \
  -- python -m streamlit run mailmind/review_dashboard.py \
       --server.address=0.0.0.0 \
       --server.port=8501 \
       --server.headless=true \
       --server.enableCORS=false \
       --browser.gatherUsageStats=false
