# Basic scene render review

Date: 2026-09-19.
Status: Nine scene studies and three matched mode variants completed. Taras owns visual acceptance.
Taras will record the UI separately. The delivered gallery contains only Blender stills.
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
Two comparison controls preview wipes between aligned modes.
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

## Matched modes for transitions

Taras requested several modes for selected scenes, including blueprint-to-normal transitions.
Three additional renders complete two matched groups:

| Added variant | Matches | Process seconds |
|---|---|---:|
| `04b-normal-discharge` | `04-blueprint-discharge` | 13.772 |
| `07b-blueprint-closing` | `07-warm-closing` | 18.065 |
| `07c-normal-closing` | `07-warm-closing` | 12.435 |

The additional renders took 44.272 process seconds.
All twelve selected renders took 163.417 process seconds in total.
The variant batch waited for the shared endurance run to release its runtime lock.
It did not interrupt that simulation.

Within each group, only the title and look differ in the shot settings.
The source frame, camera, lens, focus, geometry, and cutaway settings match.
The manifests also contain identical source-frame poses, bean counts, replay hashes, and hidden-object lists within each group.
Lighting and materials follow the selected look.
The gallery shows blueprint-to-normal wipes for both groups.
The closing comparison also supports clay-to-normal.
These controls preview a transition between stills. They do not establish alignment across future animation frames.

## Verification performed

- Python source compiled and the shot JSON parsed.
- All twelve PNGs have the expected dimensions.
- PNG and Blender file hashes match their manifests.
- Each manifest matches its shot settings.
- Recorded position error is zero for every scene.
- Maximum quaternion component error is `1.8907454091277032e-07`, below the importer's `1e-5` tolerance.
- Every source-frame bean remains present. Counts range from 515 to 523.
- The browser loaded all gallery images and all six review markers.
- A slider keypress changed the wipe from 50% to 51%. The clip boundary changed accordingly.
- The closing mode button changed the overlay to clay and updated its label.
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

## UI recording excluded

Taras requested a separate UI recording after the first review sheet was assembled.
The final gallery therefore excludes the UI.
An earlier local screenshot remains as an unused reference. No injection or restart occurred during that capture.

## Remaining work

These are basic stills, not video segments.
The supplied assets determine the current texture detail. The concept artwork was more detailed.
The slow-motion direction proposes about 50-times slower playback across the selected 0.100-second interval.
The historical 30 Hz replay cannot resolve the short air pulse accurately.
Final slow motion needs denser pose capture and per-bean pulse-contact evidence from the engine owner.
The [slow-motion notes](SLOWMO_SHOT.md) preserve the selected object IDs and timing.
Taras can review framing now before separate animation segments proceed.
