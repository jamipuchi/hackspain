#!/bin/zsh
# Sync the robotics simulator code + learnings into this repo and push. Run by hand or in a loop.
setopt +o nomatch 2>/dev/null || true
set -e
setopt NULL_GLOB
REPO=~/hackspain
SRC=~/robotics
cd "$REPO"

# GUARD (19 Sep 19:55): origin/main now carries Taras's coffee sorter under sim/coffee_sorter — the SAME path this
# script mirrors ~/robotics/coffee_sorter into with. If this clone is ever behind origin, syncing would
# overwrite/delete his files and push that. Refuse to run until a human has rebased and re-pointed the coffee rsync.
git fetch -q origin main 2>/dev/null || true
behind=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo 0)
if [ "${behind:-0}" -gt 0 ]; then
  echo "NOT SYNCING $(date '+%H:%M:%S'): local main is $behind commits behind origin/main (Taras's PR #3). See INTEGRATOR.md 19:55."
  exit 0
fi

# code (no run artifacts, no caches, no generated scene xml)
rsync -a \
  --exclude '__pycache__' --exclude 'runs/' --exclude '*.raw.mp4' --exclude 'scene_generated*.xml' --exclude '.pixi' \
  "$SRC/magnet_sorter/" sim/magnet_sorter/
rsync -a --exclude '__pycache__' --exclude 'runs/' "$SRC/astra_sort/" sim/astra_sort_v0/
rsync -a --exclude '__pycache__' --exclude 'avf_cameras' --exclude '*.jpg' "$SRC/demos/" sim/demos/
# NOTE 19 Sep 19:55: the ~/robotics/coffee_sorter mirror was removed from this script. sim/coffee_sorter on origin/main is
# Taras's fork of it (PR #3, merged 19:44) and is now the canonical version; an rsync from the older local copy would erase it.
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
