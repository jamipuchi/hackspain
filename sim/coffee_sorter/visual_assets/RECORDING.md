# Recorded-motion and browser reuse proof

Deliverable B replays the shipped MuJoCo recording through the detailed bean assets. It is a one-second review excerpt, not the full film. Only this `visual_assets/` directory changes. No simulation, classifier, inspection-camera material, controller, exporter, live viewer, backend or deployment changes are included.

- [One-second MP4](recording/coffee-one-second.mp4), [first frame](recording/frame_001.png), [render manifest and original events](recording/manifest.json).
- [Browser prototype manifest and byte counts](browser/manifest.json), [local instancing proof](browser/proof.html).
- [Detailed library and original stills](README.md).

## Sampling and truth

The immutable input is `../web/replay.json`, SHA-256 `e6ed1292b2350d121ffcc0447b4aea38188818077a8254bc1d4598318b286a9f`. Its recorded simulator revision is `340e734d06a91b589248ab6d35f20520ebab22b6`, MuJoCo 3.13.0, simulation seed 7, 1,000 beans/s and 0.06 N valve force. Source-file hashes, trained-model SHA-256 and the complete policy values are copied verbatim to the manifest. The original exporter did not save a wall-clock capture date or exporter hash; these cannot be recovered honestly. Simulation timestamps are present. New renders record their UTC start time, Blender version, script revision and exact script hashes.

**The shipped replay is sampled near 30 Hz.** Its 2 ms simulation step produces alternating 32/34 ms pose intervals. This proof uses the 30 existing samples in `[2.000, 3.000)` s, starting at 2.000 s and ending at 2.968 s. The MP4 presents those samples at exactly 30 fps for exactly 1.000 s. Presentation differs from the source timestamps by at most 1.334 ms. The exact timestamps and source indices are in `frames[]`; no poses are interpolated, extrapolated, retimed for slow motion or re-simulated. Motion blur is disabled because intermediate physical trajectories were not recorded.

All active UIDs are loaded, even those outside the camera view. A bean appears only when its row exists in that source frame. Its class, decision, spawn time, final recorded outcome and resolution time are preserved; Blender does not calculate an outcome. Final outcomes may occur outside the excerpt. No acceptance-rate claim is inferred from the crop. Evaluated object positions and quaternion rotations are audited on every rendered frame, along with active UID counts. Row hashes and unchanged replay SHA protect the input contract.

`fires` contains the unmodified `[onTime, offTime, nozzle, force]` events overlapping the excerpt; `decisions` preserves the decision events inside it. Small cyan blocks at nozzle sites indicate ON only when `onTime <= sampleTime < offTime`. They are visual indicators, not air-flow physics. A 3–12 ms pulse can fall entirely between near-30 Hz samples and be absent from the images. The manifest preserves it anyway. No pulse is lengthened to make it visible. The camera can also occlude indicators.

The good, black, insect and broken classes use the new detailed assets. Faded, sour, shell and husk retain their recorded identities/dimensions but use the good silhouette with class-colour stand-ins; stone is a box and stick is a local-Z capsule. The recording does not contain each bean's original randomized material variant, so this is presentation shading, not reconstruction of inspection pixels. Machine geometry uses the exported dimensions, positions and rotations; presentation materials are independent of the camera pipeline. Rollers use their exported static pose because their animation was not recorded.

## Simulator to Blender mapping

Both spaces are right-handed, in **metres**, with **+Z up**, **+X along the belt toward discharge**, and **Y across the belt**. Blender scene unit scale is 1. There is no axis swap, translation offset or global scale.

For the exported row `uid,x,y,z,qw,qx,qy,qz,decision`:

```text
position_m = (x, y, z) / 10000
rotation = normalize(Quaternion(qw, qx, qy, qz) / 10000)
world_vertex = position_m + rotation @ (instance_scale * prototype_vertex_m)
```

