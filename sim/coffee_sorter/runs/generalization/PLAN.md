---
status: completed
base_commit: 511f104
branch: coffee-sorter-closed-loop
pr: https://github.com/tarasyarema/hackspain/pull/1
---

# Product transfer and unseen-object evaluation

User task: 4d057230-c3e0-4b4d-88e0-8f5b0dc0abf5. Autopilot, existing branch and PR, no merge.

## Phase 1: roasted product

Train the existing roasted profile with run.py train and run the same closed-loop metrics as green_arabica. Keep controller.py, vision.py, sim.py unchanged. Record any required shared-code change as a failed zero-change claim, not a hidden fix. Compare matched operating settings and independent evaluation seeds. Training holdout confusion matrices must disclose repeated bean views if applicable.

- [x] Save training/model provenance and comparable physical metrics with denominators.
- [x] Produce side-by-side confusion matrices and labelled camera-strip examples for every known class.
- [x] Verify protected source hashes, review both axes, commit and push result.

## Phase 2: open set

Test at least a novel foreign material, odd colour and wrong-size object absent from training. Sweep good-class Mahalanobis threshold on fixed held-out observations and distinguish anomaly-only decisions, combined controller decisions and actual reject-bin outcomes. Include good-bean false rejection cost, missed detections and repeated-observation limitations. Do not select a threshold on the final holdout without disclosure.

- [x] Save reproducible harness, raw scores, threshold sweep, denominators and model/source hashes.
- [x] Compare physical outcomes at the trained threshold, an exploratory lower threshold and anomaly disabled, using a separate seed from the offline sweep.
- [x] Save tradeoff plot and H.264 video showing a measured unseen-object ejection, with score HUD and ground-truth distinction.
- [x] Add targeted tests, perform visual/video QA and both review axes.
- [x] Upload viewable copies via agent-fs, link in NIGHT_LOG.md, update same PR, push.

## Carryover

The preceding completed task left final documentation/plot readability edits and ignored tuning/confirmation artifacts in this worktree. The preceding session finalized them in commit 511f104; independent read-only verification passed all 26 tests, all 13 artifact sets and complete video decode. Do not restore unrelated main-branch stashes.

## Acceptance

All repository tests and syntax/diff checks pass. PR body contains actual test output. No claim of real-world generalization or hardware real-time performance from synthetic simulation alone. Human morning review remains pending.
