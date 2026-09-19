#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."
export MUJOCO_GL=osmesa
export LD_LIBRARY_PATH=/workspace/personal/coffee-gl/root/usr/lib/x86_64-linux-gnu
export LP_NUM_THREADS=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
physical_python=${COFFEE_PYTHON:-/workspace/personal/venvs/coffee-sorter/bin/python}
for physical_setting in trained:18.48719999197408 low:8.27 disabled:1e9; do
  physical_name=${physical_setting%%:*}
  physical_threshold=${physical_setting#*:}
  "$physical_python" -u openset.py physics --output "runs/generalization/physical-thresholds/$physical_name" --seed 41 --physics-seconds 8 --physics-rate 100 --fixed-latency-ms 60 --jet-force .06 --anomaly-threshold "$physical_threshold" --no-video > "runs/generalization/physical-thresholds/$physical_name.log" 2>&1
done