Blender and MuJoCo both accept quaternion components in `w,x,y,z` order. Three.js uses `x,y,z,w` at its setter. Normalization corrects export quantization without inventing angular motion. Export resolution is 0.1 mm for position (rounding error at most 0.05 mm/component), 0.0001/component for quaternion, and 0.01 mm for dimensions (at most 0.005 mm/component).

Metadata is `uid,class,ax,ay,az,spawnTime,outcome,resolvedTime`. Decode dimensions by dividing by 100000:

| Shape | Meaning and Blender scale |
| --- | --- |
| Regular bean / ellipsoid | Recorded semi-axes divided componentwise by prototype semi-axes `(0.0049, 0.00355, 0.00255)` m. Body origin stays at its centre. |
| Broken / half | These are **AABB half-extents**, not original mesh scale. Scale is `2 * recorded_axes / (0.0100, 0.007183224, 0.002567526)` m. Keep the cut-plane origin at local `z=0`, with the mesh extending into +Z. Do not centre its bounding box. |
| Box | A size-2 unit cube scaled by recorded semi-axes. |
| Capsule | `ax` is the half-cylinder length, `ay` the radius. Build radius `ay` hemispheres separated by a local-Z cylinder of length `2*ax`. The recorded body quaternion already includes its lying-down rotation; do not rotate it again. |

The broken prototypes inherit the original `half_bean.obj` frame. MuJoCo's internal mesh centring/inertia transform is already compensated in its mesh geom; it is not an extra body transform to apply to the original OBJ. Recorded HALF axes are rounded, so reconstructed extents can differ from the pre-export originals by up to 0.005 mm/component. The replay is the authority for this proof. No `rest_on()` adjustment, re-centring, collision solver, rigid-body cache or dynamics step runs in Blender.

Machine box `size` values are half-dimensions; cylinder `size` is radius and half-length. Machine `quat` is already unquantized `w,x,y,z`. Nozzle Y is `-belt_w/2 + belt_w/n_nozzles*(index+0.5)`, X is `ej_x`, Z is `belt_z+ej_z_offset`.

## Reproduce this proof

Use the [Blender 4.5.4 LTS setup](INSTALL.md) and an installed FFmpeg with libx264. Run from the repository root. All Blender jobs are sequential and capped at 16 render threads. No external GPU service is used.

```bash
export BLENDER=/workspace/personal/tools/blender-4.5.4-linux-x64/blender
export LD_LIBRARY_PATH=/workspace/personal/tools/blender-libs/root/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
export OMP_NUM_THREADS=16 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1

# Optional cheap single-frame framing check, in a separate directory.
"$BLENDER" --background --threads 16 --python-exit-code 1 \
  --python sim/coffee_sorter/visual_assets/render_recording.py -- \
  --preview --output-dir /tmp/coffee-proof-preview

# Exactly one second from the existing recording; no simulation imports.
"$BLENDER" --background --threads 16 --python-exit-code 1 \
  --python sim/coffee_sorter/visual_assets/render_recording.py
python3 sim/coffee_sorter/visual_assets/encode_recording.py

# Separate LOD geometry and PBR bake; never overwrites generated/ hero files.
"$BLENDER" --background --threads 16 --python-exit-code 1 \
  --python sim/coffee_sorter/visual_assets/export_browser.py
python3 -m unittest discover -s sim/coffee_sorter/visual_assets -p 'test_*.py' -v
```

Use `--output-dir /absolute/path` for a new render take and pass that same directory to `encode_recording.py`. Native PNGs are retained locally; Git includes the MP4, first frame and full manifest, with the other regenerable PNGs ignored. The encoder checks all 30 PNG hashes before encoding. It saves the exact FFmpeg command/version, UTC start, elapsed time, output size and output hash. Old files beyond frame 30 cannot enter the movie.

