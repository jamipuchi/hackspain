---
date: 2026-09-19
planner: Codex
topic: Coffee sorting quality and the first live engine demonstration
status: in-progress
next_increment: ui-restart-in-progress
updated: 2026-09-19
owner: taras
initial_source_branch: codex/coffee-sorter-upstream
initial_source_revision: 4d0f705
current_implementation_revision: 10d0975
implementation_branch: codex/coffee-core-live
---

# Coffee sorter implementation

Taras owns functional QA and acceptance. This task owns the plan, live interface, service, and separate language-policy work.
The parallel quality task owns physical motion, sorting quality, and engine runtime. The swarm owns rendering assets.

This revision addresses [Claude's review](../reviews/2026-09-19-review-of-coffee-interactive-learning-demo.md).
The first bounded increment is complete. Taras requested continued progress while the parallel task improves the engine.
Taras aligned on continued demo work and added the interaction requirements below.
Each visible increment keeps its own feedback checkpoint.

## Current checkpoint

The first increment from Phases 1 and 2 is implemented and pushed. Taras's functional acceptance remains pending.
The [checkpoint report](../research/coffee-core-live/REPORT.md) records measurements and evidence from implementation revision `51181c3`.
The [run guide](../../../sim/coffee_sorter/LIVE.md) provides installation, startup, and reset commands.
The final live run achieved 52.82% capture, 6.45% good loss, and 0.189 simulated seconds per wall second.
These results miss all three targets. The injected stone received rejection commands but spilled without jet contact.
The selected 400-body pool remains provisional. Existing UI ownership remains unconfirmed, so overlapping files remain unchanged.
Engine quality does not block independent interface work, render integration preparation, or the offline language contract.
Live policy activation still requires the physical-quality checkpoint and Taras's acceptance.

Taras then reported dropping and bouncing in the live demonstration.
Commit `91dedc3` corrects capsule spawn height. The [motion follow-up](../research/2026-09-19-coffee-motion-followup.md) records the bounded measurements and remaining uncertainty.
The model was rebuilt for that source change. No full quality evaluation ran afterward.
The figures above remain historical baseline measurements. Overall smoothness and sorting targets remain unaccepted.

## Next steps after alignment

1. Add **Restart session** to the page (Phase 2A). Stop the old worker before starting a fresh session.
2. Add class selection and a lasting result card for every injected object (Phase 2B).
   Clearly distinguish the camera prediction, controller action, actual air contact, and physical outcome.
3. Add measured decision reasons and live sorting scores after the engine interface handoff (Phase 2C).
4. Build the **3D live view** from the existing rendering work (Phase 5).
   The current 2D projection is temporary. Taras explicitly deferred 3D implementation until a later increment.
5. Prepare the offline language contract independently (Phase 3A).
   Live policy activation follows the physical and language checkpoints (Phase 3B).

Integrate reviewed quality revisions as the parallel task supplies them. Never import its uncommitted experiments.
The running demo must show its actual engine revision, model, policy, and preset.
The service on port 8890 currently uses engine revision `10d0975`. It does not include the parallel task's newer engine changes.
Its session `263d2001-256d-451e-acb5-22823117c9d9` completed before this restart increment.

Sorting quality does not block the restart control, injection cards, or independent visual work.
Engine ownership does block uncoordinated edits to its snapshot and decision fields.
This task requested an interface handoff after the parallel task's current quality checkpoint.
Taras retains functional QA and acceptance.

## Existing rendering checkpoint

The previous rendering phase description was stale. The following work already exists in separate branches:

| Work | Recorded evidence | Remaining work |
|---|---|---|
| Detailed prototypes and stills | `df8c241`, `visual_assets/README.md` | Taras's final visual acceptance |
| Four browser GLBs and one-second recording | `a230f2c`, `visual_assets/RECORDING.md` | Integration with the current live engine |
| Moodboard and selected looks | `7eede49`, `visual_assets/MOODBOARD.md` | Reuse the selected direction |
| Reproducible cinematic scene | `2fa9853`, `visual_assets/SCENE.md` | Visual acceptance and any requested scene corrections |

Paths in this table are relative to `sim/coffee_sorter/` in the rendering checkout.
The cinematic scene checkout is `/private/tmp/hackspain-coffee-scene`, branch `codex/coffee-cinematic-scene`.
That branch includes the asset and moodboard work. Use it as the render integration source after coordination.
`SCENE.md` records local preview checks. Its final scene PNG and Blender proof files are not committed.
The moodboard checkout is `/private/tmp/hackspain-coffee-moodboard`, branch `codex/coffee-moodboard`.
Taras selected textured `noir-rim`, textured `blueprint`, and clay `warm-roastery`.
The scene assigns them realism, machine explanation, and composition roles. These roles do not establish final visual acceptance.

The recorded proof uses older simulation data. It cannot demonstrate the current engine's sorting quality or motion fix.
The browser proof measured about 6.7 FPS for 552 instances under SwiftShader. Hardware-GPU performance remains unmeasured.
Detailed crowd rendering therefore needs a measured budget. Existing primitive rendering remains available.

## Desired End State

Start one local engine session from a fresh checkout. Open a webpage and inject a stone.
Show camera-derived decisions, actual motion, and the eventual physical outcome.
Show the actual simulation rate even when the engine runs slower than real time.
Reuse the existing visual work and let Taras inspect injected classes through their physical outcomes.
Provide a loss attribution report and timings for the exact preset used by that page.
Add language changes after the separate contract and physical acceptance checkpoints.
Keep sorting quality, render quality, and runtime claims tied to their respective evidence.

## Working agreement

- Commit and push small increments to `codex/coffee-core-live`.
- Preserve `/private/tmp/hackspain-coffee-pr`, including its untracked `.gitignore` and unpushed moodboard plan commit.
- Preserve the separate magnet experiment.
- Add no QA framework or routine unit tests. Use minimum syntax, startup, and runtime checks.
- Leave functional acceptance unchecked until Taras confirms it.
- Confirm ownership before modifying overlapping UI or asset files. Continue work in this task's owned files meanwhile.
- The parallel task owns engine changes. Coordinate snapshot and policy interfaces before either task modifies them.
- Provide a runnable command and request feedback after each visible increment.

## What We're NOT Doing

No public deployment, provider calls, live language activation, online training, coin switching, or generalized engine in the next increment.
No duplicate quality experiments or new render campaign in this task. Asset integration requires confirmed ownership.
The [architecture explanation](../research/2026-09-19-coffee-architecture.md) remains historical context, not current performance evidence.

## Contracts and owners

The [live contract](../contracts/2026-09-19-coffee-live-v1.md) defines IDs, timestamps, versions, appearance keys, and ownership before implementation.

| Scope | Owner and location |
|---|---|
| `sim.py`, `scene.py`, `controller.py`, `vision.py`, `classifier.py`, `bootstrap_model.py`, `engine.py`, engine presets | Parallel quality task, branch `codex/coffee-quality` |
| Overall plan, shared contract, `LIVE.md`, `live.py`, `live_web/*`, separate language-policy files | This task, `/private/tmp/hackspain-coffee-core`, branch `codex/coffee-core-live` |
| New quality evidence and local quality plan | Parallel task, `thoughts/taras/research/coffee-quality/` |
| `visual_assets/*`, GLBs, cinematic scripts | Swarm and existing render owners |
| Existing `web/*` and `export_replay.py` | Frozen until Taras confirms integration ownership |
| Generated `web/index.html` | Taras remains the integration owner |
| Functional QA and acceptance | Taras |

The task named "Improve coffee sorting motion" starts from `/Users/taras/.codex/worktrees/0833/hackspain`.
Its active engine checkout is now `/private/tmp/hackspain-coffee-quality`, based on `10d0975`.
Read-only inspection found its first quality commit, `46219fe`, plus ongoing local engine work.
Do not edit either checkout from this task.

Use port 8890 for this task and 8891 for the quality task.
Coordinate timed jobs. Never stop another task's process to obtain a benchmark.
Review a supplied quality commit before integration. Never merge its uncommitted experiment state.

## Evidence and honest targets

The recorded 1,000 beans/s run captured 202/392 defects, or 51.5%.
Its 94/2,208 false ejects equal 4.26%. That figure excludes spills and cannot represent total good loss.
At 500 beans/s, historical capture was 61.6% and false ejects were 2.9%.
No known mechanism establishes a path to 80% capture with at most 2% total good loss.
Merged objects and jet intersection are candidates for investigation, not proven fixes.

Historical execution needed 13 to 37 wall seconds per simulated second.
The morning review rejected live viewing on that host at the measured configuration.
Our first demonstration permits a slower clock. It does not contradict that real-time conclusion.
Preserve the camera schedule while measuring. Any camera, pool, or feed change creates a new preset requiring another physical evaluation.
A lower feed rate is an explicit throughput tradeoff.

### Metric definitions and pass rules

The quality cohort uses the closed spawn-time interval `[0.8, run_end - 0.6]` in simulated seconds, matching the existing evaluator.
All cohort objects remain in denominators, including spilled and unresolved objects.
A policy-required defect is a class the active policy requires rejecting.
A keep object is a class the policy requires keeping. Under specialty, this is the good class.

| Metric | Definition | Proposed acceptance rule |
|---|---|---|
| Capture | Required defects in reject / all required defects | Wilson 95% lower bound at least 80% |
| Good loss | Keep objects rejected or spilled / all keep objects | Wilson 95% upper bound at most 2% |
| Unresolved | Cohort objects without an outcome / all cohort objects | Report separately. No quality pass with unresolved objects. |
| Admitted throughput | Actual spawned objects / measured simulation interval | At least 500/s for quality acceptance |
| Engine speed | Simulated seconds / wall seconds | At least 1.0 only when measured on the exact preset |
| Browser FPS | Render callbacks / browser wall interval | Target 30 at the preset's reported active object count |
| Injection acknowledgment | Browser send to receipt of successful worker spawn | Proposed p95 below 250 ms on local loopback |

Quality acceptance needs locked seeds 111, 112, and 113 with at least 2,000 eligible objects per seed.
Every seed must meet the bound rules. Also report pooled counts and intervals.
Use seed 7 for training and seed 8 for development. An initial exploratory diagnostic exposed seed 101, so acceptance excludes it. Reserve separate object IDs and seed 9 for model holdout.
Do not use locked physical evaluation seeds for tuning or training.
The first short diagnostic run does not fulfill these acceptance requirements.

Degraded lighting remains outside the supported operating envelope until evaluated.
Later report gains 0.7 and 1.3 separately, with a proposed limit of five percentage points beyond clean good loss.
Do not market robustness before Taras accepts a measured limit.

## Phase 1: reviewed contract, loss attribution, and runtime budget (initial increment implemented)

### Deliverable

Revise the plan before implementation. Deliver a provisional preset and diagnostics before selecting quality changes.
The preset begins at 500 requested beans/s, 250 Hz inspection, the existing resolution, and 0.06 N jets.
Pool size and every runtime parameter belong in the preset. The live engine and diagnostics load that same file.
The preset is provisional. It is not a quality-qualified default.
The bounded comparison selected 400 ellipsoid bodies, down from 1,150, with unchanged measured quality counts and zero starvation in both short runs.

Assign each missed required defect one diagnostic category with this precedence:

1. `unresolved`: no physical outcome yet.
2. `not_detected`: no full camera component observation.
3. `merged_without_target`: merged observations exist but no associated rejection target exists.
4. `classification_or_tracking`: observed without an associated rejection target.
5. `targeted_not_hit`: a target exists but its own associated pulse never hits the object.
6. `hit_not_captured`: its own associated pulse hits, but the object does not enter reject.

These categories partition misses, not experimentally proven causes.
Report ever-merged status, predictions, association coverage, scheduled/fired targets, any hit, and own-pulse hit as separate fields.
Do not call a missing decision association a proven misclassification.
Report captures without an own-pulse hit separately. Do not attribute every capture to control.

### Runtime budget

The aspirational budget per simulated second totals 1,000 wall milliseconds:

| Component | Budget |
|---|---:|
| Physics | 250 ms |
| Inspection rendering | 250 ms |
| Detection | 250 ms |
| Model and controller | 100 ms |
| Evaluation, serialization, IPC, and service overhead | 150 ms |

The 250 Hz camera has a 4 ms interval. Report distributions and overruns as well as accumulated component time.
Also report startup time, peak active bodies, pool starvation, thread counts, and whole-loop wall time.
Keep CPU work in one worker process, separate from network I/O.
Use one native library thread initially. Do not run bulk jobs on the shared SSH host during this increment.
Measure locally first. Measure on `hackspain` before making host capacity claims.
Never infer sequential speed from its reported 32 vCPUs or 128 GiB RAM.

### Verification

```bash
python -m py_compile sim/coffee_sorter/engine.py sim/coffee_sorter/controller.py
python sim/coffee_sorter/engine.py --preset sim/coffee_sorter/configs/default_demo.json --seconds 2 --out /tmp/coffee-core-baseline
```

- [x] Save counts, attribution, source/model/policy/preset versions, and component timings.
- [x] Select one bounded performance or quality candidate only after reading attribution and timings.
- [x] Compare the candidate with the same seed and preset, changing only the named parameter.
- [ ] Taras reviews the result before a wider sweep or quality claim.

## Phase 2: reproducible startup and one live injection (initial increment implemented)

### Deliverable

Make startup runnable from a fresh checkout with a documented training or verified restore command.
The committed historical model is available under `runs/generalization/green_arabica/model/`, despite the ignored `models/` directory.
Its training report uses row-level splits and all-good anomaly statistics. Do not claim independent-object accuracy from it.
Prefer a newly trained model with isolated object groups and train-only statistics for this increment.

Training must exclude partial components and components without exactly one evaluation member.
Persist `(seed, object_uid)` membership. Keep every object's observations in one partition.
Use train-only mean, scale, covariance, and anomaly threshold. Disable automatic row-level early stopping.
Require every supported class in training and holdout. Fail clearly when collection is insufficient.
Save model provenance and feature version. Never substitute evaluation labels for model inputs.

Use a single process for simulation and `aiohttp` for HTTP/WebSocket support.
Pin the installed version. The separate diagnostic page connects to this service at `http://127.0.0.1:8890`.
It displays physical poses, decisions, outcomes, model/policy versions, and actual speed.
Inject a class through physical spawning. A request does not force recognition, jet contact, or rejection.
Keep pixel-derived predictions separate from the known injected class.

The first increment uses one shared session, at most four browser clients, and bounded command/event queues.
Limit sessions to 10 simulated seconds or 300 wall seconds initially. Provide explicit completion and failure states.
Bind to loopback only. Reject foreign browser origins. Use SSH forwarding for remote access.
Restart resets the session ID and all controller state. Full reset UI and durable event replay follow Taras's first feedback.
A reconnect receives the current bounded snapshot. It cannot restore events outside the retained window.

### Verification

```bash
python -m py_compile sim/coffee_sorter/bootstrap_model.py sim/coffee_sorter/live.py
python sim/coffee_sorter/bootstrap_model.py
python sim/coffee_sorter/live.py --host 127.0.0.1 --port 8890 --preset sim/coffee_sorter/configs/default_demo.json
curl --fail http://127.0.0.1:8890/health
```

- [x] Supply the actual dependency installation and bootstrap duration.
- [x] Demonstrate one injected stone reaching a recorded physical outcome.
- [x] Save that object's command ID, observation association, decision, hit status, and outcome.
- [x] Report browser FPS separately from engine rate and admitted throughput.
- [x] Taras tried the live demonstration and reported motion and quality problems.
- [ ] Taras accepts the final behavior. Independent interface work continues under the revised sequence.

## Phase 2A: restart the shared session from the page (in progress)

### Deliverable

Taras can restart a ready, running, or completed session without a terminal command.
The service stops the old worker before starting the new worker. All connected browsers observe the new session.
The page clears old command state and identifies the new session. Previous evidence remains on disk.

### Changes

- Add a same-origin restart request in `live.py` and a restart button in `live_web/*`.
- Reject stale and concurrent restart requests. Reject injections while the worker restarts.
- Resolve pending injection requests before clearing the old session.
- Show the actual engine source revision from worker startup. Update `LIVE.md` with restart behavior.

Restart affects the shared session. State this beside the control.
No engine or controller files change. Keep one active worker and the existing session limits.

### Verification

```bash
cd /private/tmp/hackspain-coffee-core
node --check sim/coffee_sorter/live_web/live.js
.venv-coffee/bin/python -m py_compile sim/coffee_sorter/live.py
# Reuse the current service unless its source predates this increment.
.venv-coffee/bin/python sim/coffee_sorter/live.py --host 127.0.0.1 --port 8890 --preset sim/coffee_sorter/configs/default_demo.json
```

Run `curl --fail http://127.0.0.1:8890/health` from another terminal.
Do not start two services on this port. Leave the quality task's port 8891 unchanged.

#### Automated Verification

- [ ] Syntax checks pass.
- [ ] A bounded lifecycle check verifies a new session ID and an ended old worker.
- [ ] Stale restart requests and foreign origins cannot restart the worker.
- [ ] Prior evidence survives the restart.

#### Automated QA

- [ ] Capture the ready page after one browser-triggered restart through agent-browser.

#### Manual Verification

- [ ] Taras restarts from the page and injects another object without a terminal command.
- [ ] Taras checks that all open browsers show the same new session.

Commit and push the increment after the required checks. Pause for Taras's visible feedback checkpoint.

## Phase 2B: explain every injection and show air contact (next)

### Deliverable

Taras can select a coffee class and retain a result card for every successful injection in the current session.
A new injection does not erase previous results. Selecting a card highlights that physical object when it remains visible.
Cards remain readable after the object leaves the view and after reconnects, within the existing 64-command limit.

Each card shows this sequence:

1. Requested class, command acknowledgment, and object ID.
2. Camera prediction and observation association, with approximate matches labeled.
3. Keep or reject decision and whether the service recorded a scheduled or activated valve.
4. Actual air contact, including own-pulse contact or other/unassociated contact where the evidence permits that distinction.
5. Accepted, rejected, spilled, or unresolved physical outcome, plus time to outcome.

Use labels and distinct symbols as well as color.
Show a command marker when rejection is requested and a different contact marker only after recorded physical contact.
Never infer air contact from a rejection command or trajectory alone.
Keep a short visible contact highlight for readability. Label it as a recent event, not sustained airflow.
Show missing decisions as missing. Do not invent reasons from the injected class.

### Changes

- Add class selection, injection history, selection, and contact indicators in `live_web/*`.
- Publish a bounded injection ledger from the existing request records in `live.py`.
- Retain the service's request provenance and join it to the engine snapshot by session and object ID.
- Update `LIVE.md` with the exact commands and a short functional QA sequence.

The existing fields support the sequence. Exact numeric policy reasons follow Phase 2C.
Events can fall outside the retained event window. Durable object summaries must state any missing event history.

### Verification

```bash
cd /private/tmp/hackspain-coffee-core
node --check sim/coffee_sorter/live_web/live.js
.venv-coffee/bin/python -m py_compile sim/coffee_sorter/live.py
curl --fail http://127.0.0.1:8890/health
```

Use the service command from Phase 2A with this increment's revision.

#### Automated Verification

- [ ] Syntax passes and command IDs remain idempotent.
- [ ] A bounded check preserves two injection records across reconnect and selection changes.

#### Automated QA

- [ ] Capture one injected-object card and the contact legend with agent-browser.

#### Manual Verification

- [ ] Taras injects stone and stick separately and can clearly explain both results from their cards.
- [ ] Taras distinguishes commanded rejection, recorded air contact, and the final physical outcome.

## Phase 2C: live scores and measured decision reasons (after engine handoff)

### Deliverable

Show provisional live capture, total good loss, correctly sorted objects, unresolved objects, and spills.
Display numerator and denominator beside each percentage. Display unavailable values when a denominator is zero.
Keep engine speed and browser FPS separate from sorting scores.

Use the existing cohort definition with the current simulation time as the provisional run end.
Keep spills and unresolved objects in cohort denominators. Label the changing cohort and live values as provisional.
Correctly sorted objects are required defects in reject plus keep objects in accept.
Divide that count by all eligible cohort objects, including unresolved objects.
Show manually injected objects separately when they fall outside the quality cohort.
Do not label classifier confidence or a selected-object score as overall sorting accuracy.
Compare final values with the engine's report for the exact same session.

For each injected object, add the measured reject probability, policy threshold, and anomaly-trigger status when available.
Explain the actual decision rule, scheduling result, recorded contact, and final outcome.
Use explicit reason values from the engine. Do not generate unsupported causal explanations in the page.
An approximate object association remains approximate even when the underlying controller decision is exact.

### Changes

Agree on an additive snapshot contract after the quality owner supplies its committed handoff.
The engine owner supplies cumulative cohort counts and decision evidence from its existing evaluator and controller data.
This task owns the service and UI integration. Evaluator truth never reaches the model or controller inputs.
Do not compute scores from the page's truncated object or event lists.
Do not serialize the full engine report at every pose update.
Measure added evaluation and serialization cost before retaining the update cadence.

### Verification

```bash
cd /private/tmp/hackspain-coffee-core
node --check sim/coffee_sorter/live_web/live.js
.venv-coffee/bin/python -m py_compile sim/coffee_sorter/live.py
curl --fail http://127.0.0.1:8890/state
```

The handoff must supply the exact bounded comparison command before implementation starts.

#### Automated Verification

- [ ] Final displayed counts match the same session's terminal report.
- [ ] A decision explanation matches its recorded score, threshold, and anomaly result.
- [ ] Measure the added score publication cost and preserve the inspection schedule.

#### Automated QA

- [ ] Capture live and final score states with their denominators and provisional labels.

#### Manual Verification

- [ ] Taras can explain one correct result and one miss from the object cards and scoreboard.
- [ ] Taras verifies that restart clears the new session's scores and preserves prior evidence.

## Phase 3A: language policy contract and offline cases (independent)

### Deliverable

Create a versioned contract, an offline case file, and a deterministic policy compiler in separate files.
Use `keep`, `reject`, and `unchanged` for raw class actions. Store the compiled policy separately.
Do not edit the parallel task's controller or change the live protocol in this increment.
Review the existing `codex/jev` contracts for useful conventions. They solve a different sorting task, so do not merge them wholesale.

The eight-call Jev spike does not establish accuracy.
The two effective policy matches partly inherit defaults. Ignoring most reject instructions could produce the same result.
Define `unchanged` as an instruction that does not mention the class.
Score raw actions for explicitly named classes as well as compiled policies.
Include opposing initial policies, such as initially keeping black beans before requesting rejection.
Use a hero request no severity preset expresses, such as keeping broken beans while rejecting shell beans.

Replace duplicated intent/ambiguity decisions with one governing interpretation gate.
Preserve raw choices, probabilities, compiled policies, and gate reasons.
The recorded ambiguity probabilities were 0.65 and 0.54, with confidence values 0.30 and 0.07.
Those values justify deferral, not a claim that ambiguity is established.
Prepare 12 paired English/Spanish cases with unsupported and contradictory requests included.
Provider evaluation later repeats each language case three times. That makes 72 evaluated requests before additional controls.
Propose at least 95% raw explicit-action accuracy, no unsafe activation, and identical activation decisions across repeats.
Require supported and clear probabilities at least 0.9 for activation. These thresholds remain uncalibrated safety choices.
Compare controls with unchanged policy, omitted current actions, and repeated identical requests.
Do not remove uncertainty gates to force success.

The offline compiler preserves the current policy when the result is invalid, unsupported, contradictory, or deferred.
Synthetic responses verify compiler behavior only. They do not measure language accuracy.

### Verification

```bash
# Future commands supplied by this increment. These perform no provider calls.
.venv-coffee/bin/python -m py_compile sim/coffee_sorter/language_policy.py
.venv-coffee/bin/python sim/coffee_sorter/language_policy.py --validate-cases sim/coffee_sorter/configs/policy_language_cases.json
```

#### Automated Verification

- [ ] Validate all case fields, class names, opposing starting policies, and expected compiled actions.
- [ ] Report bounded local compiler checks without language-accuracy claims.

#### Automated QA

- [ ] Save one supported result and one deferred result with the unchanged active policy hash.

#### Manual Verification

- [ ] Taras reviews the contract, example requests, and the proposed provider evaluation scope.

## Phase 3B: provider evaluation and live policy activation (gated)

### Deliverable

This task adds `sim/coffee_sorter/jev_policy.py` as the evaluator for the frozen language cases.
Run provider evaluation only after Taras authorizes the calls and their cost.
Coordinate the per-class controller policy with the quality task before editing `controller.py`.
Specify whether known kept classes override anomaly rejection. Preserve rejection for unknown observations.
Apply a reviewed policy at a defined session boundary and record its version.
Demonstrate affected classes through injection after physical-quality acceptance and Taras's approval of language results.

### Verification

```bash
# Future command. This calls a provider and requires the separate evaluation decision.
.venv-coffee/bin/python sim/coffee_sorter/jev_policy.py evaluate --cases sim/coffee_sorter/configs/policy_language_cases.json --out /tmp/coffee-jev-policy
```

#### Automated Verification

- [ ] Save raw choices, probabilities, compiled policies, governing gate reasons, and provider versions.
- [ ] Report every case and repeat against the language pass rules above.

#### Automated QA

- [ ] Record the active policy version before and after a supported request and a deferred request.

#### Manual Verification

- [ ] Taras reviews the language results, pass rules, and activation scope before live policy changes.
- [ ] Taras accepts the physical quality checkpoint before live activation.
- [ ] Taras injects affected classes and accepts the visible policy behavior.

## Phase 4: gallery and candidate comparison, deferred

### Deliverable

This task owns a gallery under `live_web/` and a candidate report from the proposed `learning.py` command.
The report identifies reviewed training objects, locked evaluation objects, candidate provenance, and the comparison with the active model.
Coordinate classifier changes with the quality task before enabling training.

Implement crops, a fixed standardized PCA map, class corrections, and one candidate comparison.
Do not implement a second binary policy learner, vision-model suggestion queue, activation framework, or rollback framework yet.
A binary keep/reject correction is not a physical class label.
Train candidates on reviewed training objects. Keep the locked evaluation objects unchanged.
The existing model remains active during comparison.

### Verification

```bash
python sim/coffee_sorter/learning.py evaluate --dataset <reviewed-training-manifest> --out /tmp/coffee-learning-candidate
```

This is a future command. Taras reviews the gallery before training controls.

#### Automated Verification

- [ ] Save candidate provenance and results against locked evaluation objects.

#### Automated QA

- [ ] Provide a bounded gallery view for review, without changing the active model.

#### Manual Verification

- [ ] Taras accepts the gallery before any training controls.

## Phase 5: 3D live view using existing rendering (later increment)

### Deliverable

Replace the temporary 2D projection with a 3D live view after ownership confirmation.
Integrate the selected existing visual assets into that view.
Reuse the browser GLBs, their dimension mapping, and their instancing approach.
Reuse the selected scene direction. Do not repeat completed stills, the moodboard, or the one-second recording.
The Blender scene is an offline presentation asset. It does not become the live renderer automatically.

### Changes

- Review the supplied render revision and confirm which asset and viewer files this task may change.
- Reuse detailed assets for selected-object inspection first. Keep crowd rendering within the measured browser budget.
- Check origins, dimensions, axes, quaternions, UID lifetime, and fallbacks against the existing live contract.
- Add crowd detail only if the exact live page meets the browser target at the measured active count.

Detailed assets cover good, black, insect-damaged, and broken beans. Other classes retain explicit presentation fallbacks.
Use a color-preserving view when color explains the class. Blueprint and clay cannot show color defects.
Never use true class metadata as model input. Asset mappings remain display-only.

The completed recording uses poses sampled near 30 Hz. Short jet pulses can fall between samples.
Its source is historical. Label it as a recording and retain its source hashes.
Smooth display interpolation, if selected later, must preserve physical endpoints and must not change reported outcomes.
No new replay export, full film, or higher-frequency capture belongs to this increment.
The exporter currently caps sampling at 60 FPS. The 2 ms physics step allows at most 500 unique samples per second.
Keep textures and Blender sources outside ordinary Git when a new bundle exceeds 20 MB. Use the swarm artifact store.

### Verification

```bash
# Inspect the completed assets without starting a render or a simulation.
git -C /private/tmp/hackspain-coffee-scene show --stat 2fa9853
python3 -m http.server 8765 --bind 127.0.0.1 --directory /private/tmp/hackspain-coffee-scene/sim/coffee_sorter
```

The existing proof is at `http://127.0.0.1:8765/visual_assets/browser/proof.html`.
The implementation supplies its exact live command after ownership and file scope are confirmed.

#### Automated Verification

- [ ] Check source and asset hashes, transform mapping, and explicit fallback classes.
- [ ] Check syntax and startup for the actual integration files.

#### Automated QA

- [ ] Record one bounded browser measurement with renderer, active count, frame times, and errors.
- [ ] Report detailed crowd rendering separately from selected-object inspection and engine speed.

#### Manual Verification

- [ ] Taras confirms overlapping file ownership before edits begin.
- [ ] Taras accepts the 3D view, asset appearance, and current live motion independently.

## Phase 6: public deployment, deferred

### Deliverable

This task prepares a deployment runbook, exact service configuration, and a rollback revision after local acceptance.
Record the final file paths and verify existing host ownership before implementation.
Taras owns the public-exposure decision. The swarm retains its rendering allocation.

Codex core owns deployment preparation after local acceptance. Taras chooses DNS and approves public exposure.
Target: Vercel frontend with an HTTPS/WSS API on `hackspain`.
The first local demo remains same-origin and requires no CORS or DNS changes.
Before public deployment, implement an explicit frontend API URL and one exact allowed origin.
Use authenticated access, a hard cap of one shared session, and a visible busy state.
Document process supervision, Caddy routing, restart, rollback revision, and resource limits before changing services.
Reserve one engine process and one native thread. Coordinate rendering or training allocations with the swarm before host work.

### Verification

```bash
curl --fail "$COFFEE_API_BASE_URL/health"
ssh hackspain 'systemctl --user status <coffee-service>'
ssh hackspain 'journalctl --user -u <coffee-service> -n 50 --no-pager'
```

Rollback procedure must include the actual prior revision and service commands before deployment.
No public service or DNS changes belong to this increment.

#### Automated Verification

- [ ] Supply exact supervision, access, resource limits, and rollback commands.

#### Automated QA

- [ ] Check the authorized endpoint and its busy state after deployment.

#### Manual Verification

- [ ] Taras authorizes public exposure and accepts the deployed demonstration.

## Quick Verification Reference

| Check | Command |
|---|---|
| Branch and changes | `git status --short --branch` |
| Syntax | `python -m py_compile sim/coffee_sorter/engine.py sim/coffee_sorter/live.py sim/coffee_sorter/bootstrap_model.py` |
| Model | `python sim/coffee_sorter/bootstrap_model.py` |
| Exact preset diagnostics | `python sim/coffee_sorter/engine.py --preset sim/coffee_sorter/configs/default_demo.json --seconds 2 --out /tmp/coffee-core-baseline` |
| Live service | `python sim/coffee_sorter/live.py --host 127.0.0.1 --port 8890 --preset sim/coffee_sorter/configs/default_demo.json` |

Commands in this reference are now runnable. Evidence identifies the revision that actually ran.
Existing tests remain available for Taras. Current source contains 72 simulator test functions and six replay test functions. One replay function is opt-in.
Five default replay checks can pass while the optional sixth remains skipped. Old counts do not verify this increment.

## Appendix: review disposition and deferred work

| Review findings | Resolution |
|---|---|
| C1, I1 | Attribute misses first. Define cohorts, spills, uncertainty, and conservative pass rules. Targets remain unproven. |
| C2, I5 | Profile the exact live preset. Preserve camera rate. Show a slower clock. Include model bootstrap. |
| C3, I2, I3 | Defer language. Define opposing cases, raw scoring, one gate, per-class policies, and anomaly interaction. |
| I4 | Record ownership and the protocol before code. Freeze the existing viewer and generated page. |
| I6 | Create a deployment phase with access, limits, supervision, rollback requirements, and verification commands. |
| I7 | Bound the asset proof. Correct exporter limits. Require fallback assets, contact proof, provenance, and storage limits. |
| I8 | Reduce learning to gallery, corrections, and candidate comparison. |
| M1, M2, M3 | Update source revision. Remove brittle line references. Distinguish six replay functions from five default checks. |
| M4, M5, M6 | Define timing endpoints and active object counts. Keep replay source provenance visible. |
| M7 | Add desired state, non-goals, quick commands, and this appendix. |

Coin switching, generalized products, automated suggestions, and advanced learning remain deferred.

## Manual E2E

Taras runs the completed increment from its isolated checkout:

```bash
cd /private/tmp/hackspain-coffee-core
python3.13 -m venv .venv-coffee
source .venv-coffee/bin/activate
python -m pip install -r sim/coffee_sorter/requirements.txt
python sim/coffee_sorter/bootstrap_model.py
python sim/coffee_sorter/live.py --host 127.0.0.1 --port 8890 --preset sim/coffee_sorter/configs/default_demo.json
```

In another terminal:

```bash
curl --fail http://127.0.0.1:8890/health
open http://127.0.0.1:8890
```

For the current checkpoint, inject a stone. Observe its command acknowledgment, camera-derived decision, and physical outcome.
After Phase 2A, use Restart session and inject again without a terminal command.
After Phase 2B, select stone and stick in separate injections. Review each object's retained result card.
After Phase 2C, compare live scores with the same session's final counts.
Check actual engine speed and browser FPS separately. Before Phase 2A, restart the service to reset the session.
For a later SSH demonstration, install the same revision and requirements on the host, then forward its loopback service:

```bash
ssh -N -L 8890:127.0.0.1:8890 hackspain
```

No public deployment or language command is required for this checkpoint.
The quality task supplies separate commands on port 8891. Preserve that service while Taras reviews this interface.
