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
item_control_root=${CINTA_ITEM_CONTROL_ROOT:-/var/lib/hackspain-coffee/item-control}
provider_cache_root=${CINTA_PROVIDER_CACHE_ROOT:-$item_control_root/provider-cache}
provider_mode=${CINTA_ITEM_JOBS_PROVIDER:-cached}
provider_env=${CINTA_PROVIDER_ENV:-}
physics_replay_root=${CINTA_PHYSICS_REPLAY_ROOT:-/run/cinta/provider-replay}
runtime_root=${CINTA_RUNTIME_ROOT:-/tmp/cinta-runtime}
port=${CINTA_INTERNAL_PORT:-8890}
mkdir -p "$runs_root" "$item_control_root" "$runtime_root" \
  "${HOME:-/tmp/cinta-home}" \
  "${XDG_CACHE_HOME:-/tmp/cinta-home/cache}" \
  "${XDG_CONFIG_HOME:-/tmp/cinta-home/config}" \
  "${XDG_STATE_HOME:-/tmp/cinta-home/state}"
if [ ! -d "$provider_cache_root/cache" ]; then
  echo 'The provider cache root has no cache directory.' >&2
  exit 64
fi
if [ "$provider_mode" = paid ] && [ ! -f "$provider_env" ]; then
  echo 'Paid provider mode requires a mounted credential file.' >&2
  exit 64
fi
if [ ! -d "$physics_replay_root" ]; then
  echo 'The reviewed physics replay directory is unavailable.' >&2
  exit 64
fi
out=$(mktemp -d "$runs_root/$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")

set -- python /app/sim/coffee_sorter/live.py \
  --host 0.0.0.0 \
  --port "$port" \
  --allowed-host "$CINTA_PUBLIC_HOST" \
  --allowed-origin "$CINTA_PUBLIC_ORIGIN" \
  --preset /app/sim/coffee_sorter/configs/continuous_demo.json \
  --item-jobs-root "$item_control_root" \
  --item-jobs-provider "$provider_mode" \
  --item-jobs-provider-cache "$provider_cache_root" \
  --item-jobs-physics-replay "$physics_replay_root" \
  --item-jobs-generator-root /app/sim/coffee_sorter/generator \
  --item-jobs-runtime-lock "$runtime_root/render.lock" \
  --out "$out"
if [ "$provider_mode" = paid ]; then
  set -- "$@" --item-jobs-provider-env "$provider_env"
fi
exec "$@"
