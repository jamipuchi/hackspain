---
date: 2026-09-19
planner: Codex
topic: Coffee sorting quality and the first live engine demonstration
status: in-progress
owner: taras
source_branch: codex/coffee-sorter-upstream
source_revision: 4d0f705
implementation_branch: codex/coffee-core-live
---

# Coffee sorter implementation

Taras owns functional QA and acceptance. Codex owns the plan, engine measurements, and first live demonstration.
The swarm owns rendering assets. This task does not merge its branch or accept its visuals.

This revision addresses [Claude's review](../reviews/2026-09-19-review-of-coffee-interactive-learning-demo.md).
Only the first bounded increment is authorized here. Later phases remain proposed work with separate feedback checkpoints.

## Desired End State

Start one local engine session from a fresh checkout. Open a webpage and inject a stone.
Show camera-derived decisions, actual motion, and the eventual physical outcome.
Show the actual simulation rate even when the engine runs slower than real time.
Provide one loss attribution report and timings for the exact preset used by that page.
Do not claim that this small demonstration establishes sorting quality.

## Working agreement

- Commit and push small increments to `codex/coffee-core-live`.
- Preserve `/private/tmp/hackspain-coffee-pr`, including its untracked `.gitignore` and unpushed moodboard plan commit.
- Preserve the separate magnet experiment.
- Add no QA framework or routine unit tests. Use minimum syntax, startup, and runtime checks.
- Leave functional acceptance unchecked until Taras confirms it.
- Confirm ownership before modifying existing UI files. Continue independent engine work while Taras responds.
- Do not expand beyond the first visible demonstration without a feedback checkpoint.

## What We're NOT Doing

No public deployment, Jev integration, online training, coin switching, generalized engine, asset merge, or full inspector in this increment.
No automatic sweep or broad QA campaign. No new cinematic renders.
The [architecture explanation](../research/2026-09-19-coffee-architecture.md) remains historical context, not current performance evidence.

## Contracts and owners

The [live contract](../contracts/2026-09-19-coffee-live-v1.md) defines IDs, timestamps, versions, appearance keys, and ownership before implementation.
Codex owns `vision.py`, `sim.py`, `controller.py`, model/bootstrap changes, and new engine files.
`export_replay.py` remains frozen under its existing owner until coordination completes.
Taras owns integration of generated `web/index.html`. Existing `web/viewer.js` remains frozen pending ownership confirmation.
The first diagnostic page lives in `live_web/`, separate from the existing viewer.
Swarm assets remain on `codex/coffee-realistic-assets`, with visual acceptance pending.

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

The quality cohort contains objects spawned after 0.8 simulated seconds and before the run ends minus 0.6 seconds.
All cohort objects remain in denominators, including spilled and unresolved objects.
A policy-required defect is a class the active policy requires rejecting.
A keep object is a class the policy requires keeping. Under specialty, this is the good class.

| Metric | Definition | Proposed acceptance rule |
|---|---|---|
| Capture | Required defects in reject / all required defects | Wilson 95% lower bound at least 80% |
| Good loss | Keep objects rejected or spilled / all keep objects | Wilson 95% upper bound at most 2% |
| Unresolved | Cohort objects without an outcome / all cohort objects | Report separately. No quality pass with unresolved objects. |
| Admitted throughput | Actual spawned objects / measured simulation interval | At least 500/s for the stretch target |
| Engine speed | Simulated seconds / wall seconds | At least 1.0 only when measured on the exact preset |
| Browser FPS | Render callbacks / browser wall interval | Target 30 at the preset's reported active object count |
| Injection acknowledgment | Browser send to receipt of successful worker spawn | Proposed p95 below 250 ms on local loopback |

Quality acceptance needs locked seeds 101, 102, and 103 with at least 2,000 eligible objects per seed.
Every seed must meet the bound rules. Also report pooled counts and intervals.
Use seeds 7 and 8 for training and development. Reserve separate object IDs and seed 9 for model holdout.
Do not use locked physical evaluation seeds for tuning or training.
The first short diagnostic run does not fulfill these acceptance requirements.

Degraded lighting remains outside the supported operating envelope until evaluated.
Later report gains 0.7 and 1.3 separately, with a proposed limit of five percentage points beyond clean good loss.
Do not market robustness before Taras accepts a measured limit.

## Phase 1: reviewed contract, loss attribution, and runtime budget

### Deliverable

Revise the plan before implementation. Deliver a provisional preset and diagnostics before selecting quality changes.
The preset begins at 500 requested beans/s, 250 Hz inspection, the existing resolution, and 0.06 N jets.
Pool size and every runtime parameter belong in the preset. The live engine and diagnostics load that same file.
The preset is provisional. It is not a quality-qualified default.

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

- [ ] Save counts, attribution, source/model/policy/preset versions, and component timings.
- [ ] Select one bounded performance or quality candidate only after reading attribution and timings.
- [ ] Compare the candidate with the same seed and preset, changing only the named parameter.
- [ ] Taras reviews the result before a wider sweep or quality claim.

## Phase 2: reproducible startup and one live injection

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

- [ ] Supply the actual dependency installation and bootstrap duration.
- [ ] Demonstrate one injected stone reaching a recorded physical outcome.
- [ ] Save that object's command ID, observation association, decision, hit status, and outcome.
- [ ] Report browser FPS separately from engine rate and admitted throughput.
- [ ] Taras performs functional QA and acceptance before more controls or broader evaluation.

## Phase 3: language policy contract, deferred

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
Before integration, evaluate 12 paired English/Spanish cases, repeated three times, with unsupported and contradictory cases included.
Propose at least 95% raw explicit-action accuracy, no unsafe activation, and identical activation decisions across repeats.
Require supported and clear probabilities at least 0.9 for activation. These thresholds remain uncalibrated safety choices.
Compare controls with unchanged policy, omitted current actions, and repeated identical requests.
Do not remove uncertainty gates to force success.

Add a versioned per-class policy in `controller.py` before integration.
Specify whether known kept classes override anomaly rejection. Preserve rejection for unknown observations.
Demonstrate the policy with injected affected classes, rather than relying on their random feed frequency.

### Verification

```bash
python sim/coffee_sorter/jev_policy.py evaluate --cases sim/coffee_sorter/configs/policy_language_cases.json --out /tmp/coffee-jev-policy
```

This command is a future deliverable. No new provider calls are authorized by this increment.
Taras reviews the contract and results before live activation.

## Phase 4: gallery and candidate comparison, deferred

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

## Phase 5: swarm assets, separate ownership

The handoff names draft PR https://github.com/tarasyarema/hackspain/pull/4 and last known commit `a230f2c`.
The source checkout also contains unpushed moodboard decisions in `bef9b61`.
Preserve those decisions: textured `noir-rim`, textured `blueprint`, and clay `warm-roastery`.
Blueprint and clay cannot communicate color defects. Use a textured color view when color matters.
The remote asset branch currently resolves to `a230f2c`. The review's missing-branch statement is stale. Asset acceptance remains pending.

Bound the next proof to three 960x720 stills and one second at 30 FPS.
Include a belt-contact close-up, a color defect, and the injection class.
Use explicit primitive fallbacks for classes without a reviewed asset, including faded and stone.
Represent jets with a labeled schematic overlay tied to recorded valve events.
Do not animate improved sorting outcomes.

The exporter currently limits `--fps` to 60. Higher sampling requires a separately owned change.
The 2 ms physics step limits unique state samples to 500 Hz.
The shipped replay records historical source hashes, including older `vision.py`. Label visual proofs with that provenance.
Keep textures and Blender sources outside ordinary Git when the bundle exceeds 20 MB, using the swarm's artifact store.
Do not start a full film until Taras specifies duration, resolution, deadline, and render budget.

### Verification

```bash
gh pr view 4 --repo tarasyarema/hackspain
python sim/coffee_sorter/export_replay.py --help
```

The swarm supplies its exact render command and asset manifest. This task does not regenerate `web/index.html`.

## Phase 6: public deployment, deferred

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

## Quick Verification Reference

| Check | Command |
|---|---|
| Branch and changes | `git status --short --branch` |
| Syntax | `python -m py_compile sim/coffee_sorter/engine.py sim/coffee_sorter/live.py sim/coffee_sorter/bootstrap_model.py` |
| Model | `python sim/coffee_sorter/bootstrap_model.py` |
| Exact preset diagnostics | `python sim/coffee_sorter/engine.py --preset sim/coffee_sorter/configs/default_demo.json --seconds 2 --out /tmp/coffee-core-baseline` |
| Live service | `python sim/coffee_sorter/live.py --host 127.0.0.1 --port 8890 --preset sim/coffee_sorter/configs/default_demo.json` |

Commands become runnable as each implementation commit lands. Evidence must identify what actually ran.
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
python3 -m venv .venv-coffee
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

Inject a stone. Observe its command acknowledgment, camera-derived decision, and physical outcome.
Check actual engine speed and browser FPS separately. Restart the service to reset the session.
For a later SSH demonstration, install the same revision and requirements on the host, then forward its loopback service:

```bash
ssh -N -L 8890:127.0.0.1:8890 hackspain
```

No public deployment or language command is required for this first checkpoint.
