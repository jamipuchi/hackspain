#!/usr/bin/env bash
set -euo pipefail
cd /home/worker/.worktrees/hackspain/2026-09-19-coffee-sorter-closed-loop/sim/coffee_sorter
PY=/workspace/personal/venvs/coffee-sorter/bin/python
"$PY" -u run.py run --rate 1000 --seconds 4 --seed 1 --fixed-controller-latency-ms 60 --name confirmation-seed1/physics-base > runs/confirmation-seed1/physics-base.log 2>&1
"$PY" -u run.py run --rate 1000 --seconds 4 --seed 1 --fixed-controller-latency-ms 60 --jet-force 0.06 --video --name confirmation-seed1/force-0.06 > runs/confirmation-seed1/force-0.06.log 2>&1
