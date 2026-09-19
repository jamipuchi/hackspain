---
date: 2026-09-19
reviewer: Claude
topic: Review of the coffee interactive learning demo plan
status: complete
reviewed_document: thoughts/taras/plans/2026-09-19-coffee-interactive-learning-demo.md
reviewed_branch: codex/coffee-sorter-upstream
reviewed_revision: 86a7a36
---

# Review: coffee interactive learning demo plan

Taras, this review checks the [plan](../plans/2026-09-19-coffee-interactive-learning-demo.md) against the code and evidence at `86a7a36`.
It does not rewrite the plan. It adds no tests and no QA tooling. You own acceptance.

Paths below are relative to `sim/coffee_sorter/` unless they start with `thoughts/`.

## Verdict

The plan is honest about what is proposed and what is measured. Its boundaries section is strong.
Three problems block it as an implementation base:

1. The capture goal has no path in the phases. Recorded evidence says the classifier is not the bottleneck.
2. The real-time goal has no work item. Phase 1 can measure a configuration that the live page cannot run.
3. The Jev spike case cannot detect a wrong interpretation. Its "pass" result carries about one bit of information.

Counts: 3 Critical, 8 Important, 7 Minor.

## Scope and method

- Read the full plan, the Jev spike evidence, and both spike scripts.
- Checked each `file:line` reference in the plan against the source.
- Compared the goals with `MORNING_REVIEW.md`, `README.md`, and `NIGHT_LOG.md`.
- Inspected `web/replay.json`, `export_replay.py`, `controller.py`, `classifier.py`, `profiles.py`, and the web build.
- Did not open an SSH session. Host facts in the Deployment section are unverified here.
- Did not verify the three swarm task links. The agent-swarm MCP server failed to connect in this session.
- Did not run the simulator, the tests, or any Jev request.

## Critical

### C1. The 80% capture goal has no path in Phase 1

Plan lines 114 to 122 set 80% capture and 2% good loss at 500 beans/s.
The rate sweep already measured 500 beans/s: 61.6% capture and 2.9% good loss (`MORNING_REVIEW.md:70`).
The goal exceeds the best recorded point on both axes.

The recorded diagnosis names the cause (`MORNING_REVIEW.md:87` to `92`):

- "The classifier is not the problem on a clean camera; jet timing and coverage are."
- Ever-merged beans: 26.2% capture. Single beans: 47.0%.
- Neighbour jets knock 4 to 10% of good beans into reject.
- Classifier blob holdout is already 97.9%.

Phase 1 items 1 and 2 repair the classifier split. That work makes the accuracy figure honest. It will probably lower it.
It does not move physical capture. Item 3 sweeps feed rate, jet force, and nozzle controls.
The overnight tuning screen already covered force, pulse, splitter, and valve coverage. Its best result was 51.5%.

Missing: a loss attribution step before the sweep. Each missed defect needs one cause:
not detected, merged, misclassified, targeted but not hit, or hit but not in reject.
`metrics.json` already separates targeted, jet-hit, and rejected counts (`README.md:375`). The data exists.

Recommended action: make the attribution table the first Phase 1 output. Choose sweep dimensions from it.
State in the plan which mechanism is expected to close the 18-point gap. If none is known, say that and call 80% a stretch.

### C2. The real-time goal has no work item, and it can invalidate Phase 1

Plan line 120 sets a real-time factor of at least 1.0. Recorded speed is 13 to 37 wall seconds per simulated second.
Physics alone ran at 1.5 to 3 wall seconds per simulated second at 2,000 beans/s (`README.md:135`).
The camera runs at 250 Hz (`run.py:219`). Render costs 5.6 ms and detection 5.44 ms per 4 ms frame interval.
Detector plus controller overran the frame interval on 996 of 1,000 frames (`MORNING_REVIEW.md:95`).
The same line concludes: "A live viewer demo is not an option on this host."
The plan does not cite or answer that conclusion.

