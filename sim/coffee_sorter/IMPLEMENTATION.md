---
status: completed
---

# First closed-loop implementation

Base: `cad5f9b`. Scope: coffee sorter only. Review in a PR to
`tarasyarema/hackspain`, without merging.

## Phase 1: Detector

- [x] Cache pixel coordinates and calculate blob statistics on foreground pixels.
- [x] Prove unchanged blob features against the original implementation.
- [x] Measure before/after detector timings: 79.038 → 5.443 ms p50; exact equality in ten contexts.
- [ ] Reach below 5 ms per frame (current p95 5.635 ms; target honestly missed).

## Phase 2: Classifier

- [x] Establish a local uv environment with MuJoCo 3.13 and headless rendering.
- [x] Run `run.py train --profile green_arabica --seconds 24 --rate 900 --boost 5`.
- [x] Preserve the model, training report, and confusion plot in the first-run archive.

## Phase 3: Closed loop

- [x] Fix demonstrated metrics, overview rendering, duplicate-actuation, and latency-accounting bugs; nine tests pass.
- [x] Run `run.py run --rate 2000 --seconds 8 --video`.
- [x] Inspect physical rejection versus targeting/jet hits, late decisions, and spills; final run has zero late decisions but poor sorting quality.
- [x] Preserve metrics and video, and document limitations and evidence against blind actuator tuning.
- [x] Independent standards and spec review pass; prepare PR evidence with actual command output.

No rate sweep, roasted profile, robot arm, or changes to the magnet sorter.

Delivery includes known open goals: detector p50 is 5.443 ms (target <5 ms);
physical sorting needs further work (38.89% removal, 7.96% good loss, 4.72% spills).
Final run: `20260919_015039_green_arabica_2000`, zero late decisions;
modeled latency p50/p99/max 40.87/45.69/81.73 ms, not a hard real-time guarantee.
