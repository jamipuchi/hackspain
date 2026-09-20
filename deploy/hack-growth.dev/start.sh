#!/bin/sh
set -eu

: "${CINTA_PUBLIC_HOST:?CINTA_PUBLIC_HOST is required}"
: "${CINTA_PUBLIC_ORIGIN:?CINTA_PUBLIC_ORIGIN is required}"
: "${CINTA_SOURCE_REVISION:?CINTA_SOURCE_REVISION is required}"

image_revision=$(cat /app/source-revision.txt)
if [ "$CINTA_SOURCE_REVISION" != "$image_revision" ]; then
  echo 'CINTA_SOURCE_REVISION does not match the image release.' >&2
  exit 64
fi

runs_root=${CINTA_RUNS_ROOT:-/var/lib/hackspain-coffee/runs}
port=${CINTA_INTERNAL_PORT:-8890}
mkdir -p "$runs_root"
out=$(mktemp -d "$runs_root/$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")

exec python /app/sim/coffee_sorter/live.py \
  --host 0.0.0.0 \
  --port "$port" \
  --allowed-host "$CINTA_PUBLIC_HOST" \
  --allowed-origin "$CINTA_PUBLIC_ORIGIN" \
  --preset /app/sim/coffee_sorter/configs/continuous_demo.json \
  --out "$out"