One simulation is sequential. The 32 vCPUs on the box do not help it. The plan says this at line 128.
The box renders with OSMesa on CPU (`README.md:62`). The inspection camera may be slower there than on the Mac.

The larger problem is coupling. Reaching real time probably needs a lower camera rate, a lower feed rate, or a smaller body pool.
Each of those changes capture. Phase 1 selects `default_demo.json` offline at 250 Hz.
Phase 2 may then run a different configuration to stay responsive. The Phase 1 numbers would not describe the live demo.

Phase 2 has an honest fallback: label the actual simulation rate. At 13 to 37 times slower, that fallback is the likely outcome.
A bean resolves about 0.46 simulated seconds after spawn. One injected stone then takes roughly 6 to 17 wall seconds.

Recommended action: add an explicit profiling and speed work item with a per-component wall-time budget.
Require that Phase 1 evaluates the exact engine configuration that Phase 2 runs. Measure speed on the box, not only on the Mac.
Decide the rule now: if real time is not reachable, does the demo slow the clock or reduce the camera rate?

### C3. The Jev spike case cannot detect a wrong interpretation

The refined instruction is "Keep faded beans, but reject black beans and foreign material."
The spike state sets `current_action: reject` for every defect class (`refined_policy_spike.py:28` to `39`).
Only `faded` differs from the default. Four of five expected actions equal the current policy.

Jev answered `unchanged` for `black` with p(reject) = 0.23 in English and 0.28 in Spanish.
It answered `unchanged` for husk, stone, and stick as well.
The compile step turned `unchanged` into `reject`, so the effective policy matched.
A model that ignored the whole reject clause would also pass.

Plan line 263 reports that both requests "produced the requested effective policy." That is accurate and weak.
The contract has a real defect. `unchanged` and `reject` have the same effect when the current action is reject.
Two options with one meaning split the probability mass and lower confidence.
If the current policy kept black beans, this same answer would compile to the wrong policy.

Recommended action: before another batch, define `unchanged` as "the instruction does not mention this class."
Add cases where the instruction opposes the current policy on the reject side. Example: start from a policy that keeps black.
Score the raw action for every explicitly named class, not only the compiled policy.

## Important

### I1. Metric definitions and the pass rule are undefined

- The target counts good beans "wrongly rejected or spilled." The recorded 4.26% counts false ejects only.
  Spills were 2.12% of eligible beans in the same run (`README.md` matched-run table). No baseline exists under the target's definition.
- The plan does not define the denominator. The README keeps spills and unresolved beans in it. State the same rule.
- No pass rule exists. At 2,000 objects and about 14% defects, one seed gives about 280 defects.
  The 95% half-width is about 4.7 points at 80% capture, and about 0.7 points at 2% good loss.
  State whether the point estimate or the lower bound must reach 80%.
- Recorded seed spread is 48.5, 48.7, and 51.5% for the same settings. Three seeds is a minimum, not a margin.
- Lighting is the first demo risk in `MORNING_REVIEW.md`. The plan reports degraded lighting separately but sets no limit for it.

### I2. The ambiguity gate acts on near-coin-flip answers

| Case | p(ambiguous) | Confidence | Other weak answers |
|---|---:|---:|---|
| English | 0.65 | 0.30 | capability supported 0.81 |
| Spanish | 0.54 | 0.07 | intent policy_change 0.51 vs ambiguous 0.47, capability supported 0.67 |

The gate uses the top choice (`refined_policy_spike.py:223`). A 0.54 answer with 0.07 confidence is noise, not a finding.
The plan says Jev "incorrectly marked" the instruction as ambiguous. It omits these margins.
The Spanish request was one small shift from failure on three separate gates.

Two questions overlap. `intent` has an `ambiguous` choice, and `ambiguity` is a separate question. The plan does not say which one governs.

The plan's hypothesis is plausible and cheap to test. The state shows `faded: current_action: reject`, and the instruction says keep faded.
Three control requests would separate the causes:

1. An instruction that equals the current policy.
2. The same instruction with `current_action` removed from the state given to the ambiguity question.
3. The same request repeated, to measure answer stability.

