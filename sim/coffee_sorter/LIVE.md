# Coffee live engine checkpoint

Taras owns functional QA and acceptance. This increment provides a local diagnostic page and one stateful Python engine.
The existing replay viewer and swarm assets remain unchanged.

## Run from a fresh checkout

Use Python 3.13, which was used for the recorded local measurements.
Run these commands from the repository root:

```bash
python3.13 -m venv .venv-coffee
source .venv-coffee/bin/activate
python -m pip install -r sim/coffee_sorter/requirements.txt
python sim/coffee_sorter/bootstrap_model.py
python sim/coffee_sorter/live.py --host 127.0.0.1 --port 8890 --preset sim/coffee_sorter/configs/default_demo.json
```

Open [the local page](http://127.0.0.1:8890), then select **Inject stone**.
The first injection starts the simulation. A ring identifies the injected object in both projections.
Prediction, jet contact, and physical outcome appear separately. Approximate object associations carry an explicit label.
The page draws schematic primitive projections. It does not provide the swarm's realistic assets or a complete 3D viewer.

The service prints its evidence directory. It writes `commands.jsonl`, `report.json`, `final-state.json`, and `service-profile.json` there.
The model bootstrap takes approximately 25 seconds on the measured Mac. It validates provenance before reusing an existing model.
Training uses seed 7. Holdout observations come from separate seed-9 objects. Anomaly statistics use training objects only.
The bootstrap score describes observation-weighted classifier output. It excludes anomaly policy and physical sorting outcomes.

## Session behavior

- One worker serves one shared session, with up to four browser clients.
- The preset requests 500 objects per simulated second with inspection at 250 Hz.
- The default uses 400 ellipsoid bodies, plus the existing half, box, and capsule pools.
- The session ends after 10 simulated seconds or 300 wall seconds.
- A maximum of 64 unique injection commands limits retained command state.
- Repeated command IDs return the original result. They do not create another object.
- Reconnect obtains the current snapshot and retained command results.
- Snapshots contain the latest 50 events. The page retains six events for the selected object.
- Reports retain up to 2,000 event records and full-session event counters. They label any truncated event window.
- Ctrl+C stops the service and its worker. Restart the command to reset the session.

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
Seeds 111, 112, and 113 remain reserved for later acceptance measurements.

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
The 80% capture and 2% good-loss targets remain unproven. Lighting robustness remains unsupported.
Reset through the page, a full object inspector, language policies, and learning controls remain deferred.

See the checkpoint report under `thoughts/taras/research/coffee-core-live/` for the measured configuration, source hashes, and evidence.
