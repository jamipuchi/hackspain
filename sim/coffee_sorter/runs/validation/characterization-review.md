# Phase 1 review against f5bead0

## Standards

PASS after fixes: per-rate benchmark output names, applicable CLI flags only,
queued-versus-activated labels, redundant tracking state removed, and focused tests.

## Spec

PASS after fixes: physical accuracy/precision/recall/false-eject denominators,
spills and unresolved counted as errors, single/merged/unseen cohorts,
activated pulses and own-pulse attribution, annotated fired evidence.
Baseline completed 7.999999999999341 simulated seconds and 2000 camera frames.

## Validation

22 regression tests pass. Exact detector equivalence passes all ten contexts.
Detector timing target remains unmet; no performance threshold was relaxed.
Delivered H.264 video: 400 frames, 25 fps, 16 seconds playback, 1280x720.
Middle and final frames decode; six annotated camera sheets are present.
