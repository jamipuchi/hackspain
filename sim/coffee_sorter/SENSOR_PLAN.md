---
status: completed
task: 29f7117e-b13a-481c-adcd-8a249331141a
---

# Sensor realism and economics

Continue coffee-sorter-closed-loop and draft fork PR #1. Autonomous execution;
no blocking questions, no merge, commit and push useful milestones.

## Phase 1: sensor realism

- [x] Frozen green model; brightness ±30%, horizontal gradient, exposure-linked
  motion blur, shot/read noise, physical ±5% belt jitter, crowded feed.
- [x] Matched one-factor physical experiments and combined assumed worst case;
  denominators, camera versus actuation diagnostics, plots and camera frames.
- [x] Exposure, noise and jitter frequency explicitly assumptions until measured.
- [x] Tests and separate Standards / Spec reviews before pushing.

## Phase 2: economics

- [x] Rerunnable mass/value ledger using effective rates and observed physical
  recall and good losses from the rate sweep; show spills separately.
- [x] Explicit mass, duty cycle, grade-related value assumptions; break-even
  threshold, no real hardware throughput or market-price claim.
- [x] Add sensor scenario ledger after the sensor runs finish.
- [x] Tests and review; README Progress/Next and NIGHT_LOG; update PR and push.

Human validation: measure exposure, noise, illumination and belt jitter on hardware;
obtain actual buyer prices and lot-grade acceptance rules. These remain unverified.