The locked camera has identical keyframes at output frames 1 and 30: position `(0.4,-0.42,0.85)` m, target `(0.12,-0.09,0.555)` m, 64 mm focal length, 36 mm sensor, DOF off. There is no camera move to confuse the trajectory comparison. Cycles CPU renders 1280×720, 48 samples, seed 230948, adaptive threshold 0.035, denoising on, 5 maximum bounces, AgX Medium High Contrast and +0.8 exposure. Lights and machine presentation materials are fixed in the hash-pinned script. The manifest includes camera keyframes and render settings. Byte-identical images across different Blender/CPU/denoiser builds are not promised; pin this build for comparison.

Measured final: **276.478 s** summed render/PNG calls; **280.923 s** for the complete frame loop including transform updates, audits and manifest writes; **16 CPU threads**. Startup/mesh construction is excluded. Encoding took **0.414 s**. MP4: **263,952 bytes**, 30 frames, 1280×720, 30 fps, 1.000 s; an independent FFmpeg decode confirmed all 30 frames. The render happened with HEAD at `df8c241`; source content hashes identify the new scripts before their commit.

## Browser reuse and fallback

The chosen fallback is **separate mesh LODs with baked PBR textures**, not a reduction of hero quality. Four standalone GLBs each contain one mesh primitive and one material, with embedded 256×256 base-colour, tangent-normal and roughness/metallic textures. Metallic is zero. Normal detail is baked from the LOD's procedural shader, not projected from the high-poly silhouette; small holes/creases can soften during decimation. Baking also removes per-instance procedural colour randomness. This is a deliberate browser quality limit.

The target is about 600 triangles per prototype, compared with roughly 5,000–13,000 triangles in the detailed prototypes. The actual counts and GLB-vs-OBJ bytes are in `browser/manifest.json`. OBJ is geometry-only while GLB includes textures; the packed detailed Blender library is a separate 453,483-byte comparison. GPU texture memory after decode is larger than compressed file bytes. This is a conservative LOD choice; no claim is made that the hero assets fail on every device.

| Prototype | GLB bytes (including PBR) | Detailed OBJ bytes (geometry only) | LOD / hero triangles |
| --- | ---: | ---: | ---: |
| Good | 181,792 | 610,052 | 600 / 12,096 |
| Black | 179,848 | 610,053 | 600 / 12,096 |
| Insect | 199,912 | 631,016 | 598 / 12,652 |
| Broken | 172,232 | 253,049 | 600 / 4,752 |
| Total | **733,784** | **2,104,170** | **2,398 / 41,596** |

The four textured GLBs total 65.1% fewer bytes than the OBJs, but are **61.8% larger than the compressed 453,483-byte procedural Blender library**. Geometry reduction is 94.2%. Decimation is followed by restoring the original local bounds, including the broken cut plane, before baking. This preserves nominal dimensions and origins while keeping simplification out of hero renders.

Blender's standard glTF export uses Y up: `(x,y,z)_glTF = (x,z,-y)_Blender`. In a Z-up Three.js scene, first flatten the loaded node transform into its geometry, then rotate geometry **+π/2 about X**. The proof page does this once per prototype. Do not apply that correction again per instance. To reuse the viewer's existing recorded dimension matrices, additionally divide regular geometry coordinates by nominal semi-axes, and broken geometry by nominal AABB half-extents. Then keep the original per-class `THREE.InstancedMesh`, its UID-to-slot mapping, recorded position/quaternion/scale updates and decision colours. Do not add one scene object per bean. Broken local Z remains nonnegative after normalization.

