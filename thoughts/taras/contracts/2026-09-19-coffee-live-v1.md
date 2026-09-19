# Coffee live contract v1

Owner: original demo task for this contract and the live service. Scope: one local engine session and its interface.
The parallel quality task completed its delivery. Commit `90dffc1` integrates that delivery into the original demo task.

## File ownership

| Files | Owner |
|---|---|
| `sim.py`, `scene.py`, `controller.py`, `vision.py`, `classifier.py`, `bootstrap_model.py`, `engine.py`, engine presets | Original demo task after the committed quality handoff. Preserve the completed quality checkout. |
| `live.py`, `live_web/*`, `LIVE.md`, separate language-policy files | Original demo task, `codex/coffee-core-live` |
| Overall plan, shared contract, existing demo evidence | Original demo task |
| New quality evidence and local quality plan | Parallel task, `thoughts/taras/research/coffee-quality/` |
| `export_replay.py` | Existing replay owner. Frozen until coordinated with Taras and the swarm. |
| `web/viewer.js`, `web/template.html`, `web/build_page.py` | Taras ownership confirmation pending. Frozen in this task. |
| Generated `web/index.html` | Taras is the integration owner until explicitly reassigned. No other track regenerates it. |
| Rendering assets, GLBs, cinematic scripts | Swarm. Visual acceptance remains with Taras. |

The original demo task uses `/private/tmp/hackspain-coffee-core` and port 8890.
The task named "Improve coffee sorting motion" uses `/private/tmp/hackspain-coffee-quality` and port 8891 for engine work.
Its Codex task directory remains `/Users/taras/.codex/worktrees/0833/hackspain`.
Each task edits only its own checkout and assigned files.
The original task owns subsequent additive snapshot changes. Coordinate any resumed parallel engine work before overlapping edits.
Only committed, reviewed quality changes enter the demo branch. Delivery `1944538` is integrated.

The diagnostic page uses a separate directory. Existing viewer files remain frozen until Taras confirms ownership.
The render integration source is `codex/coffee-cinematic-scene`, currently `2fa9853`.
It includes the earlier asset and moodboard work. Reuse those outputs after owner coordination.
Independent interface and offline language work can proceed before physical-quality acceptance.
Live language activation still requires the physical and language checkpoints.
The source checkout and its unpushed moodboard plan commit remain intact.

## Identity and time

- `protocol_version`: integer `1`.
- `session_id`: UUID generated when the worker starts. A process restart creates a new session.
- `seq`: increasing snapshot sequence within the session.
- `object_id`: simulation bean UID, unique within the session and never reused for recycled bodies.
- `track_id`: controller observation identity. It is separate from object identity.
- `command_id`: client UUID. Bounded v1 sessions retain the original acknowledgment for repeated IDs throughout the session, without another injection.
- Proposed continuous v2 commands also require a server-issued `command_epoch`. The epoch rules below bound replay retention.
- `sim_time_s`: seconds from this simulation's start.
- `wall_elapsed_s`: measured monotonic wall seconds since the worker started stepping.
- `policy_version`: hash of the canonical policy JSON.
- `model_version`: SHA256 of the trusted local model artifact.
- `preset_version`: hash of the canonical preset JSON.
- `appearance_key`: opaque object appearance identity. It carries no class name.

Positions use meters and the simulator axes: x along the belt, y across the belt, z upward.
Quaternions use `[w, x, y, z]`. Object poses come from physics.
Render metadata contains shape, dimensions, and color. It is never a model input.
Outcomes appear only after the simulator records `accept`, `reject`, or `spilled`.
The controller receives only camera images, measured features, and calibration/profile constants.
Evaluation associates rendered centers with components only after control finishes. It never changes control decisions or target positions.
Association is an evaluation approximation. A missing association remains unknown. Never invent a nearest-neighbor identity.

## Transport and limits

The first service binds to `127.0.0.1`. It has one worker process and one shared session.
A second browser observes the same session. It does not create another simulation.
The page connects to a same-origin WebSocket. The service rejects foreign origins and non-loopback Host headers.
HTTP health reports worker state. HTTP state and WebSocket snapshots report the same state.
Injection messages contain the session ID, command ID, and a supported class request.
The requested class selects a physical object at spawn. It never supplies a prediction or outcome.
The worker acknowledges only after spawn succeeds. Failed spawn returns an explicit error without hidden retry.
The service bounds pending commands, command IDs, clients, events, snapshots, simulation duration, and wall duration.
Concrete limits live in the preset or service constants. Shutdown joins the worker and closes the renderer.
Phase 2A provides a same-origin restart control for the current bounded session.
That control must stop the old worker before creating a new session. Preserve each session's evidence separately.
Automatic reconnect obtains the latest bounded snapshot.
An unavailable worker produces an error state. No synthetic poses or predicted outcomes replace it.

## Measurements

Record physics, camera render, detection, model inference, control, evaluation, and snapshot cost separately.
Record simulation rate, admitted throughput, browser FPS, and injection latency separately.
Acknowledgment latency starts at browser send and ends at receipt of the worker's spawn acknowledgment.
Final sorting latency starts at successful spawn and ends at the physical outcome.
Include preset, model, policy, source hashes, platform, and native thread counts with evidence.

## Next demo requirements

Taras requires a 3D live view in a later increment. The current 2D projection remains temporary.
Every successful injection must receive its own result card.
Continuous mode retains the latest 64 completed cards and bounded pending requests. The UI identifies history expiry.
The card separates the requested class, camera prediction, control decision, actual air contact, and physical outcome.
A command does not prove contact. Contact does not prove correct sorting.
The UI must label approximate associations and unavailable reasons explicitly.

The continuous-engine plan and Phase 2C need an additive engine contract after the completed quality handoff:

- Rolling quality-cohort counts, including required defects, keep objects, correct outcomes, spills, and unresolved objects.
- An explicit clock, window bounds, settling count, available duration, and warm-up state.
- The measured rejection probability and threshold for each retained decision.
- The anomaly score, anomaly threshold, and trigger status used for that decision.
- Explicit reason values and retained decision evidence for injected objects.

These fields are proposed, not implemented. Finalize their names and semantics before changing the shared snapshot.
The UI must not derive quality percentages from truncated object or event windows.
Continuous sessions do not require completion. Label rolling values as simulation measurements and include counts beside every percentage.
Keep truth in the evaluator. Do not supply these summaries as controller or model inputs.

## Proposed continuous mode

The deployed engine starts automatically and runs without connected clients. Opening a browser does not create or reset an engine.
Keep the existing bounded diagnostic mode. Continuous mode requires bounded histories and incremental evaluation before removing duration limits.
Use the score-window definition in `../plans/2026-09-19-coffee-continuous-live.md`.
The proposed default is 60 simulated seconds with a labeled 0.6-second settling delay.
Show newer objects as settling. Include older unresolved objects and spills in their applicable cohort denominators.
Background-feed scores exclude manual injections. Retain separate inspection results for manual injections.
Bound command history without imposing a lifetime injection cap.
Proposed v2 epochs last 60 wall seconds. Retain acknowledgments from the current and previous epoch, with at most 64 admitted commands per epoch.
Retain bounded pending commands until their result arrives. Do not evict an unresolved command to admit another request.
Reject expired epochs with `command_epoch_expired` and no spawn. A stale duplicate cannot become a new injection after acknowledgment eviction.
These epoch rules replace session-long replay retention only in continuous v2. They do not alter current bounded v1 behavior.
Visitors do not receive an administrative restart control. Local diagnostics retain the existing restart control.
Continuous mode and these additive fields are not implemented in v1 yet.
