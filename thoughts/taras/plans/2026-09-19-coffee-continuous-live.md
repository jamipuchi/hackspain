---
date: 2026-09-19
owner: taras
status: proposed
implementation_branch: codex/coffee-core-live
quality_integration_revision: 90dffc1
---

# Continuous coffee demonstration

The deployed engine runs continuously, including when no browser is connected.
Opening the page observes the current conveyor. Injection adds an object without starting or resetting the engine.
Scores describe a rolling interval. Visitors do not need to restart the session.
Restart remains an administrative action. Bounded runs remain available for reproducible diagnostics.

This plan changes the next engine increment. It does not authorize public deployment or claim that continuous operation already works.
Taras owns functional QA and acceptance. Existing rendering ownership remains unchanged.

## Current evidence

The quality delivery is integrated at `90dffc1`. The exact selected model and manifest are installed and match the frozen evaluation.
Capture passed its reserved-seed bounds. Good loss and real-time engine speed remain below their acceptance requirements.
The existing service stops after ten simulated seconds. It starts only after the first injection.

Several engine histories grow with total runtime. The evaluator also scans every historical object on every physics step.
Removing the duration limit alone would eventually consume more memory and slow the engine.
The current command limit permits only 64 injections per session. Continuous operation needs bounded retention without a lifetime injection limit.
An idle browser connection also lost an injection before the worker received it. Reloading allowed the next injection to succeed.

## Score contract

The proposed default window is **60 simulated seconds**. Taras can choose another duration or clock before implementation.
Label the clock explicitly. At approximately 0.2x engine speed, this window represents approximately five wall minutes.
Keep engine speed and browser FPS separate from sorting scores.

Use a rolling cohort based on spawn time. Do not select only objects that recently resolved.
Allow a documented 0.6 simulated seconds for travel before an object enters the displayed cohort.
At simulation time `T`, membership is `T - 0.6 - N < spawn_time_s <= T - 0.6`.
Before `T = 0.6`, the cohort is empty. This formula includes time-zero objects once they reach the settling age.
Show the actual bounds and available duration `max(0, min(N, T - 0.6))` during warm-up.
Show newer objects separately as settling. Keep older unresolved objects inside every applicable denominator until their spawn time leaves the window.
This delay preserves the current diagnostic travel allowance. It does not assert that every object resolves within 0.6 seconds.

| Score | Numerator | Denominator |
|---|---|---|
| Sorting accuracy | Required defects rejected plus keep objects accepted | All objects in the window |
| Defect capture | Required defects rejected | All required defects in the window |
| Good loss | Keep objects rejected or spilled | All keep objects in the window |
| Unresolved | Objects without a recorded outcome | All objects in the window |

Spills and unresolved objects remain in denominators. Show counts beside percentages and unavailable values for empty denominators.
The interface labels these as rolling simulation scores. They are not physical-machine measurements or classifier confidence.
The fixed acceptance cohort remains unchanged. Rolling display values do not replace reserved evaluation reports.

Use evaluator truth only to count outcomes. It must never enter control or model inputs.
Label manual injections separately and exclude them from the background-feed scores. Their requested mix must not distort the feed benchmark.
For future model or policy changes, start a fresh score epoch and show warm-up. Never silently mix versions in one score.

## Phase 1: bound engine history and evaluation work

### Deliverable

The engine can retain active objects, rolling score data, and a bounded injection history without retaining every completed object.
Each physics step processes new outcomes and contact events instead of scanning all historical objects.

### Changes

- Replace append-only physical event and decision histories with bounded consumption in continuous mode.
- Preserve active physical objects. Mark overdue injections unresolved instead of inventing an outcome or silently deleting them.
- Keep compact rolling counts and bounded timing samples. Preserve the existing bounded diagnostic report path.
- Set explicit memory, snapshot, command-result, and rotating-log limits. Expose retained counts for diagnostics.

Pruning must preserve pending valve targets, active tracks, and approximate object associations.
Resolved physical objects can be retired only after their outcome and required evidence reach the retained ledger.
Keep cumulative scalar totals when useful. Do not retain full objects solely to produce lifetime totals.

### Verification

```bash
cd /private/tmp/hackspain-coffee-core
.venv-coffee/bin/python -m py_compile sim/coffee_sorter/engine.py sim/coffee_sorter/sim.py sim/coffee_sorter/controller.py
.venv-coffee/bin/python sim/coffee_sorter/engine.py --preset sim/coffee_sorter/configs/default_demo.json --seconds 2 --out /tmp/coffee-continuous-retention-check
```

