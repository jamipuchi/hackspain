# Runtime checkpoint

The selected physical configuration uses 1 ms physics, 250 Hz inspection, and 500 requested objects/s.
The model artifact remains `3e0532d24da3a872ae931b43b9f048ceda5435e13bdaaf65087d54c37705bdbd` for this comparison.

Model inference was the largest measured cost: 2,140.7 ms per simulated second.
The selected optimization evaluates numeric trees together with NumPy indexing.
It preserves each tree decision and each class accumulation order.
The cache holds at most two classifiers outside serialized models.
Unsupported models and batches larger than 64 observations use the public scikit-learn predictor.
Native thread limits remain one. No inspection frame or requested object is removed.

The replay uses 1,000 actual camera batches with 6,001 observations.
All probabilities and anomaly scores match the original predictor bit for bit.
The check also covers empty batches, one-row batches, NaN routing, large batches, and unsupported classifiers.
Three prototype replays measured 8,601.4 ms versus 3,280.0 ms at the median, or 2.62x faster inference.
The integrated replay also passed numerical equivalence. Its timing overlaps another session and is excluded.

| Four-second engine run | Wall seconds | Inference ms/s | Capture count | Keep loss | Late decisions |
|---|---:|---:|---:|---:|---:|
| Before | 22.743 | 2140.7 | 156/190 | 50/1110 | 0 |
| First optimized run | 20.922 | 1025.4 | 153/190 | 49/1110 | 2 |
| Optimized repeat | 17.707 | 858.3 | 156/190 | 50/1110 | 0 |

The first optimized run had an 80 ms inference maximum and a 118 ms control-path maximum.
Two decisions became late near simulation time 1.66 seconds.
Other components also slowed in that run. The repeat resolves some timing uncertainty without discarding the first result.
The host had unrelated background CPU load. No competing coffee simulation ran, and no external process was stopped.

The repeat improves whole-engine wall time by 22.1% against the direct runtime baseline.
Its speed is 0.226 simulated seconds per wall second. This remains below real time.
Observed controller latency still affects scheduling. Equivalent predictions alone do not guarantee identical physical outcomes under host contention.

Taras reports approximately 16 vCPUs and 128 GB RAM for production.
These are local measurements. They do not establish production speed or parallel scaling.

## Reproduce

```bash
.venv-coffee/bin/python thoughts/taras/research/coffee-quality/measure_pulses.py --seconds 4 --out /tmp/coffee-runtime-inputs
.venv-coffee/bin/python thoughts/taras/research/coffee-quality/benchmark_inference.py --features /tmp/coffee-runtime-inputs/features.npz --repeats 3
.venv-coffee/bin/python sim/coffee_sorter/engine.py --preset sim/coffee_sorter/configs/default_demo.json --seconds 4 --out /tmp/coffee-runtime-engine
```

The original feature archive remains at `/tmp/coffee-quality-light-force/features.npz`.
Its SHA256 is `640cd67dd340a30c7837b3529bb44944d6c57b1b1dbde2f3668a53ec45543a18`.
Newly bootstrapped models require a separate comparison.

## Host timing audit

The service on port 8890 completed a user-triggered run while this task remained active.
File timestamps and reported active durations place that run approximately between 17:04:59 and 17:05:52 local time.
The integrated inference replay overlaps that window. Its timing is excluded, while numerical equivalence remains valid.
The three-run prototype benchmark preceded that window.
The engine baseline ran approximately from 17:03:04 to 17:03:27.
The first optimized engine run ran approximately from 17:05:56 to 17:06:17.
The optimized repeat ran approximately from 17:07:30 to 17:07:48.
Those engine comparisons do not overlap the observed service run.
The final bootstrap ran approximately from 17:09:44 to 17:10:10.
Reserved evaluation checks competing activity before each seed and during execution.
