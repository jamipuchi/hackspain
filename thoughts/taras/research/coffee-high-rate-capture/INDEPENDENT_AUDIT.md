# Independent high-rate capture audit

Date: 2026-09-19.
Status: Passed for the slow-motion render handoff.

This review recalculated the capture results from the raw replay, manifest,
and partial JSONL files. It did not read `validation.json` as evidence.
The result supersedes the pending independent-review flag in the author report.

The local audit record is:

`/private/tmp/coffee-demo-video-previews/high-rate-capture/independent-audit.json`

Its SHA-256 is
`06f52dc56c1b52ad4a2fa1ced4483ad58bea8a68f50fd0d72bba5b4039f5c5ab`.

The large replay artifacts remain outside Git. The primary replay is:

`/private/tmp/coffee-demo-video-previews/high-rate-capture/fresh-current-main/replay.json`

Its SHA-256 is
`9193cbd739134b6678c6c5e39bb6db87d0cf717b9d5cd83bd8f183e82f9dfa16`.
The reproduction replay is:

`/private/tmp/coffee-demo-video-previews/high-rate-capture/fresh-current-main-reproduction/replay.json`

Its SHA-256 is
`0d41593511d032d819e7fd0acc1395944099a37a21e1b298be4c1031f92555ec`.

## Provenance

Both replays identify source revision
`44609edd63b456524d0dc100404417193023e668`.
The six recorded source-file hashes match that Git revision.
The model hash is
`0b9e164cbf4df18f904f30fde38e0b7ed352877653849cc55da25e6f143051c8`.
The policy hash is
`e1f84378ba7e7c79aaecf8aec0eaa9b725e086b6ec1106a9e3e2ca05810bed04`.
The preset hash is
`a5dfed6525eb9894e8ed2a4ba1bb64092116de09292d8521ac6bd12250946469`.

The fixed preset uses seed 8, 1,000 objects per simulated second, and 0.06 N jets.
The physics timestep is 0.002 seconds. The sample rate is 500 Hz.

The primary run used `Inspector`, `Controller`, and fresh model evaluation.
It has no control-replay hash or recorded valve-schedule hash.
The reproduction hashes the primary replay and reuses its control schedule.
It samples new physics. It does not evaluate the classifier again.

The failed historical attempt used revision `340e734d` and seed 7.
It timed out before it wrote a replay.
The later historical reproduction also used revision `340e734d`.
Neither historical attempt supports the fresh capture claims.

## Recalculated results

Both replays contain 326 timestamps from 1.400 through 2.050 seconds.
Every interval is 0.002 seconds within floating-point representation error.
The comparison covered 170,125 object poses.

All encoded position, quaternion, and decision values match exactly.
All counters also match. The 410 fire commands match exactly.
The 1,736 normalized decisions match exactly.
The 557 physical contact rows match exactly.

The quaternion order is `w,x,y,z`.
The maximum encoded quaternion norm error is
`8.106671409402466e-05`. The quantization tolerance is `0.0002`.

The stored object axes are semiaxes. Renderers must double them for full size.
Black bean 1261 is 9.14 by 6.80 by 4.42 millimetres at full size.
Good bean 1256 is 8.80 by 7.44 by 4.62 millimetres at full size.

Black bean 1261 received a black, reject decision at 1.558 seconds.
Pulse 289 targeted that object. Contacts occurred at 1.628 and 1.630 seconds.
The object received a reject outcome at 1.710 seconds.

Good bean 1256 received a good, accept decision at 1.566 seconds.
It has no pulse contact. It received an accept outcome at 1.714 seconds.

The render interval is `[1.478, 1.764)` simulated seconds.
It contains 143 samples. At 30 frames per second, it lasts 4.7667 seconds.
This playback is 16.6667 times slower than simulation time.
Only 0.136 simulated seconds remain after the first contact.

## Verification method

The review parsed both replay files independently.
It compared frame timestamps, object IDs, encoded poses, counters, and contacts.
It normalized the primary decisions to the reproduction decision schema.
It compared the complete fire schedule and recalculated the render interval.
It also matched source-file and model bytes against the pinned Git revision.

Verify the artifact hashes with:

```bash
shasum -a 256 \
  /private/tmp/coffee-demo-video-previews/high-rate-capture/fresh-current-main/replay.json \
  /private/tmp/coffee-demo-video-previews/high-rate-capture/fresh-current-main-reproduction/replay.json \
  /private/tmp/coffee-demo-video-previews/high-rate-capture/independent-audit.json
```

Run the packaged validator separately with:

```bash
sim/coffee_sorter/.venv/bin/python sim/coffee_sorter/validate_high_rate_capture.py \
  --replay /private/tmp/coffee-demo-video-previews/high-rate-capture/fresh-current-main-reproduction/replay.json \
  --source-replay /private/tmp/coffee-demo-video-previews/high-rate-capture/fresh-current-main/replay.json \
  --output /tmp/high-rate-validation.json \
  --object-id 1261 --object-id 1256
```

## Reproduction prerequisites

The replay files are too large for Git. Preserve the local paths and hashes above.
An exact regeneration needs the repository at revision `44609edd`.
It also needs the recorded model, MuJoCo 3.13.0, NumPy, and scikit-learn.
The capture command acquires `/private/tmp/hackspain-coffee-runtime.lock`.
Create the tested environment from `sim/coffee_sorter/requirements.txt` first.

Create a detached source worktree. Then run the new capture tool from the current tree:

```bash
git worktree add --detach /private/tmp/hackspain-capture-source \
  44609edd63b456524d0dc100404417193023e668

sim/coffee_sorter/.venv/bin/python sim/coffee_sorter/capture_high_rate.py \
  --sim-dir /private/tmp/hackspain-capture-source/sim/coffee_sorter \
  --model /private/tmp/hackspain-capture-source/sim/coffee_sorter/runs/generalization/green_arabica/model/green_arabica.joblib \
  --seconds 2.05 --capture-start 1.4 --capture-end 2.05 \
  --rate 1000 --jet-force 0.06 --seed 8 \
  --output /private/tmp/coffee-demo-video-previews/high-rate-capture/fresh-current-main/replay.json
```

Add `--control-replay` with the primary replay path to reproduce the second run.
Use a new output directory when the recorded artifacts must remain immutable.

## Limits

Zero pose deltas apply to quantized values.
Position resolution is 0.1 millimetres.
Quaternion-component resolution is `0.0001`.
This evidence does not prove equal unquantized internal state.

Air contact means that an object satisfied the simulator force predicate.
It is not a hardware sensor measurement.
The primary run supports classifier and controller event claims.
The reproduction supports deterministic encoded motion under recorded control.
It does not provide a second classifier evaluation.