- [ ] Automated verification: compare bounded diagnostic counts before and after retention changes on development seed 8.
- [ ] Automated verification: check window entry, expiry, delayed outcomes, spills, and zero denominators with a small synthetic stream.
- [ ] Automated QA: save retained-object counts, snapshot size, and per-step costs over a bounded development run.
- [ ] Manual verification: Taras reviews the retention limits and visible treatment of unresolved injected objects.

## Phase 2: run automatically and publish rolling scores

### Deliverable

One local service starts the conveyor automatically and continues without connected browsers.
The page shows rolling accuracy, capture, loss, unresolved objects, settling objects, and window warm-up.
Continuous mode uses a separate preset. The current diagnostic preset retains its bounded duration.

### Changes

- Add an explicit continuous mode after Phase 1 passes. Remove duration gates only for that mode.
- Publish bounded aggregate scores from the evaluator at one wall-second intervals. Keep pose updates separate.
- Repair idle reconnect and acknowledgment recovery. Reconnection must preserve the running engine and command identity.
- Replace the lifetime command cap with bounded admission and retained acknowledgments. Reject expired command epochs instead of repeating old injections.

The score API includes clock, duration, bounds, warm-up, settling count, numerator, denominator, and source/model/policy versions.
Validate the selected artifact and its physical compatibility before startup. Service duration and score settings must not trigger accidental retraining.
The UI must not derive scores from truncated pose or event lists.
Every restart or worker recovery uses a new session ID and visibly resets score warm-up.
The deployed visitor UI does not expose the administrative restart control.
This local increment does not change public routing, authentication, or process supervision.

Continuous commands use a server-issued epoch lasting 60 wall seconds. Retain completed acknowledgments for the current and previous epoch.
Allow at most 64 admitted commands per epoch. Retain pending commands until their result arrives, within the existing bounded queue.
Reject an expired epoch with `command_epoch_expired` and no spawn. Never reinterpret an expired duplicate as a new injection.
Keep the latest 64 completed injection cards and all bounded pending requests. Mark history eviction visibly.
Finalize this proposed v2 command contract before implementation. Bounded v1 commands keep their existing session-long identity rule.

### Verification

The following command becomes available in this phase through the new continuous preset. Run it in terminal one:

```bash
cd /private/tmp/hackspain-coffee-core
.venv-coffee/bin/python sim/coffee_sorter/live.py --port 8892 --preset sim/coffee_sorter/configs/continuous_demo.json --out /tmp/coffee-continuous-local
```

Run these checks in terminal two:

```bash
cd /private/tmp/hackspain-coffee-core
curl --fail http://127.0.0.1:8892/health
curl --fail http://127.0.0.1:8892/state
node --check sim/coffee_sorter/live_web/live.js
```

- [ ] Automated verification: the simulation advances before any injection and beyond the old ten-second limit.
- [ ] Automated verification: two clients receive the same score counts and engine session.
- [ ] Automated verification: expired command epochs cannot create duplicate objects.
- [ ] Automated QA: disconnect and reconnect after idle, then verify one acknowledged physical injection.
- [ ] Manual verification: Taras opens the page, sees an active conveyor, and reads the window without a restart.

## Phase 3: measure sustained operation before deployment

### Deliverable

A local run spans at least three full score windows. Its report identifies memory growth, retained counts, and engine cost by interval.
The run compares exact rolling counts against an independent calculation of the same spawn-time window.
Any intended deployment-host benchmark remains separate and requires host coordination.

### Verification

Use the Phase 2 service command with a new output directory. Sample the real process while it runs:

```bash
curl --fail http://127.0.0.1:8892/state
ps -o pid,rss,etime,%cpu -p <engine-worker-pid>
```

- [ ] Automated verification: retained structures stay within their configured limits across three windows.
- [ ] Automated verification: scoreboard counts equal the independently calculated cohort at each sampled boundary.
- [ ] Automated QA: document memory and engine cost by interval, including any slowdown.
- [ ] Manual verification: Taras accepts continuous behavior before the deployment phase begins.

## Manual E2E

After implementation, start the continuous service with the Phase 2 command.
Open `http://127.0.0.1:8892` before injecting anything. Check that the conveyor already moves.
Inject an object and observe its acknowledgment, decision, contact, and outcome.
Close the page and reconnect after one minute. Check that the same engine session continued.
Wait beyond one score window. Check that old objects leave the counts without a restart.
Check warm-up and version labels after an administrative restart.
Stop the local service with Ctrl+C. Preserve its report and rotated logs.

Per-object result cards and the required 3D view remain in the parent plan.
