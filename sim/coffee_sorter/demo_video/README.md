# Coffee demo video previews

This directory owns only the demo storyboard and preview-render command.
It does not own the source scene, simulator, model, live interface, or recorded evidence.

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

The stills use the selected looks for separate roles:

- `machine-overview`: `hero` with `warm-roastery` for machine form.
- `inspection-close-up`: `inspection` with `noir-rim` for visible bean materials.
- `discharge-air-jet`: `discharge` with `blueprint` for nozzle and splitter context.

These previews use an old recorded instant.
They do not show the current live engine or establish sorting quality, speed, or physical feasibility.
