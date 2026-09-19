# Basic scene render review

Date: 2026-09-19.
Status: Nine draft Blender stills completed. Taras owns visual acceptance.
No new animation, simulation, model change, or exporter change ran for this pass.

![All scene studies](/private/tmp/coffee-demo-video-previews/scene-review-sheet.png)

## Review surface

- Gallery: http://127.0.0.1:8894/
- Local gallery: `/private/tmp/coffee-demo-video-previews/index.html`
- Individual files: `/private/tmp/coffee-demo-video-previews/basic-scene-renders-v3/`
- Source renderer: `sim/coffee_sorter/demo_video/render_studies.py`
- Shot settings: `sim/coffee_sorter/demo_video/study_shots.json`
- Gallery source: `sim/coffee_sorter/demo_video/study_gallery.html`

Each Blender folder contains `scene.png`, `scene.blend`, `manifest.json`, and `blender.log`.
The gallery links each image to its clean original.
Six optional markers identify the two beans across three slow-motion studies.
The markers use camera projections of the recorded positions.
They do not modify the rendered image or imply pulse contact.

## The delivered scenes

| Scene folder | Source frame | Simulated time | Process seconds |
|---|---:|---:|---:|
| `01-bean-macro` | 60 | 2.000 | 16.126 |
| `02a-low-reveal-start` | 60 | 2.000 | 11.592 |
| `02b-low-reveal-end` | 60 | 2.000 | 11.235 |
| `03-overhead-inspection` | 60 | 2.000 | 13.199 |
| `04-blueprint-discharge` | 55 | 1.834 | 18.718 |
| `05a-slowmo-belt-edge` | 54 | 1.800 | 14.401 |
| `05b-slowmo-valve-on` | 55 | 1.834 | 12.842 |
| `05c-slowmo-splitter` | 57 | 1.900 | 9.202 |
| `07-warm-closing` | 60 | 2.000 | 11.830 |

The selected nine renders took 119.145 process seconds in total.
This sum excludes discarded framing studies, UI capture, and gallery preparation.
All frames use Blender 5.2.2, CPU Cycles, 24 samples, eight threads, and 960 by 540 pixels.
Motion blur remains disabled.
Each Blender process acquired the shared runtime lock before execution.

## Verification performed

- Python source compiled and the shot JSON parsed.
- All nine PNGs have the expected dimensions.
- PNG and Blender file hashes match their manifests.
- Each manifest matches its shot settings.
- Recorded position error is zero for every scene.
- Maximum quaternion component error is `1.8907454091277032e-07`, below the importer's `1e-5` tolerance.
- Every source-frame bean remains present. Counts range from 515 to 523.
- The browser loaded all ten gallery images and all six review markers.
- Visual inspection covered the individual renders and the assembled review sheet.

The initial side composition hid the selected beans behind the splitter geometry.
The final composition moves upstream and uses a documented cutaway.
It retains the actual splitter and shows the separated beans approaching its leading edge.
The macro uses a closer focus plane. The closing view uses a wider lens.

## Recording provenance

The Blender studies use the historical `sim/coffee_sorter/web/replay.json` recording.
Its simulator source revision is `340e734d06a91b589248ab6d35f20520ebab22b6`.
Its SHA-256 is `e6ed1292b2350d121ffcc0447b4aea38188818077a8254bc1d4598318b286a9f`.
Each manifest includes the source frame, object metadata, selected camera, cutaways, and asset-source hashes.
No performance or sorting-quality claim follows from these images.

## Actual UI reference

`06-ui-reference.png` is a browser screenshot from http://127.0.0.1:8892/ at 1440 by 900 pixels.
The capture shows the continuous engine and its real rolling scores at 69.7 simulated seconds.
The displayed source is `b07604b`, model `89513398373c`, and policy `2d7f41a11898`.
No injection or restart occurred during this capture.
The latest-stone card therefore has no injected result.
The available interface remains a 2D view, not the required final 3D interface.
This current UI uses a different engine source from the historical Blender recording.

## Remaining work

These are basic stills, not video segments.
The supplied assets determine the current texture detail. The concept artwork was more detailed.
The slow-motion direction proposes about 50-times slower playback across the selected 0.100-second interval.
The historical 30 Hz replay cannot resolve the short air pulse accurately.
Final slow motion needs denser pose capture and per-bean pulse-contact evidence from the engine owner.
The [slow-motion notes](SLOWMO_SHOT.md) preserve the selected object IDs and timing.
Taras can review framing now before separate animation segments proceed.
