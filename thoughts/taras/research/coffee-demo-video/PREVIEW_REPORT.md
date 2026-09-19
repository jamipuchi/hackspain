# Coffee demo preview report

Status: Three previews rendered and inspected. Animation remains blocked on Taras's review.

## Preview results

All images are 960 by 540 pixels with 24 samples and eight render threads.
Each still uses 523 recorded beans from frame 60 at 2.0 seconds.

| Preview | Visual review | Build | Render | Blender process |
|---|---|---:|---:|---:|
| Machine overview | The full machine reads clearly. The clay look is suitable for the opening form shot. | 2.595 s | 5.525 s | 10.014 s |
| Inspection close-up | Bean materials and the scan area remain visible. Recorded beans near the lens create deliberate visual density. | 2.645 s | 4.124 s | 7.719 s |
| Discharge and air-jet context | The blueprint view explains the belt end, nozzle bank, splitter, and chute. It shows no airflow. | 3.225 s | 9.551 s | 13.683 s |

The three Blender processes took 31.416 seconds in total.
These measurements include Blender startup and file saves.
The render column comes from each source manifest.

## Viewable outputs

- Machine overview: `/private/tmp/coffee-demo-video-previews/machine-overview/scene.png`
- Inspection close-up: `/private/tmp/coffee-demo-video-previews/inspection-close-up/scene.png`
- Discharge and air-jet context: `/private/tmp/coffee-demo-video-previews/discharge-air-jet/scene.png`
- Run manifest: `/private/tmp/coffee-demo-video-previews/run-manifest.json`

Each preview directory also contains `scene.blend`, `manifest.json`, and `blender.log`.

## Reproduction commands

Run all previews:

```sh
python3 sim/coffee_sorter/demo_video/render_previews.py \
  --output-dir /private/tmp/coffee-demo-video-previews
```

Run one preview:

```sh
python3 sim/coffee_sorter/demo_video/render_previews.py \
  --only machine-overview \
  --output-dir /private/tmp/coffee-demo-video-previews

python3 sim/coffee_sorter/demo_video/render_previews.py \
  --only inspection-close-up \
  --output-dir /private/tmp/coffee-demo-video-previews

python3 sim/coffee_sorter/demo_video/render_previews.py \
  --only discharge-air-jet \
  --output-dir /private/tmp/coffee-demo-video-previews
```

The wrapper records each resolved Blender command in `run-manifest.json`.
It acquires the shared nonblocking CPU lock before every Blender process.

## Manifest checks

- Replay SHA-256: `e6ed1292b2350d121ffcc0447b4aea38188818077a8254bc1d4598318b286a9f`
- Maximum position error: 0.0 metres for every still.
- Maximum quaternion component error: `1.7944581998108333e-07` for every still.
- Every image hash matches its source manifest.
- Blender reported no render error or traceback.

| Preview | PNG SHA-256 |
|---|---|
| Machine overview | `6175d3c2b9afb0b04a71c8953feee4f5ec455e21a9afbd185bd8714135d967e2` |
| Inspection close-up | `55f72c987f291f9f12ec8cebd6f3125aabf7a2e52d33e38388cdef97025df92a` |
| Discharge and air-jet context | `33d0f93ff11db221e9a52ff964dd1dcbf3a2e46605b81e1417e03096bc9aaa2e` |

## Recommendation

Animate the four-second warm-roastery overview first.
Use a small camera move while every recorded bean remains fixed at frame 60.
This proof tests camera pace without implying current engine motion.

Do not start animation until Taras accepts the stills and proposed 26-second sequence.

## Evidence boundary

The source replay predates the new live engine.
These previews cannot establish current sorting behavior, good loss, or real-time performance.
The discharge still shows machine context only.
The future UI segment must use a separate real capture from the continuous engine.
