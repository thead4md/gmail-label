#!/bin/sh
# Render container startup
set -e

DB_PATH="${MAILMIND_DB_PATH:-/tmp/mailmind.db}"
export MAILMIND_DB_PATH="$DB_PATH"

echo "[render-start] DB path:      $DB_PATH"
echo "[render-start] S3 bucket:    ${LITESTREAM_S3_BUCKET:-NOT SET}"
echo "[render-start] S3 endpoint:  ${LITESTREAM_S3_ENDPOINT:-NOT SET}"
echo "[render-start] Access key:   $([ -n "$LITESTREAM_ACCESS_KEY_ID" ] && echo YES || echo NO)"

if [ -z "$LITESTREAM_ACCESS_KEY_ID" ] || [ -z "$LITESTREAM_SECRET_ACCESS_KEY" ] || [ -z "$LITESTREAM_S3_BUCKET" ] || [ -z "$LITESTREAM_S3_ENDPOINT" ]; then
  echo "[render-start] ERROR: Missing env vars. Required in Render dashboard:"
  echo "  MAILMIND_DB_PATH=/tmp/mailmind.db"
  echo "  LITESTREAM_ACCESS_KEY_ID"
  echo "  LITESTREAM_SECRET_ACCESS_KEY"
  echo "  LITESTREAM_S3_BUCKET"
  echo "  LITESTREAM_S3_ENDPOINT  (e.g. s3.us-west-004.backblazeb2.com)"
  exit 1
fi

echo "[render-start] Restoring DB from S3..."
litestream restore \
  -config /etc/litestream.yml \
  -if-replica-exists \
  "$DB_PATH" \
  && echo "[render-start] Restore complete." \
  || echo "[render-start] No replica found, starting fresh."

echo "[render-start] Starting Streamlit via Litestream..."
# Note: litestream replicate -exec runs a subprocess and keeps the DB in sync.
# Do NOT pass a positional DB path here — the config file defines it.
exec litestream replicate \
  -config /etc/litestream.yml \
  -exec "python -m streamlit run mailmind/review_dashboard.py \
    --server.address=0.0.0.0 \
    --server.port=10000 \
    --server.headless=true \
    --server.enableCORS=false \
    --browser.gatherUsageStats=false"
