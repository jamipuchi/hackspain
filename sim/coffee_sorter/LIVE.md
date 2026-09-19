# Coffee live engine checkpoint

Taras owns functional QA and acceptance. The local page observes one shared Python engine.
The existing replay viewer and swarm assets remain unchanged.

## Run from a fresh checkout

Use Python 3.13, which was used for the recorded local measurements.
Run these commands from the repository root:

```bash
python3.13 -m venv .venv-coffee
source .venv-coffee/bin/activate
python -m pip install -r thoughts/taras/research/coffee-quality/requirements-resolved.txt
python - <<'PY'
from pathlib import Path
import gzip

source = Path('thoughts/taras/research/coffee-quality')
target = Path('sim/coffee_sorter/models')
target.mkdir(exist_ok=True)
for suffix in ('joblib', 'manifest.json'):
    content = gzip.decompress((source / f'model-selected.{suffix}.gz').read_bytes())
    output = target / f'live_green_arabica.{suffix}'
    if output.exists() and output.read_bytes() != content:
        raise SystemExit(f'Preserve your existing artifact before replacing {output}')
    output.write_bytes(content)
PY
python sim/coffee_sorter/live.py --host 127.0.0.1 --port 8890 --preset sim/coffee_sorter/configs/continuous_demo.json
```

