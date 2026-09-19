# Cinematic coffee scene

The scene combines the complete recorded machine, presentation details, and the recorded bean positions.
Each command creates a PNG, an editable Blender file, and a manifest.
The Blender file embeds the source manifest, including bean poses and camera settings.

This first increment reproduces one recorded instant. It does not contain animation.
Use the existing `render_recording.py` for the earlier one-second recording proof.

## Render a preview

Run these commands from the repository root with Blender on PATH:

```sh
blender --background --python-exit-code 1 \
  --python sim/coffee_sorter/visual_assets/render_scene.py -- \
  --shot hero --look noir-rim --frame 60 --preview --threads 8 \
  --output-dir /tmp/coffee-scene/hero-noir-rim
```

The preview uses 960 by 540 pixels and 24 samples.
Remove `--preview` for 1920 by 1080 pixels and 96 samples.
Use `--samples` to override the sample count.
Remote workers must use sequential renders with `--threads 16`.

Shot names are `hero`, `inspection`, and `discharge`.
Look names are `noir-rim`, `blueprint`, and `warm-roastery`.
The frame argument selects a zero-based index in the recording.
Frame 60 is the recorded instant at 2.0 seconds in the shipped replay.

Open the saved scene for manual camera work:

```sh
blender /tmp/coffee-scene/hero-noir-rim/scene.blend
```

Render that saved scene without rebuilding geometry:

```sh
blender --background /tmp/coffee-scene/hero-noir-rim/scene.blend \
  --threads 8 --render-output /tmp/coffee-scene/reproduced_ --render-frame 61
```

## Source ownership

- `scene_machine.py` creates machine geometry and materials.
- `scene_direction.json` defines the cameras and lighting.
- `scene_lighting.py` applies those settings.
- `render_scene.py` assembles the scene and records its provenance.
- `render_recording.py` supplies the shared bean import and pose checks.

The renderer does not start the simulator or change the recording.
The pose check compares evaluated world transforms with source positions and quaternions.
The manifest records file hashes, source counters, Blender version, settings, and measured render time.

## Visual limits

The machine details illustrate an industrial sorter. They do not establish a manufacturable design.
They are presentation geometry and do not affect collisions.
The simulator still controls the belt, chute surfaces, bean positions, and outcomes.

Detailed bean assets cover good, black, insect-damaged, and broken beans.
Other classes still use appearance stand-ins.
The noir look preserves bean materials. Blueprint and `warm-roastery` (clay) remove color differences.
The procedural assets remain subject to Taras's visual review.
These renders establish no accuracy or throughput result.

See [SCENE_DIRECTION.md](SCENE_DIRECTION.md) for the role of each look and the remaining visual work.

## Initial verification

Blender 5.2.2 on the local M2 Pro rendered five preview combinations on 2026-09-19.
They covered every primary shot and every look.
The final blueprint preview took 2.6 seconds to build and 8.7 seconds to render with eight threads.
These timings describe previews, not final quality or simulator speed.

The pose checks passed for all 523 beans in frame 60.
The maximum position error was zero. The quaternion component error was below 0.000001.
The existing recording preview also rendered after the shared importer changed.

The saved blueprint scene reloaded with all 523 beans in `RecordedBeans`.
Rendering that saved scene reproduced the same decoded PNG pixels.
The PNG file hashes differ because Blender includes timestamps and render duration in image metadata.
The embedded manifest contains source and render metadata. Only the external manifest contains the Blender file hash.

Taras's visual acceptance and the remote Blender 4.5.4 render remain pending.
