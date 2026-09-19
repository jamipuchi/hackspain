#!/bin/zsh
# Sync the robotics simulator code + learnings into this repo and push. Run by hand or in a loop.
setopt +o nomatch 2>/dev/null || true
set -e
setopt NULL_GLOB
REPO=~/hackspain
SRC=~/robotics
cd "$REPO"

# code (no run artifacts, no caches, no generated scene xml)
rsync -a --delete \
  --exclude '__pycache__' --exclude 'runs/' --exclude '*.raw.mp4' --exclude 'scene_generated*.xml' --exclude '.pixi' \
  "$SRC/magnet_sorter/" sim/magnet_sorter/
rsync -a --delete --exclude '__pycache__' --exclude 'runs/' "$SRC/astra_sort/" sim/astra_sort_v0/
rsync -a --exclude '__pycache__' --exclude 'avf_cameras' --exclude '*.jpg' "$SRC/demos/" sim/demos/
# coffee bean optical sorter (belt + camera + air jets); keep its preview renders, drop models/videos/run dirs
rsync -a --delete --exclude '.git' --exclude '__pycache__' --exclude 'runs/' --exclude 'models/' --exclude '*.mp4' --exclude 'MUJOCO_LOG.TXT' \
  "$SRC/coffee_sorter/" sim/coffee_sorter/
mkdir -p sim/coffee_sorter/runs/preview
cp -f "$SRC"/coffee_sorter/runs/preview/*.png sim/coffee_sorter/runs/preview/ 2>/dev/null || true
for d in "$SRC"/coffee_sorter/runs/*/; do
  n=$(basename "$d")
  [ -f "$d/metrics.json" ] || [ -f "$d/report.json" ] || [ -f "$d/bench.json" ] || continue
  mkdir -p "sim/coffee_sorter/runs/$n"
  cp -f "$d"/*.json "$d"/*.png "$d"/*.csv "sim/coffee_sorter/runs/$n"/ 2>/dev/null || true
  for v in "$d"/overview.mp4; do [ -f "$v" ] && [ $(stat -f %z "$v") -lt 40000000 ] && cp -f "$v" "sim/coffee_sorter/runs/$n"/ || true; done
done
# physical sorting line: control panel, contracts, per-subsystem modules and tests (no models/datasets)
rsync -a --delete --exclude '__pycache__' --exclude '.pytest_cache' --exclude 'models/' --exclude 'datasets/' "$SRC/line/" sim/line/
cp "$SRC/README.md" sim/README-toolchain.md

# selected run artifacts: GPT-6 photos/plans and preview videos (small), never Cycles frame dumps
mkdir -p runs
for d in "$SRC"/magnet_sorter/runs/*/; do
  n=$(basename "$d")
  [ -f "$d/meta.json" ] || continue
  mkdir -p "runs/$n"
  cp -f "$d"/meta.json "$d"/events.json runs/$n/ 2>/dev/null || true
  cp -f "$d"/round*_plan.json "$d"/round*_plan.png "$d"/agent_*_mosaic.png runs/$n/ 2>/dev/null || true
  for v in "$d"/video_preview.mp4 "$d"/video_mujoco.mp4 "$d"/video_cycles.mp4; do
    [ -f "$v" ] && [ $(stat -f %z "$v") -lt 40000000 ] && cp -f "$v" runs/$n/ || true
  done
done
cp -f "$SRC"/magnet_sorter/*.log logs/ 2>/dev/null || mkdir -p logs && cp -f "$SRC"/magnet_sorter/*.log logs/ 2>/dev/null || true

git add -A
if ! git diff --cached --quiet; then
  git commit -q -m "sync $(date '+%Y-%m-%d %H:%M') — sim code, learnings, run artifacts

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
  git push -q origin main
  echo "pushed $(date '+%H:%M:%S')"
else
  echo "nothing to push $(date '+%H:%M:%S')"
fi
