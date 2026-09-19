# Continuous coffee interface, version 2

This contract implements the approved [continuous plan](../plans/2026-09-19-coffee-continuous-live.md).
It fixes interfaces for parallel implementation. Taras owns functional QA.
The diagnostic preset and protocol version 1 retain their bounded behavior.

## Ownership

All worktrees start from the same published main revision containing this contract.
The root task integrates changes and owns plans, contracts, evidence, and commits.

| Scope | Assigned executor |
|---|---|
| `engine.py`, `sim.py`, `controller.py`, a rolling-score module, focused engine tests | Sol engine task |
| `live.py`, `configs/continuous_demo.json`, startup artifact validation, focused service tests | Sol service task |
| `live_web/index.html`, `live_web/live.js` | Terra interface task |

The root task confirms ownership of these live interface files through the existing live contract.
The swarm retains `visual_assets/*`. Existing `web/*`, replay exports, and render assets remain outside this increment.
Do not modify another task's files without coordination. Do not change model training or physical policy.

## Preset and lifecycle

The continuous preset copies the diagnostic physical settings exactly.
It adds `mode: "continuous"` and `score_window_seconds: 60.0`.
Its `limits.max_sim_seconds` and `limits.max_wall_seconds` are null.
Other retention limits remain explicit. The engine exposes `continuous` as a boolean attribute.
Only continuous mode starts automatically and bypasses duration limits.
The engine continues with zero browser connections. It never restarts to clear scores or counters.
The HTTP service remains loopback-only. This increment does not deploy a public service.

Validate model bytes against the adjacent manifest before continuous startup.
Validate classes, features, profile, physical layout, feed rate, and camera cadence against the selected model provenance.
Do not compare live-only duration, window, or preset names as training inputs. Do not retrain implicitly.
Source changes remain visible in engine provenance. They do not silently rewrite the historical acceptance freeze.

## State

Continuous snapshots use `protocol_version: 2` and `mode: "continuous"`.
The engine keeps existing object, pose, event, identity, and version fields.
`Engine.rolling_scores()` returns the aggregate below. The service calls it at most once per wall second.
The service caches that aggregate separately from poses, which retain their existing publication cadence.

```json
{
  "schema_version": 1,
  "clock": "simulation",
  "score_epoch_id": "current-engine-session-uuid",
  "as_of_sim_time_s": 12.0,
  "window_seconds": 60.0,
  "settling_seconds": 0.6,
  "window_start_exclusive_s": -48.6,
  "window_end_inclusive_s": 11.4,
  "available_seconds": 11.4,
  "warming_up": true,
  "manual_injections_excluded": true,
  "settling_objects": 300,
  "eligible_objects": 5700,
  "sorting_accuracy": {"numerator": 5300, "denominator": 5700, "value": 0.9298245614},
  "defect_capture": {"numerator": 450, "denominator": 500, "value": 0.9},
  "good_loss": {"numerator": 250, "denominator": 5200, "value": 0.0480769231},
  "unresolved": {"numerator": 120, "denominator": 5700, "value": 0.0210526316},
  "versions": {"model": "sha256", "policy": "sha256", "source_revision": "git-sha"}
}
```

The service publishes this object as `rolling_scores` in state packets and `/state`.
Numbers above illustrate the schema. Production values come only from evaluator counts.
Use null values for empty denominators. Preserve the exact spawn-time cohort from the plan.
Manual injections never enter feed counts, settling counts, or score denominators.
Objects without an outcome remain in their applicable denominators until their spawn time leaves the window.
Changing model or policy requires a new score epoch. This increment does not support model or policy changes during a session.

Continuous snapshots include `retention` with actual counts and explicit limits.
Retain active physical objects, at most 200 recent resolved feed objects, 64 completed injection records, and 2000 recent events.
Retain at most 4096 timing samples per category. Preserve cumulative scalar totals where reports need them.
Bound per-object decision evidence and track associations. Retire completed tracks and consumed physical events.
Compact score rows may span only the score window plus settling time. They must not retain full simulation objects.
Include `injection_history_evicted` as a cumulative count. Keep active injections until physical resolution.
Mark overdue injections explicitly without inventing a physical outcome.
Diagnostic mode retains its existing detailed report and acceptance cohort.