Phase 3 has no acceptance numbers: no case count, no pass rule, no stability requirement, no probability threshold for the gate.
I agree with the plan on one point: do not remove the gate to make two cases pass.

### I3. The controller policy is a severity set, not per-class actions

`Policy.reject_severities` is a tuple of severities (`controller.py:20`). The reject mask derives from it (`controller.py:82`).
Phase 3 asks Jev for per-class keep or reject. The current policy type cannot express "keep broken, reject shell."
Phase 3 lists no controller change and no versioned policy schema. Both are required.

The example instruction equals the existing `COMMERCIAL` preset (`controller.py:33`). `faded` is the only minor class (`profiles.py:51`).
The demo would show language doing what a preset toggle already does. Choose a hero instruction that no preset covers.

The anomaly path can still reject a kept class (`controller.py:162`). Faded colour differs from the good cloud.
Verify that "keep faded" physically keeps faded beans. Otherwise the instruction has no visible effect.
Faded prior is 3%. At 500 beans/s that is about 15 beans per second. Plan the demo around injected faded beans.

### I4. Parallel tracks share files that no track owns

The track table assigns classifier, live service, web, and rendering files. These files have no owner, and several tracks need them:

| File | Needed by |
|---|---|
| `vision.py` (crops) | Phase 1 item 1, Phase 2 inspector, Phase 4 `object_examples.py` |
| `sim.py` (spawn) | Phase 2 injection |
| `controller.py` | Phase 1 nozzle sweep, Phase 3 per-class policy |
| `export_replay.py` | Phase 5 capture rate, Phase 2 pose shapes |
| `web/viewer.js` | Phase 2 live page, Phase 5 browser asset sample |

`web/index.html` is a committed 1.8 MB build output. Two tracks that rebuild it will conflict on every merge.
Name one track that commits build output, or build only at deploy.

Line 56 says to agree on IDs, timestamps, policy versions, and appearance keys "before integrating tracks."
That is too late. Tracks need the contract before they start. The plan names no file and no owner for it.
Crop extraction is the clearest case. Three phases need it, so one track should deliver it first.

### I5. "Can start now" hides two dependencies for the live page

- The Phase 2 command needs `configs/default_demo.json`. Phase 1 produces that file. The live track needs a stub preset on day one.
- Trained models are ignored by Git (`.gitignore:10`, `README.md:374`). This checkout has no `models/` directory. `live.py` on the box needs a model file.
  The Manual E2E has no train or restore step. It will fail at startup as written.
- The live track also depends on engine speed (C2). The plan lists only the classifier as a non-dependency.

### I6. Deployment is a section, not a phase

- No phase owns deployment. The Manual E2E refers to "the deployment task." No such task exists in the plan.
- The section has no verification commands, no owner, and no rollback. Your plan rules require a Verification section per phase.
- Two topologies coexist. Phase 2 has `live.py` serve the page. The Deployment section puts the page on Vercel.
  Choose one for the first public demo. The same-origin option needs no CORS work and no Vercel project.
- The web build is `python3 build_page.py`. It inlines assets into `index.html`. The plan does not say how the API URL enters a Vercel build.
- Process supervision, the Caddy route, and the restart procedure are absent.
- "Session limits" has no number. Each session is one CPU-bound process that already runs slower than real time.
  A public URL needs a hard cap and a visible "busy" state. A single-digit cap is my suggestion.
- The access policy is listed as an open decision. It affects the WebSocket handshake. Decide it before the Phase 2 protocol is designed.
- The 24 vCPU bulk budget and live sessions compete with Cycles renders on the same host. The plan notes priority but no mechanism.

### I7. Cinematic assets: one false statement and no film specification

- Line 354 says the exporter accepts `--fps` for higher capture rates. `export_replay.py:35` rejects any value above 60.
  The 120 Hz capture needs a code change. The 2 ms physics timestep caps capture at 500 Hz.
