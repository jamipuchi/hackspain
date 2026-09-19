---
status: in-progress
task: 1647255c-27bd-4428-a124-c37fab65f2fe
branch: coffee-sorter-closed-loop
---

# UR5e infeed picking

Implement the remaining README item in the existing coffee-sorter harness.
Stop experiments at 07:15 UTC on 19 September 2026. Keep draft fork PR #1
unmerged. Existing morning-review text is immutable; append only the requested
5–10 line addendum. No Slack messages.

## Phase 1: bounded pick cell and evidence

- [x] Reuse Menagerie UR5e and mink with the existing simulation conventions.
- [x] Select oversize debris on an explicitly configured infeed.
- [x] Execute reach/pick/reject motions and record all outcome denominators.
- [x] Separate observed kinematics from assumed grasp/perception behavior.
- [x] Run nominal and adverse cases; preserve failures, inputs, events and metrics.
- [x] Save readable plots/frames and, if available, a short video in `runs/ur5e-infeed/`.
- [x] Record dependencies, external assets/provenance and reproduction commands.

Success means a reproducible quantified result, including a partial/inconclusive
result if physical grasp or moving-target capture cannot be established in time.
Ideal attachment, oracle poses, stopped feed, collision exclusions and lack of
actuator dynamics must be stated wherever they limit a reported success count.

## Phase 2: review, documentation and delivery

- [x] Run meaningful selection/outcome tests and the existing complete test suite.
- [x] Independent Standards and Spec reviews; resolve material findings.
- [x] Update README and append NIGHT_LOG result, assumptions and links.
- [x] Verify original MORNING_REVIEW bytes are preserved, append addendum only.
- [ ] Commit and push on the existing branch; refresh PR #1 test output.
- [ ] Check remote commit and CI status; store task result with artifact links.

Human hardware validation and morning PR acceptance remain pending.