Open [the local page](http://127.0.0.1:8890). The conveyor starts automatically, including when no browser is connected.
Select **Inject stone** to add an object. A ring identifies that object in both projections.
Prediction, jet contact, and physical outcome appear separately.
The contact counter counts nozzle contact steps. Multiple nozzles can contact an object during one physics step. Approximate object associations carry an explicit label.
The page draws schematic primitive projections. It does not provide the swarm's realistic assets or a complete 3D viewer.

The service prints its evidence directory. Command logs rotate in continuous mode. Shutdown writes the retained report and final state.
The command above restores the exact evaluated model. Continuous startup validates its manifest and physical compatibility without retraining.
The artifact used seed 7 for training and separate seed-9 objects for holdout observations. Anomaly statistics use training objects only.

## Continuous operation and rolling scores

The default score window covers 60 simulated seconds, after a 0.6-second settling allowance.
At simulation time `T`, the cohort contains spawn times in `(T - 0.6 - 60, T - 0.6]`.
Scores remain unavailable until their denominators contain objects. The page labels warm-up and shows the available duration.
Newer feed objects appear in the settling count. Manual injections stay outside all feed scores and settling counts.

Sorting accuracy counts required defects rejected and keep objects accepted.
Defect capture counts required defects rejected. Good loss counts keep objects rejected or spilled.
Spills and unresolved objects remain in the relevant denominators. Each score shows its numerator and denominator.
These values describe rolling simulation outcomes. They do not replace the frozen acceptance evaluation or measure classifier confidence.

The service updates score aggregates once per wall second. Pose updates remain separate.
The engine continues with zero browsers. Reconnecting preserves the session and current scores.
Each 60-second wall-time command epoch admits at most 256 injections, with at most 16 pending commands.
Exact retries retain their original command and epoch. An expired unknown epoch returns an error without spawning an object.
The interface hides visitor restart in continuous mode. Stop and restart the process when an administrative reset is necessary.
That reset creates a new engine session and starts score warm-up again.

Continuous history retains active objects, bounded recent outcomes, and 64 completed injection records.
The page identifies evicted injection history. `/state` exposes retention counts and limits for inspection.
The browser retains up to 64 request records and preserves every pending payload exactly.
The latest-stone card follows click order. It separates prediction, controller action, air contact, physical outcome, and command failures.
The main view fits one desktop or mobile viewport. Detailed diagnostics use the **Details** dialog.

## Bounded diagnostic behavior

Use the original preset when a reproducible short session or UI restart is needed:

```bash
.venv-coffee/bin/python sim/coffee_sorter/live.py --port 8892 --preset sim/coffee_sorter/configs/default_demo.json --out /tmp/coffee-bounded
```

Open `http://127.0.0.1:8892`. The first injection starts this bounded session.

- One worker serves one shared session, with up to four browser clients.
- The preset requests 500 objects per simulated second with inspection at 250 Hz.
- The default uses 400 ellipsoid bodies, plus the existing half, box, and capsule pools.
- The session ends after 10 simulated seconds or 300 wall seconds.
- A maximum of 64 unique injection commands limits retained command state.
- Repeated command IDs return the original result. They do not create another object.
- Reconnect obtains the current snapshot and retained command results.
- Snapshots contain the latest 50 events. The page retains six events for the selected object.
- Reports retain up to 2,000 event records and full-session event counters. They label any truncated event window.
- **Restart session** stops the old worker and creates a fresh session for every connected browser.
- Restart clears the page's selected object and commands. The prior session's evidence remains on disk.
- Injection stays disabled while the engine restarts. A page reload alone does not reset the session.
- The footer identifies the engine source revision, session, model, policy, and preset.
- Ctrl+C stops the service and its worker.

The service binds only to loopback and rejects foreign origins.
For an accepted later SSH installation, forward its loopback port with:

```bash
ssh -N -L 8890:127.0.0.1:8890 hackspain
```

This task did not deploy a service or change DNS on `hackspain`.
Vercel and HTTPS/WSS remain a later phase.

## Inspect the exact preset

The same `Engine` class drives the page and the diagnostic command:

```bash
source .venv-coffee/bin/activate
python sim/coffee_sorter/engine.py --preset sim/coffee_sorter/configs/default_demo.json --seconds 2 --out /tmp/coffee-pool400
python sim/coffee_sorter/engine.py --preset sim/coffee_sorter/configs/default_demo_pool1150.json --seconds 2 --out /tmp/coffee-pool1150
```

Run these commands sequentially. Stop the live engine first to avoid contention.
These short runs use development seed 8 and are diagnostic evidence, not acceptance runs.
The parallel quality task completed its frozen evaluation on seeds 111, 112, and 113. These seeds are now exposed.
Commit `90dffc1` integrates that task's engine changes. Its exact selected model accompanies the evaluation evidence.

The quality cohort uses the closed spawn-time interval `[0.8, end - 0.6]` in simulated seconds.
Capture counts required defects in reject. Good loss counts keep objects rejected or spilled.
Spilled and unresolved objects remain in denominators.
The report partitions missed defects and distinguishes own-pulse contact from other jet contact.
Attribution labels describe observed stages. They do not establish causal mechanisms.

## Known limits

The engine runs slower than real time on the measured Mac. The page displays its actual simulation rate.
Browser FPS measures display callbacks. Pose packets arrive at up to 10 Hz and do not establish engine speed.
Injection acknowledgment measures browser send through receipt of successful worker acknowledgment.
Time to outcome uses worker timestamps from physical spawn through observed outcome.
HTTP encoding and send waits appear in `service-profile.json`. Remaining IPC and scheduling cost stays in the unattributed wall residual.

The first demonstrated stone was classified as stone, received rejection commands, and spilled without jet contact.
A functioning transport does not establish successful sorting.
The later capsule-placement correction removes excess stick spawn height. Other objects still bounce after collisions.
The short motion diagnostic does not establish smoother overall physics or acceptable sorting quality.
The frozen evaluation met the 80% capture lower bound on every seed. Every seed exceeded the 2% good-loss upper bound.
Pooled good loss was 5.80%. Engine speed remained approximately 0.2 times real time. Lighting robustness remains unsupported.
The latest-stone card and rolling scores are diagnostic aids. They do not establish acceptable sorting quality.
The current two-dimensional projections are temporary. A later increment provides a 3D view using the existing render assets.
Language policies and learning controls remain deferred.

See the checkpoint report under `thoughts/taras/research/coffee-core-live/` for the measured configuration, source hashes, and evidence.

## Bounded restart checkpoint for Taras

Open the page and select **Restart session**. Wait until the status shows **Ready**.
The session ID in the footer must change. All open browsers must show the same new ID.
Select **Inject stone** after restart. The server must acknowledge its physical spawn.
Restart also works after the bounded session completes. You do not need a terminal command for each run.

Restart uses `POST /restart` with the current `session_id` and the same browser origin.
The service rejects stale requests and simultaneous restarts.
This shared-session control resets the engine for every connected browser.

## Remaining continuous verification

Three full score windows still need a controlled endurance run and an independent count comparison.
Taras's functional QA remains separate. Public deployment, supervision, and the required 3D view remain later work.

## Use the evaluated model

To reproduce the integrated quality configuration, restore its exact artifact instead of retraining:

```bash
gzip -dc thoughts/taras/research/coffee-quality/model-selected.joblib.gz > sim/coffee_sorter/models/live_green_arabica.joblib
gzip -dc thoughts/taras/research/coffee-quality/model-selected.manifest.json.gz > sim/coffee_sorter/models/live_green_arabica.manifest.json
shasum -a 256 sim/coffee_sorter/models/live_green_arabica.joblib
```

The expected SHA-256 is `89513398373c6e0e81286419962feb3e312742de14a76d02dd0d819ad5264a5a`.
Back up any existing local model before replacement. Continuous startup enforces the adjacent manifest before the worker starts.
For exact evaluation reproduction, validate `freeze.json` as described by the quality report before starting the service.