- The asset list covers good, black, insect, and broken: 4 of 10 classes. The replay contains all 10.
  `faded` is the subject of the language demo. `stone` is the subject of the injection checkpoint. Neither has an asset.
  State what the film shows for the other six classes.
- No film specification exists: duration, resolution, frame rate, shot list, render-time budget per frame, or deadline.
  "Expand this into the cinematic demo" is unbounded. This is the largest scope risk in the plan.
- Physics bodies are ellipsoids, boxes, and capsules. Detailed meshes on primitive colliders float or interpenetrate in close-ups.
  The plan requires alignment. Make one of the three first stills a belt-contact close-up to expose this early.
- Air jets are invisible. The film needs a decision on how to show a jet without showing a better result than the engine produced.
- No storage decision exists for `coffee_demo.blend` and baked textures. State Git, LFS, or agent-fs, with a size limit.
- Branch `codex/coffee-realistic-assets` is not on `origin` or `upstream` as fetched here. It may be unpushed. I could not verify the task status.

### I8. Phase 4 is over-built for this demo

Apply the deletion test to each item:

- Item 4, the vision-model suggestion queue, serves no stated demo moment. Delete it, and the demo story is unchanged.
- The separate binary-correction policy learner is a second learning system. The plan itself marks it as "evaluate before integrating."
- Activation, rollback, and regression limits are correct in principle. For a first increment, "train a candidate and show the comparison" is enough.

Recommended action: reduce Phase 4 to gallery, PCA map, class corrections, and one candidate comparison. Move the rest to an appendix.

## Minor

- M1. Frontmatter `source_revision` is `3c620dc`. HEAD is `86a7a36`. The later commits change documents only.
- M2. The anomaly statistics reference `classifier.py:106` is one line early. The line is 107. All other references are accurate.
- M3. The plan reports 72 simulator tests and five replay checks at `4dbc350`. The current tree has 72 simulator and 6 replay test functions.
- M4. The 250 ms acknowledgment target names no measurement point. A tunnel, a LAN, and Vercel-to-box give different numbers.
- M5. The 30 FPS target names no object count. The shipped replay holds 4,000 beans.
- M6. `web/replay.json` records a `vision.py` hash from revision `340e734`. The current file differs after the upstream merge. Label the first stills with that revision.
- M7. Template sections are absent: Desired End State, What We're NOT Doing, Quick Verification Reference, Appendix.
  "Boundaries" covers part of that. Success criteria use two buckets, not three. That matches your working agreement, so I raise no objection.

## Verified as accurate

- 23 features in `vision.py:15`. Gradient-boosted trees and Mahalanobis distance in `classifier.py:32` and `:95`.
- `collect()` discards object IDs (`classifier.py:85`). The split is row-level with automatic early stopping (`classifier.py:99` to `101`).
- Anomaly statistics use every good observation, including test rows (`classifier.py:107`).
- `component_members()` exists and `openset.py:156` uses it as described.
- The replay holds truth classes and final outcomes per bean. The plan's warning about live exposure is correct.
- 51.5% capture and 4.3% good loss match `runs/confirmation-seed1` as cited in `MORNING_REVIEW.md:57`.
- 13 to 37 wall seconds per simulated second matches `NIGHT_LOG.md:112` and `:490`.
- Jev latencies, token counts, model version, and the two deferred proposals match `results.md` and `refined_results.json`.
- `aiohttp` is absent from `requirements.txt`. scikit-learn 1.9.1 is present, so PCA and grouped splits add no dependency.
- No `rendering/`, `live.py`, `jev_policy.py`, `learning.py`, or `evaluate_default.py` exists. The plan says so correctly.

## Questions for Taras

1. If 80% capture is not reachable at 500 beans/s, which gives way first: the capture goal, the feed rate, or the good-loss limit?
2. If real time is not reachable, do you prefer a slowed clock with full camera rate, or real time with a reduced camera rate?
3. Is the first public demo same-origin on the box, or Vercel plus an API subdomain?
4. What is the film: duration, resolution, and deadline?
5. Which instruction should be the language hero, given that "keep faded" equals the commercial preset?
