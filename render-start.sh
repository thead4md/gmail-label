#!/bin/sh
# Render container startup:
# 1. Restore latest DB snapshot from S3 via Litestream
# 2. Start Litestream in replica mode (keeps DB in sync with Mac Mini)
# 3. Launch Streamlit dashboard
#
# Note: Render free tier has no persistent disk.
# DB lives in /tmp (ephemeral) and is restored fresh from S3 on each deploy/restart.
# Litestream replica mode then keeps it updated every 10s.

set -e

DB_PATH="${MAILMIND_DB_PATH:-/tmp/mailmind.db}"

echo "[render-start] Restoring DB from S3..."
litestream restore -config /etc/litestream.yml -if-replica-exists "$DB_PATH" || echo "[render-start] No existing replica, starting fresh."

echo "[render-start] Starting Litestream replica + Streamlit..."
exec litestream replicate -config /etc/litestream.yml \
  -- python -m streamlit run mailmind/review_dashboard.py \
       --server.address=0.0.0.0 \
       --server.port=10000 \
       --server.headless=true \
       --server.enableCORS=false \
       --browser.gatherUsageStats=false
