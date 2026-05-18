#!/bin/bash
# Run on Mac Mini to replicate ~/.mailmind/mailmind.db → S3.
# Runs litestream in the foreground; wrap in launchd for auto-start.
#
# Prerequisites:
#   brew install litestream
#   export LITESTREAM_ACCESS_KEY_ID=...
#   export LITESTREAM_SECRET_ACCESS_KEY=...
#   export LITESTREAM_S3_BUCKET=...
#   # Optional for Backblaze B2 / Tigris:
#   export LITESTREAM_S3_ENDPOINT=...

set -euo pipefail

exec litestream replicate \
  -config "$(dirname "$0")/litestream.yml"