`browser/proof.html` loads these actual GLBs with the official [Three.js r180 GLTFLoader](https://github.com/mrdoob/three.js/blob/r180/examples/jsm/loaders/GLTFLoader.js), matching the existing vendored Three.js r180. Its loader and BufferGeometryUtils are vendored with the upstream MIT license; the existing Three core is reused. [Three.js loader documentation](https://threejs.org/docs/#GLTFLoader) describes the standard loader API.

This standalone proof has four-bean inspection and 552-instance stress modes (four draws, 138 instances per prototype), covering the shipped replay's peak of 551 active beans. It is a synthetic benchmark, not a modified live webpage or replay. It records the renderer, viewport, draw calls, triangle counts and 180-frame median/p95 wall times. Refresh-rate and software-renderer limits affect these timings; they are evidence for this environment, not a device-wide FPS guarantee.

Verified in Chromium using **ANGLE/SwiftShader software rendering**, 1240×404 drawing buffer, DPR 1: four inspection instances, 2,398 triangles, four calls, median/p95 **16.7/16.7 ms**; 552 instances, 330,924 triangles, four calls, median/p95 **150.0/166.7 ms**, each over 180 intervals. Thus the crowd case is only about **6.7 fps on this software renderer**. These are static instance matrices and omit the live viewer's per-frame matrix-update cost. Hardware-GPU performance remains unmeasured. Do not infer deployment readiness or swap these into the live viewer based on this benchmark; its current inexpensive procedural fallback remains available. No browser errors were reported and every GLB loaded all three PBR maps. [Inspection evidence](evidence/browser-inspect.json), [stress evidence](evidence/browser-stress.json), [inspection screenshot](evidence/browser-inspect.png), [stress screenshot](evidence/browser-stress.png).

For local validation only (no deployed service or application backend):

```bash
python3 -m http.server 8765 --bind 127.0.0.1 --directory sim/coffee_sorter
# Open http://127.0.0.1:8765/visual_assets/browser/proof.html
```

Inspect all four prototypes, switch to 552 instances, confirm four draw calls and three maps per asset, and check the console for loader/WebGL errors. The current live viewer is untouched; a future visual-only adapter can follow the normalization recipe above.

## Higher-frequency capture proposal, not executed

The **existing exporter accepts at most 60 fps**. The immediate supported proposal is one fresh 4-second capture at 60 fps (240 intervals, potentially 241 samples with the endpoint), using the same pinned source/model, seed 7, rate 1000 and 0.06 N force. Keep `[2,3)` s for comparison and save to a new file, never over `web/replay.json`:

```bash
# PROPOSAL ONLY. This runs a simulation and was NOT executed for Deliverable B.
python sim/coffee_sorter/export_replay.py \
  --sim-dir /path/to/pinned-340e734-checkout/sim/coffee_sorter \
  --model /path/to/pinned-340e734-checkout/sim/coffee_sorter/models/green_arabica.joblib \
  --seconds 4 --fps 60 --rate 1000 --jet-force 0.06 --seed 7 \
  --output /new-output/coffee-60hz.json
```

This halves pose spacing to about 16/18 ms, but still undersamples 3–12 ms valves and is marginal for close-up slow motion. For serious slow motion, propose separately approving a narrow exporter FPS-cap increase and a **500 Hz** pose capture at every existing 2 ms physics step (about 16.7× as many pose samples as 30 Hz). That would use the existing exporter loop with a changed argument guard, not new Blender physics; it requires a future scoped change and has not been executed. Camera/controller cadence remains the original 4 ms. Even 500 Hz does not create sub-step physical truth. Preserve exact event timestamps, source/exporter/model hashes, policy, seeds and UTC capture time in that future capture bundle.

## Validation

Three artifact-contract tests pass: all 30 frame row hashes, UIDs, outcomes, timestamps, counters, valve/decision events and source/model/config provenance match the shipped replay; movie/poster hashes match; all four GLBs have one primitive/material, embedded PBR images, fewer than 650 triangles, and exact original bounds after the glTF basis conversion. Every rendered frame passed evaluated-transform assertions. FFmpeg decoded 30 frames / 1.00 s without error.

Existing `npm ci`, `npm run build` and `npm test` pass: the unchanged page is 1,842,120 bytes; six replay tests run, one optional fresh-physics test skipped. Existing simulator discovery runs 66 tests, one optional UR5e/imageio test skipped, all others pass. [Artifact test log](evidence/delivery-tests.log), [simulator log](evidence/recording-sim-tests.log), [decode log](evidence/recording-decode.log). The same draft PR remains on the explicitly requested baseline; the integration branch's previously documented pinned-source test issue is outside this asset change.
