# Coffee demo video previews

This directory owns only the demo storyboard and preview-render commands.
It does not own the source scene, simulator, model, live interface, or recorded evidence.

## Separate video previews

Taras authorized video rendering after the still review.
Render the approved scenes as separate clips:

```sh
python3 sim/coffee_sorter/demo_video/render_segments.py
cp sim/coffee_sorter/demo_video/segment_gallery.html /private/tmp/coffee-demo-video-previews/clips.html
```

Use `--only 01-bean-macro` to render one segment.
The command creates silent 640 by 360 MP4 previews at 30 fps.
The default uses 12 Cycles samples and eight Blender threads.
Every Blender process requires the shared runtime lock.
An occupied lock returns status 75. Run the command again after the slot becomes available.

The video gallery is `http://127.0.0.1:8894/clips.html` when the preview server is active.
The completed package is `/private/tmp/coffee-demo-video-previews/coffee-demo-clips.zip`.
It contains nine separate MP4s with descriptive filenames. It excludes the UI and rejected render attempts.
It exposes each verified MP4 when that segment completes.
The renderer retains existing verified clips and can resume verified partial frame sequences with identical inputs.
It refuses mismatched inputs or unverified existing output files.
Use a new output directory for a revised render.

Dynamic segments use consecutive actual frames from the historical 30 Hz replay.
The macro holds one recorded instant while its camera and focus move.
Mode variants share the same frame sequence and camera path.
No simulation, inferred bean trajectory, or optical-flow retiming runs inside this renderer.
The air-jet segment uses a separate high-rate capture and a separate command.
See `thoughts/taras/research/coffee-demo-video/SEGMENTS_REVIEW.md` for current progress and evidence.

### Verified slow motion

The engine task provides the fresh capture and its reproduction audit under the preview directory.
Do not render until the independent audit approves that capture.
The renderer requires the exact validated replay hash and zero pose differences.

```sh
python3 sim/coffee_sorter/demo_video/render_slowmo.py --study \
  --output-dir /private/tmp/coffee-demo-video-previews/slowmo-study-review
python3 sim/coffee_sorter/demo_video/render_slowmo.py
```

Inspect the seven study frames before the full render.
The study also checks both bean centres for obstructions across all 120 frames.
The final clip directory is `/private/tmp/coffee-demo-video-previews/segments-v2/05-ultra-slowmo-v2/`.
The full clip uses the first 120 samples from the audited 143-sample interval.
It ends at 1.716 simulated seconds, after both physical outcomes and before the splitter hides the good bean.
Playback at 30 fps slows the motion by 16.667 times without synthetic poses.
The camera tracks black bean 1261 and good bean 1256, then retreats as their paths separate.
All other recorded beans remain present.
The manifest records the source pulse, direct contacts, outcomes, camera, cutaways, and frame hashes.
This development-seed example does not establish general sorting quality or hardware feasibility.

## Current scene studies

Taras approved the concept direction and requested basic renders of every scene.
The current pass includes nine scene studies and three matched mode variants.
Taras will record the UI separately. The gallery excludes the UI.
It does not contain new animation.
The discharge has blueprint and normal versions.
The closing view has clay, blueprint, and normal versions.
Each group retains its camera, source frame, focus, and geometry settings.
The gallery provides a wipe control for comparing modes and links to each clean PNG.

```sh
python3 sim/coffee_sorter/demo_video/render_studies.py \
  --output-dir /private/tmp/coffee-demo-video-previews/basic-scene-renders-v3
cp sim/coffee_sorter/demo_video/study_gallery.html /private/tmp/coffee-demo-video-previews/index.html
python3 -m http.server 8894 --bind 127.0.0.1 \
  --directory /private/tmp/coffee-demo-video-previews
```

Open `http://127.0.0.1:8894/` to inspect the gallery.
Use `--only <shot>` to render one composition.
Each shot produces a PNG, an editable Blender scene, a log, and a source manifest.
The renderer retains all recorded beans and lists each presentation cutaway.
Existing images remain unchanged. Use a new output directory for a revised pass.
The shared lock and eight-thread limit apply to every Blender process.

See `thoughts/taras/research/coffee-demo-video/BASIC_RENDER_REVIEW.md` for evidence and limitations.

## Earlier studies

Run the three previews from the repository root:

```sh
python3 sim/coffee_sorter/demo_video/render_previews.py \
  --output-dir /private/tmp/coffee-demo-video-previews
```

The command uses frame 60 from the shipped replay.
It renders each shot separately with Blender preview settings and eight threads.
It acquires `/private/tmp/hackspain-coffee-runtime.lock` before each Blender process.
It exits with status 75 when another task owns the render slot.

The wrapper writes each image, Blender scene, source manifest, log, and one run manifest to the output directory.
The generated files remain outside Git.
See `thoughts/taras/research/coffee-demo-video/PREVIEW_REPORT.md` for the measured preview review.

The stills use the selected looks for separate roles:

- `machine-overview`: `hero` with `warm-roastery` for machine form.
- `inspection-close-up`: `inspection` with `noir-rim` for visible bean materials.
- `discharge-air-jet`: `discharge` with `blueprint` for nozzle and splitter context.

These previews use an old recorded instant.
They do not show the current live engine or establish sorting quality, speed, or physical feasibility.

## Review segment 1

Render the four-second machine overview:

```sh
python3 sim/coffee_sorter/demo_video/render_intro_segment.py
```

The review render is 640 by 360 pixels at 24 frames per second.
It uses 12 Cycles samples and eight threads.
The camera moves 0.22 metres forward.
All beans keep their recorded frame 60 poses.

The command writes PNG frames, an editable Blender scene, an MP4, logs, and one manifest.
It refuses to replace existing rendered frames.
