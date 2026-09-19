---
status: in-progress
task: b4d760bf-03a6-4991-b961-e7dd2c12a915
---

# Sorter characterization

Continue `coffee-sorter-closed-loop`, draft PR #1. Autonomous execution,
commit and push each major result. Keep the PR unmerged for morning review.

## Phase 1: observable experiments

- [x] Record rate-sweep physical rejection accuracy, precision, recall,
  good-bean false ejects, late decisions, spills, starvation and wall/sim time.
- [x] Add controlled latency sweep and show misses against budget headroom.
- [x] Measure single and merged camera blobs separately, with denominators.
- [x] Save annotated camera strips for each numerical run and HUD video.
- [x] Verify instrumentation and run regression tests.
- [x] Standards and Spec reviews; push instrumentation and first result.

## Phase 2: characterize and tune

- [x] Run 500, 1000, 2000 and 3000 beans/s; extend only if capacity holds.
- [x] Sweep induced latency across the camera-to-jet deadline.
- [ ] Compare force, pulse, splitter, nozzle coverage and pool settings.
- [ ] Preserve before/after metrics and plots; justify any default change.
- [ ] Review results, run checks and push the completed evidence.

## Phase 3: morning handoff

- [ ] Upload visuals with agent-fs and verify retrievability.
- [ ] Append NIGHT_LOG.md with numbers, links, surprises and limitations.
- [ ] Update README Progress log and Next list; update existing PR body.
- [ ] Final check that the PR remains open, draft and unmerged.
- [ ] Complete the swarm task with headline results and artifact links.

Human visual acceptance remains for Taras in the morning. Passing synthetic
experiments does not establish physical-camera accuracy or real-time execution.
