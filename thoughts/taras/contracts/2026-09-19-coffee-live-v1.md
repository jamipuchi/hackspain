# Coffee live contract v1

Owner: Codex core task. Scope: one local engine session and a separate diagnostic page.

## File ownership

| Files | Owner |
|---|---|
| `vision.py`, `sim.py`, `controller.py`, `classifier.py` | Codex core task |
| New `engine.py`, `live.py`, `bootstrap_model.py`, `configs/default_demo.json`, `live_web/*` | Codex core task |
| Plan, contract, and engine evidence | Codex core task |
| `export_replay.py` | Existing replay owner. Frozen until coordinated with Taras and the swarm. |
| `web/viewer.js`, `web/template.html`, `web/build_page.py` | Taras ownership confirmation pending. Frozen in this task. |
| Generated `web/index.html` | Taras is the integration owner until explicitly reassigned. No other track regenerates it. |
| Rendering assets, GLBs, cinematic scripts | Swarm. Visual acceptance remains with Taras. |

The diagnostic page uses a separate directory. It does not modify the existing viewer or merge asset work.
The source checkout and its unpushed moodboard plan commit remain intact.

## Identity and time

- `protocol_version`: integer `1`.
- `session_id`: UUID generated when the worker starts. A process restart creates a new session.
- `seq`: increasing snapshot sequence within the session.
- `object_id`: simulation bean UID, unique within the session and never reused for recycled bodies.
- `track_id`: controller observation identity. It is separate from object identity.
- `command_id`: client UUID. Repeated IDs return the original acknowledgment without another injection.
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
The first increment resets through service restart. Automatic reconnect obtains the latest bounded snapshot.
An unavailable worker produces an error state. No synthetic poses or predicted outcomes replace it.

## Measurements

Record physics, camera render, detection, model inference, control, evaluation, and snapshot cost separately.
Record simulation rate, admitted throughput, browser FPS, and injection latency separately.
Acknowledgment latency starts at browser send and ends at receipt of the worker's spawn acknowledgment.
Final sorting latency starts at successful spawn and ends at the physical outcome.
Include preset, model, policy, source hashes, platform, and native thread counts with evidence.
