#!/bin/sh
# Fly.io container startup:
# 1. Restore latest DB snapshot from S3 via Litestream
# 2. Start Litestream in replica mode (keeps DB in sync with the source DB)
# 3. Launch the FastAPI web app (API + built React SPA)
# 4. Launch the MailMind backend app process
#
# Step 4 depends on MAILMIND_RUN_MODE:
#   unset / "watch" (default) — background `run --watch` loop, polling Gmail
#     continuously forever. Requires the machine to stay running
#     (fly.toml: min_machines_running=1, auto_stop_machines=false). This is
#     the original behavior, unchanged below.
#   "external" — the watch loop is NOT started. An external trigger (see
#     .github/workflows/poll-trigger.yml) instead calls POST /internal/poll
#     on this same FastAPI process to run one cycle on demand. This is what
#     makes scale-to-zero (fly.toml: min_machines_running=0,
#     auto_stop_machines=true) safe — there's no long-lived background loop
#     whose state would be lost when the machine suspends.

set -e

export MAILMIND_DB_PATH="${MAILMIND_DB_PATH:-/data/mailmind.db}"
WEB_PORT="${WEB_PORT:-${STREAMLIT_PORT:-8501}}"
RUN_MODE="${MAILMIND_RUN_MODE:-watch}"

LITESTREAM_BUCKET="${LITESTREAM_S3_BUCKET:-}"
LITESTREAM_ENDPOINT="${LITESTREAM_S3_ENDPOINT:-}"
LITESTREAM_ACCESS_KEY_ID="${LITESTREAM_ACCESS_KEY_ID:-}"
LITESTREAM_SECRET_ACCESS_KEY="${LITESTREAM_SECRET_ACCESS_KEY:-}"

litestream_configured() {
  [ -n "$LITESTREAM_BUCKET" ] && [ -n "$LITESTREAM_ENDPOINT" ] && \
    [ -n "$LITESTREAM_ACCESS_KEY_ID" ] && [ -n "$LITESTREAM_SECRET_ACCESS_KEY" ]
}

log_litestream_missing() {
  # Be specific about WHICH var is missing so debugging is one log line.
  missing=""
  [ -z "$LITESTREAM_BUCKET" ]            && missing="$missing LITESTREAM_S3_BUCKET"
  [ -z "$LITESTREAM_ENDPOINT" ]          && missing="$missing LITESTREAM_S3_ENDPOINT"
  [ -z "$LITESTREAM_ACCESS_KEY_ID" ]     && missing="$missing LITESTREAM_ACCESS_KEY_ID"
  [ -z "$LITESTREAM_SECRET_ACCESS_KEY" ] && missing="$missing LITESTREAM_SECRET_ACCESS_KEY"
  echo "[fly-start] Litestream disabled — missing env var(s):${missing}. DB is local-only."
}

if [ "$RUN_MODE" = "external" ]; then
  echo "[fly-start] MAILMIND_RUN_MODE=external — the continuous watch loop is disabled; POST /internal/poll drives cycles instead."

  if litestream_configured; then
    echo "[fly-start] Litestream: all S3 env vars present (bucket=$LITESTREAM_BUCKET endpoint=$LITESTREAM_ENDPOINT)."
    echo "[fly-start] Restoring DB from S3 if a replica exists (won't overwrite existing local DB)..."
    litestream restore -config /etc/litestream.yml -if-replica-exists "$MAILMIND_DB_PATH" || true

    # No `run --watch` subprocess to -exec in this mode (the API process
    # itself is what's kept alive, below), so replication runs standalone in
    # the background instead of being litestream's -exec'd main process.
    echo "[fly-start] Starting Litestream replica in the background..."
    litestream replicate -config /etc/litestream.yml &
  else
    log_litestream_missing
  fi

  echo "[fly-start] Starting MailMind web app on :${WEB_PORT} (foreground — this is the container's main process in external mode)..."
  cd /app
  export PYTHONPATH="/app${PYTHONPATH:+:$PYTHONPATH}"
  exec uvicorn mailmind.api.main:app --host 0.0.0.0 --port "${WEB_PORT}"
fi

echo "[fly-start] Starting MailMind web app on :${WEB_PORT}..."
(
  cd /app
  export PYTHONPATH="/app${PYTHONPATH:+:$PYTHONPATH}"
  exec uvicorn mailmind.api.main:app --host 0.0.0.0 --port "${WEB_PORT}"
) &

if litestream_configured; then
  echo "[fly-start] Litestream: all S3 env vars present (bucket=$LITESTREAM_BUCKET endpoint=$LITESTREAM_ENDPOINT)."
  echo "[fly-start] Restoring DB from S3 if a replica exists (won't overwrite existing local DB)..."
  litestream restore -config /etc/litestream.yml -if-replica-exists "$MAILMIND_DB_PATH" || true

  echo "[fly-start] Starting Litestream replica + MailMind app..."
  exec litestream replicate \
    -config /etc/litestream.yml \
    -exec "python -m mailmind.main run --watch"
fi

log_litestream_missing
exec python -m mailmind.main run --watch
