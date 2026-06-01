#!/bin/sh
# Fly.io container startup:
# 1. Restore latest DB snapshot from S3 via Litestream
# 2. Start Litestream in replica mode (keeps DB in sync with the source DB)
# 3. Launch the Streamlit dashboard
# 4. Launch the MailMind backend app process

set -e

DB_PATH="${MAILMIND_DB_PATH:-/data/mailmind.db}"
STREAMLIT_PORT="${STREAMLIT_PORT:-8501}"
LITESTREAM_BUCKET="${LITESTREAM_S3_BUCKET:-}"
LITESTREAM_ENDPOINT="${LITESTREAM_S3_ENDPOINT:-}"
LITESTREAM_ACCESS_KEY_ID="${LITESTREAM_ACCESS_KEY_ID:-}"
LITESTREAM_SECRET_ACCESS_KEY="${LITESTREAM_SECRET_ACCESS_KEY:-}"

if command -v streamlit >/dev/null 2>&1 && [ -f /app/mailmind/dashboard/app.py ]; then
  echo "[fly-start] Starting Streamlit dashboard on :${STREAMLIT_PORT}..."
  (
    cd /app
    export PYTHONPATH="/app${PYTHONPATH:+:$PYTHONPATH}"
    streamlit run mailmind/dashboard/app.py \
      --server.address 0.0.0.0 \
      --server.port "${STREAMLIT_PORT}" \
      --server.headless true
  ) &
else
  echo "[fly-start] Streamlit dashboard is unavailable; starting MailMind app only..."
fi

if [ -n "$LITESTREAM_BUCKET" ] && [ -n "$LITESTREAM_ENDPOINT" ] && [ -n "$LITESTREAM_ACCESS_KEY_ID" ] && [ -n "$LITESTREAM_SECRET_ACCESS_KEY" ]; then
  echo "[fly-start] Litestream: all S3 env vars present (bucket=$LITESTREAM_BUCKET endpoint=$LITESTREAM_ENDPOINT)."
  echo "[fly-start] Restoring DB from S3 if a replica exists (won't overwrite existing local DB)..."
  litestream restore -config /etc/litestream.yml -if-replica-exists "$DB_PATH" || true

  echo "[fly-start] Starting Litestream replica + MailMind app..."
  exec litestream replicate \
    -config /etc/litestream.yml \
    -exec "python -m mailmind.main run --watch"
fi

# Be specific about WHICH var is missing so debugging is one log line.
missing=""
[ -z "$LITESTREAM_BUCKET" ]            && missing="$missing LITESTREAM_S3_BUCKET"
[ -z "$LITESTREAM_ENDPOINT" ]          && missing="$missing LITESTREAM_S3_ENDPOINT"
[ -z "$LITESTREAM_ACCESS_KEY_ID" ]     && missing="$missing LITESTREAM_ACCESS_KEY_ID"
[ -z "$LITESTREAM_SECRET_ACCESS_KEY" ] && missing="$missing LITESTREAM_SECRET_ACCESS_KEY"
echo "[fly-start] Litestream disabled — missing env var(s):${missing}. DB is local-only."
exec python -m mailmind.main run --watch
