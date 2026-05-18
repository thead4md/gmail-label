#!/bin/sh
# Render container startup:
# 1. Restore latest DB snapshot from S3 via Litestream
# 2. Start Litestream in replica mode (keeps DB in sync with Mac Mini)
# 3. Launch Streamlit on port 10000

set -e

DB_PATH="${MAILMIND_DB_PATH:-/tmp/mailmind.db}"
export MAILMIND_DB_PATH="$DB_PATH"

echo "[render-start] DB path: $DB_PATH"
echo "[render-start] Restoring from S3 bucket: $LITESTREAM_S3_BUCKET"

litestream restore \
  -config /etc/litestream.yml \
  -if-replica-exists \
  "$DB_PATH" || echo "[render-start] No existing replica found, will start empty."

echo "[render-start] Starting Litestream replica + Streamlit..."
exec litestream replicate -config /etc/litestream.yml \
  -- python -m streamlit run mailmind/review_dashboard.py \
       --server.address=0.0.0.0 \
       --server.port=10000 \
       --server.headless=true \
       --server.enableCORS=false \
       --browser.gatherUsageStats=false
