# Sensor realism results

Quickstart (from `sim/coffee_sorter`):

    /workspace/personal/venvs/coffee-sorter/bin/python -u run_sensor_realism.py all

These are matched synthetic physical runs with the frozen green classifier. All degradations labeled ASSUMED remain unverified on hardware; there is no real-time claim.

| scenario | effective /s | accuracy | defect recall (defects) | good false eject (keep) | ever merged | CPU overruns |
|---|---:|---:|---:|---:|---:|---:|
| base-1000 | 1000.0 | 88.7% | 48.7% (376) | 4.1% (2224) | 9.5% | 996/1000 |
| brightness-minus30-1000 | 1000.0 | 69.8% | 34.8% (376) | 22.8% (2224) | 11.7% | 995/1000 |
| brightness-plus30-1000 | 1000.0 | 76.4% | 47.6% (376) | 17.4% (2224) | 12.2% | 998/1000 |
| gradient-30-1000 | 1000.0 | 83.2% | 42.8% (376) | 9.0% (2224) | 10.4% | 999/1000 |
| exposure-nominal-100us-1000 | 1000.0 | 87.0% | 48.9% (376) | 6.0% (2224) | 11.8% | 987/1000 |
| exposure-stress-500us-1000 | 1000.0 | 77.7% | 44.7% (376) | 15.8% (2224) | 12.0% | 999/1000 |
| noise-nominal-1000 | 1000.0 | 87.4% | 52.9% (376) | 6.4% (2224) | 10.2% | 947/1000 |
| noise-stress-1000 | 1000.0 | 84.0% | 51.1% (376) | 9.8% (2224) | 10.4% | 933/1000 |
| belt-jitter-5pct-1000 | 1000.0 | 89.8% | 46.8% (376) | 2.6% (2224) | 7.3% | 998/1000 |
| high-feed-reference-3000 | 3000.0 | 80.7% | 38.8% (1206) | 8.6% (6594) | 31.7% | 998/1000 |
| touching-3000 | 1192.8 | 82.5% | 39.4% (454) | 9.6% (2653) | 38.5% | 997/1000 |
| combined-assumed-worst-3000 | 1201.3 | 68.6% | 31.4% (456) | 24.4% (2680) | 35.8% | 996/1000 |

Exposure is distinct from latency: assumed 100 us nominal and 500 us stress exposure only form the image (blur = `3 * seconds * 4000` pixels). The unchanged controller retains its existing 4 ms pipeline floor and every run uses a 60 ms minimum total latency and 0.06 N jets.

The late/miss/own-hit funnel and merged cohorts are in `summary.json` and each scenario's `metrics.json`. They are diagnostics, not an isolated causal attribution from final outcomes.

Config SHA-256: `c17fccac4f9f6da7b1cdcf4682b6892ae4b0d82297a26d78f6e4fff317e9cc1f`
Classifier SHA-256: `0b9e164cbf4df18f904f30fde38e0b7ed352877653849cc55da25e6f143051c8`

Exact per-scenario rerun commands are in `rerun_commands.json`.
