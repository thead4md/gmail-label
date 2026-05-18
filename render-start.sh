#!/bin/sh
# Render container startup:
# 1. Validate required env vars are present
# 2. Restore latest DB snapshot from S3 via Litestream
# 3. Start Litestream replica mode + Streamlit

set -e

DB_PATH="${MAILMIND_DB_PATH:-/tmp/mailmind.db}"
export MAILMIND_DB_PATH="$DB_PATH"

echo "[render-start] DB path: $DB_PATH"
echo "[render-start] S3 bucket: ${LITESTREAM_S3_BUCKET:-NOT SET}"
echo "[render-start] S3 endpoint: ${LITESTREAM_S3_ENDPOINT:-NOT SET}"
echo "[render-start] Access key set: $([ -n "$LITESTREAM_ACCESS_KEY_ID" ] && echo YES || echo NO)"

# Abort early if credentials are missing
if [ -z "$LITESTREAM_ACCESS_KEY_ID" ] || [ -z "$LITESTREAM_SECRET_ACCESS_KEY" ] || [ -z "$LITESTREAM_S3_BUCKET" ]; then
  echo "[render-start] ERROR: Missing required env vars."
  echo "[render-start] Set in Render dashboard: LITESTREAM_ACCESS_KEY_ID, LITESTREAM_SECRET_ACCESS_KEY, LITESTREAM_S3_BUCKET, LITESTREAM_S3_ENDPOINT"
  exit 1
fi

echo "[render-start] Restoring DB from S3..."
litestream restore \
  -config /etc/litestream.yml \
  -if-replica-exists \
  "$DB_PATH" \
  && echo "[render-start] Restore complete." \
  || echo "[render-start] No existing replica, starting fresh."

echo "[render-start] Starting Litestream replica + Streamlit..."
exec litestream replicate \
  -config /etc/litestream.yml \
  -- python -m streamlit run mailmind/review_dashboard.py \
       --server.address=0.0.0.0 \
       --server.port=10000 \
       --server.headless=true \
       --server.enableCORS=false \
       --browser.gatherUsageStats=false
