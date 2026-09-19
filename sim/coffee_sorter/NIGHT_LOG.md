# Coffee sorter night log

One branch: `coffee-sorter-closed-loop`.
One [PR](https://github.com/tarasyarema/hackspain/pull/1).
Do not merge; Taras reviews in the morning.

## Task f2a7e430: first trained closed loop

### What ran

- Base: `cad5f9b`.
- Detector: 100 timed frames plus exact equivalence checks.
- Train: `train --profile green_arabica --seconds 24 --rate 900 --boost 5`.
- Run: `run --rate 2000 --seconds 8 --video`.
- Python 3.12, MuJoCo 3.13, headless OSMesa.
- Nine regression tests pass.

### Numbers

- Detector: 79.04 → 5.44 ms median.
- Exact feature and coordinate equality in ten cases.
- The <5 ms goal remains open.
- Training: 61,929 blob samples.
- Holdout accuracy: 97.91%; defect recall: 96.99%.
- Holdout shares repeated bean views; not independent-bean accuracy.
- Final run: 13,134 evaluated beans.
- Throughput: 1,983 simulated beans/s.
- Defect removal: 38.89%.
- Good-bean loss: 7.96%.
- Spills: 4.72%; pool-starved attempts: 122.
- Late reject decisions: 0.
- Latency p50/p99/max: 40.87 / 45.69 / 81.73 ms.
- Nominal budget: 73.33 ms. No worst-case timing guarantee.
- 8 simulated seconds took 284.22 wall seconds.

### Watch and inspect

- [Closed-loop MP4 with HUD](runs/20260919_015039_green_arabica_2000/overview.mp4).
- [Prediction strip 0](runs/20260919_015039_green_arabica_2000/inspection_0.png).
- [Prediction strip 3](runs/20260919_015039_green_arabica_2000/inspection_3.png).
- [Metrics](runs/20260919_015039_green_arabica_2000/metrics.json).
- [Controller decisions](runs/20260919_015039_green_arabica_2000/decisions.csv).
- These initial strips show predictions, not physical hit outcomes.
- Target-versus-hit visual recording is being added below.

### What surprised us

- A good classifier did not produce good physical sorting.
- 281 black beans were targeted; only 155 were hit by a jet.
- Of those 155 hits, 146 ended in rejection.
- Increasing force alone would not fix missed intersections.
- Early decisions initially created duplicate tracks and pulses.
- Keeping decided tracks associated fixed that regression.
- Transfer delay and scheduling were missing from latency accounting.
- Timing now adds modeled 4 ms capture/transfer to CPU work.
- Synthetic renderer wall time is not hardware-camera latency.

### Still broken

- Target-to-jet timing or coverage needs investigation.
- Collateral hits discard good beans.
- Shells, husks and stones spill heavily.
- Detector misses the 5 ms goal on this host.
- Force, pulse and splitter settings are unchanged pending evidence.
- Intermediate run `20260919_014250` had duplicate actuation.
  It is invalid as final validation.

### Overnight handoff

- Keep this branch and PR for subsequent tasks.
- Commit videos, annotated strips and metrics in each run directory.
- Add one task section here after each major result.
- Push each major result; never merge overnight.
- Rate sweep and roasted/open-set demos are queued separately.