## Commands and connection recovery

Continuous state includes `command_epoch`, a server-issued opaque string, and `command_epoch_seconds: 60`.
The epoch advances by wall time. The HTTP service publishes changes even without new pose packets.
Version 2 injection payloads contain `command_epoch` alongside the existing session UUID, command UUID, and class name.
Store the full original payload for retries. Never replace an old epoch with a new one during recovery.

Admit at most 256 unique commands per epoch and 16 pending commands overall.
Retain completed results for the current and previous epoch. Never evict pending requests.
Accept new requests only in the current epoch. Return retained duplicate results from either retained epoch.
An expired, unknown epoch returns `error_code: "command_epoch_expired"` and creates no object.
An existing command UUID with a different payload returns a conflict and creates no object.
The worker retains no lifetime command-result dictionary in continuous mode.
Rotate command logs at 1 MiB with two backups. Bound restart evidence retention in continuous mode if restart is enabled.

The browser detects stale connections through an application heartbeat or equivalent explicit liveness check.
It retries the same pending payload after reconnection. Server acknowledgment recovery must not create duplicate objects.
A new engine session invalidates pending commands visibly. A reconnect alone preserves the session and score epoch.
Every browser sees the same score aggregate and engine session.
The browser retains at most 64 request records and at most 16 pending requests.
Completed records can leave the client history before a new request is added. Pending records cannot be evicted.

## Interface and restart

Render server aggregates directly. Never calculate sorting scores from visible poses or truncated event lists.
Show counts beside percentages, simulated seconds, warm-up, settling count, and manual-injection exclusion.
Keep engine speed and browser FPS separate. Show unavailable scores when denominators are empty.
Show one prominent latest-stone card above the conveyor. Its headline follows click order instead of acknowledgment order.
Use In progress, Rejected, Spilled, and Passed for physical states. Show command failure when no object was added.
Keep prediction, controller action, air contact, physical outcome, and command failure as separate evidence.
Clear request state, selection, and selected events when the engine session changes.
Fit the main view in one desktop or mobile viewport without page scrolling or horizontal overflow.
Keep the injection button, latest card, conveyor, compact scores, counts, warm-up, and engine failure visible.
Place detailed diagnostics in an on-demand panel with its own scrolling area.
Show injection-history eviction explicitly. The 3D increment remains in the parent plan.

Continuous mode publishes `restart_supported: false` by default and rejects visitor restart requests.
The local operator restarts the process when needed. Bounded diagnostic mode retains its existing restart route.
Do not add authentication or a public administrative endpoint in this increment.

## Verification

Use synthetic streams to verify cohort boundaries, delayed outcomes, unresolved objects, spills, manual exclusions, and expiry.
Use fake engine service tests for epochs, duplicate requests, retained acknowledgments, idle connections, and bounded queues.
Run no simultaneous physics measurements. The root task schedules real engine checks after integration.

```bash
cd /Users/taras/Documents/code/hackspain
.venv-coffee/bin/python -m py_compile sim/coffee_sorter/engine.py sim/coffee_sorter/sim.py sim/coffee_sorter/controller.py sim/coffee_sorter/live.py
node --check sim/coffee_sorter/live_web/live.js
```

## Manual E2E

```bash
cd /Users/taras/Documents/code/hackspain
.venv-coffee/bin/python sim/coffee_sorter/live.py --port 8892 --preset sim/coffee_sorter/configs/continuous_demo.json --out /tmp/coffee-continuous-v2
# In another terminal:
curl --fail http://127.0.0.1:8892/health
curl --fail http://127.0.0.1:8892/state
```

Taras opens `http://127.0.0.1:8892` and checks that the conveyor already moves.
Inject one stone. Check its acknowledgment, camera decision, air contact, and physical outcome.
Disconnect for one minute. Reconnect and check that the same engine session continued.
Confirm that old feed objects leave the score window without a restart.
