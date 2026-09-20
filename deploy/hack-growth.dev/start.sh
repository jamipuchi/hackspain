#!/bin/sh
set -eu

: "${CINTA_PUBLIC_HOST:?CINTA_PUBLIC_HOST is required}"
: "${CINTA_PUBLIC_ORIGIN:?CINTA_PUBLIC_ORIGIN is required}"

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
