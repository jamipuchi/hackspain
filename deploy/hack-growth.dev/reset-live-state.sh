#!/bin/sh
set -eu

ssh_bin=${SSH_BIN:-ssh}
host=${CINTA_SSH_HOST:-hackspain}
mode=${1:---dry-run}

case "$mode" in
  --dry-run|--apply)
    ;;
  *)
    echo 'Usage: reset-live-state.sh [--dry-run|--apply]' >&2
    exit 64
    ;;
esac

"$ssh_bin" "$host" sudo -n /bin/sh -s -- "$mode" <<'REMOTE'
set -eu

mode=$1

compose() {
  docker compose \
    --env-file /srv/compose/.env \
    --env-file /srv/hackspain-coffee/deploy/release.env \
    -f /srv/compose/docker-compose.yml \
    -f /srv/hackspain-coffee/deploy/compose.yml \
    "$@"
}

restart=1
restart_coffee() {
  if [ "$restart" -eq 1 ]; then
    compose up -d --no-deps coffee
  fi
}

helper=/app/deploy/hack-growth.dev/reset_live_state.py
item_root=/var/lib/hackspain-coffee/item-control
python_path=/app/sim/coffee_sorter:/app/deploy/hack-growth.dev

if [ "$mode" = --dry-run ]; then
  compose exec -T \
    -e "PYTHONPATH=$python_path" \
    coffee python "$helper" --item-control-root "$item_root" --dry-run
  exit 0
fi

compose exec -T \
  -e "PYTHONPATH=$python_path" \
  coffee python "$helper" --item-control-root "$item_root" --dry-run
compose stop coffee
trap restart_coffee EXIT
trap 'exit 1' HUP INT TERM
compose run --rm --no-deps \
  -e "PYTHONPATH=$python_path" \
  --entrypoint python \
  coffee "$helper" --item-control-root "$item_root" --apply
compose up -d --no-deps coffee
restart=0
trap - EXIT HUP INT TERM
REMOTE
