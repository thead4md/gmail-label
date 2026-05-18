#!/bin/bash
# Run on Mac Mini to replicate ~/.mailmind/mailmind.db -> S3.
# Uses litestream-mac.yml (hardcoded path, no shell expansion issues).
#
# Prerequisites:
#   brew install litestream
#   export LITESTREAM_ACCESS_KEY_ID=...
#   export LITESTREAM_SECRET_ACCESS_KEY=...
#   export LITESTREAM_S3_BUCKET=...
#   export LITESTREAM_S3_ENDPOINT=...   # e.g. s3.us-west-004.backblazeb2.com

set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
exec litestream replicate -config "$DIR/litestream-mac.yml"
