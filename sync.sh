#!/bin/zsh
# Sync the robotics simulator code + learnings into this repo and push. Run by hand or in a loop.
set -e
REPO=~/hackspain
SRC=~/robotics
cd "$REPO"

# code (no run artifacts, no caches, no generated scene xml)
rsync -a --delete \
  --exclude '__pycache__' --exclude 'runs/' --exclude '*.raw.mp4' --exclude 'scene_generated*.xml' --exclude '.pixi' \
  "$SRC/magnet_sorter/" sim/magnet_sorter/
rsync -a --delete --exclude '__pycache__' --exclude 'runs/' "$SRC/astra_sort/" sim/astra_sort_v0/
rsync -a "$SRC/demos/" sim/demos/
cp "$SRC/README.md" sim/README-toolchain.md

# selected run artifacts: GPT-6 photos/plans and preview videos (small), never Cycles frame dumps
mkdir -p runs
for d in "$SRC"/magnet_sorter/runs/*/; do
  n=$(basename "$d")
  [ -f "$d/meta.json" ] || continue
  mkdir -p "runs/$n"
  cp -f "$d"/meta.json "$d"/events.json runs/$n/ 2>/dev/null || true
  cp -f "$d"/round*_plan.json "$d"/round*_plan.png "$d"/agent_*_mosaic.png runs/$n/ 2>/dev/null || true
  for v in "$d"/video_preview.mp4 "$d"/video_cycles.mp4; do
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
