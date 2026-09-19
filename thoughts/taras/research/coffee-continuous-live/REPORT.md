# Continuous coffee checkpoint

Date: 2026-09-19

Taras owns functional QA and acceptance. This report records automated and local browser evidence.
The required three-window endurance run remains pending.

## Historical baseline

The frozen development baseline is `/private/tmp/coffee-continuous-baseline-ea43a12`.
It ran development seed 8 for two simulated seconds and spawned 1,000 objects.
The cohort contained 300 eligible objects, 40 required defects, 33 captured defects, 260 keep objects, 16 lost objects, and zero unresolved objects.
The run took 41.90 wall seconds and reported 0.0477x engine speed.
Worktree I/O overlapped that run, so it is not a clean benchmark.

The selected model SHA-256 remains:

```text
89513398373c6e0e81286419962feb3e312742de14a76d02dd0d819ad5264a5a
```

Historical source hashes were:

| File | Historical SHA-256 |
|---|---|
| `engine.py` | `c0f8fcb79bf93b27b5b78e10f2b777a231b023191d9066e60da4a5a6981dff1d` |
| `controller.py` | `ee2045d8c8694d2c19a90a63eeef33b9d3b29f51255bfcd79a2823ed10f712d6` |
| `sim.py` | `1bda4e58ee7c7c7635ac81062891a8189d56639b35ba48b5459357ec1df7e7c9` |

The current preview reports different hashes for each changed source file.
At integration revision `b07604b`, they were `242da9f` for `engine.py`, `25b027d` for `controller.py`, and `e4935e5` for `sim.py`.
The historical acceptance freeze remains unchanged.

## Failed preview preserved

The first continuous preview used session `528efba4-d7a6-41b0-9736-bbf8a2c435f0`.
A multi-client reconnect probe left the HTTP state pump blocked at 28.237 simulated seconds and heartbeat 1,510.
The worker continued and wrote a final state at 67.497 simulated seconds after shutdown.
`/health` incorrectly returned `running` during the stall.

The stopped run remains under `/private/tmp/coffee-continuous-preview`.
Its service profile recorded 1,552 broadcast starts and 1,551 send-wait samples.
The unfinished broadcast therefore had no completed send-duration sample.
The precise client behavior that triggered the blocked send remains unproven.

The fix bounds each client send to 0.5 seconds. It force-closes failed clients and cleans cancelled send tasks.
Continuous health now returns 503 after three seconds without an application heartbeat.
Focused tests reproduce a blocked client, broadcast cancellation, failed send, blocked pump, and stopped pump.

## Automated checks

Run from `sim/coffee_sorter`:

```bash
../../.venv-coffee/bin/python -m unittest test_continuous_engine.py test_live.py -v
../../.venv-coffee/bin/python -m py_compile engine.py sim.py controller.py rolling_scores.py live.py test_continuous_engine.py test_live.py
node --check live_web/live.js
git diff --check
```

The focused suite passed 25 tests. Eight cover engine retention and rolling scores. Seventeen cover service behavior and startup validation.
The existing `test_controller.py` suite still has ten timer-mock failures on published main and this branch.
Those pre-existing failures remain outside this increment.

Independent Standards and Spec reviews found no remaining issues after two fixes.
The first fix bounded per-Fire contact evidence. The second fix cleaned cancelled client sends and stale pump health.

## Browser and protocol checks

The reviewed preview runs at `http://127.0.0.1:8892`.
Its current session is `fae8a089-0d6a-4801-b90b-f74aa873890f`.

Agent-browser verified a 1,440 by 900 desktop viewport and a 320 by 568 phone viewport.
Both views had exact viewport scroll dimensions and no horizontal overflow.
The phone kept the injection button, latest card, conveyor, and compact scores visible.
The Details dialog kept its Close button visible and used its own scroll area.

Ten same-tick injection clicks created ten pending UUIDs. Each request retained its original payload.
The latest card followed the final click and resolved with these distinct values:

- Headline: `Rejected`
- Prediction: `stone (approximate object match)`
- Controller action: `Reject, valves scheduled`
- Air contact: `Own pulse, 2 nozzle contact steps`
- Physical outcome: `Reject path`

A 17-click burst reached the 16-pending client limit.
The latest card changed to `Command failed` and showed the saturation error.

A two-client probe received equal rolling score counts and one shared session.
The original command, simultaneous duplicate, and retained retry all returned object ID 18,813.
After both clients closed, simulation time advanced from 37.457 to 37.932 seconds.
The application heartbeat advanced from 2,235 to 2,264.
The next connection retained the same engine session.

Another probe retried object ID 22,536 after the 60-second command epoch changed.
The server accepted the exact old-epoch payload and returned the same object ID.
It did not create a duplicate object.

Local screenshots remain at:

- `/tmp/coffee-continuous-desktop.png`
- `/tmp/coffee-continuous-phone-320x568.png`
- `/tmp/coffee-continuous-phone-details.png`
- `/tmp/coffee-continuous-rapid-latest.png`

## Retention checkpoint

At 73.802 simulated seconds, the first score window was full.
The service retained 200 recent feed outcomes, 64 completed injections, 2,000 events, and 4,096 samples per timing category.
It reported 30,300 score rows, 535 object records, and 24 evicted injection records.
All exposed bounded collections were at or below their configured limits.

This interactive preview does not provide clean timing evidence.
Browser probes and Taras's review can overlap its runtime.
The measured 0.163x engine rate does not satisfy the real-time target and is not an acceptance benchmark.

## Pending verification

- Run three complete 60-simulated-second score windows under the exclusive runtime lock.
- Compare every sampled rolling aggregate with an independent count from the retained score rows.
- Record memory and engine cost by interval.
- Complete Taras's functional QA.
- Add the required later 3D view.

Historical defect capture passed. Historical good loss and runtime targets failed.
This increment does not retune policy, retrain the model, or change those acceptance results.
