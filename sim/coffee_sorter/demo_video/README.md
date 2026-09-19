# Coffee demo video previews

This directory owns only the demo storyboard and preview-render commands.
It does not own the source scene, simulator, model, live interface, or recorded evidence.

## Separate video previews

### Native Full HD renders

Taras authorized all nine existing clips at 1920 by 1080, with unchanged framing and timing.
The Full HD preset uses 48 Cycles samples, a 0.035 adaptive threshold, denoising, and H.264 CRF 16.
The commands use the Mac's Metal GPU. The shared runtime lock and eight-thread limit still apply.

```sh
python3 sim/coffee_sorter/demo_video/render_segments.py --full-hd --device METAL \
  --output-dir /private/tmp/coffee-demo-video-previews/segments-1080p
python3 sim/coffee_sorter/demo_video/render_slowmo.py --full-hd --device METAL \
  --output-dir /private/tmp/coffee-demo-video-previews/segments-1080p/05-ultra-slowmo
```

Use `--frame-limit 3` with one segment to benchmark three new frames.
Repeat without `--frame-limit` to resume the same verified frame sequence.
Use `--device CPU` on a computer without Metal. Do not change devices within an unfinished sequence.
The manifests record native frame dimensions, quality settings, source poses, and encoded video dimensions.
The original preview files remain unchanged.
The higher resolution does not change the slow-motion framing or resolve its pending visual feedback.

### Independent remote batch

`bulk_full_hd.py` renders sequentially, verifies native frames and encoded videos, and uploads each completed clip to Swarm.
It preserves identical existing remote files and rejects conflicting bytes.
It creates and verifies the final nine-clip ZIP without requiring the Mac.
Its status file is `segments-1080p/batch.json`. Detailed logs remain in `/tmp/bulk-full-hd-*.log` inside the container.

The HackSpain worker uses an isolated container named `coffee-demo-full-hd`.
Host files remain under `/opt/coffee-demo-render-20260919/`.
The container maps `source/` to `/render/source` and `data/` to `/private/tmp/coffee-demo-video-previews`.
It maps the official Blender 5.2.2 Linux directory to `/opt/blender` without write access.
The container currently uses eight CPUs and at most 16 GiB RAM. It does not modify existing services.
Taras permits up to 14 CPU threads for this remote job.
The active renderer retains eight threads to preserve its checkpoint contract.
The host render lock is bind-mounted at `/private/tmp/hackspain-coffee-runtime.lock`.
Its private credential file contains only `AGENT_FS_API_URL` and `AGENT_FS_API_KEY`.

```sh
ssh hackspain 'docker logs --tail 30 coffee-demo-full-hd'
ssh hackspain 'docker inspect --format "{{.State.Status}} {{.State.ExitCode}}" coffee-demo-full-hd'
ssh hackspain 'cat /opt/coffee-demo-render-20260919/data/segments-1080p/batch.json'
```

Inside the prepared container, the repeatable command is:

```sh
python3 sim/coffee_sorter/demo_video/bulk_full_hd.py \
  --credentials /run/secrets/agent-fs.json --verbose
```

The root batch lock prevents duplicate queues. Each Blender process also holds the shared render lock.
Completed clips copied from the Mac retain their original GPU manifests.
Only unfinished clips render with the CPU backend. Do not combine partial GPU frames with CPU frames in one clip.
Swarm Lead task `8d7515a6-f830-4140-bcf5-2a1d09cea153` owns monitoring and verified delivery in Slack `#x-hackspain`.
The local render queue stopped after remote verification. Its files remain intact, and its heartbeat is paused.

### Local assistance for the final clips

Taras subsequently authorized local rendering alongside the remote batch.
The Mac renders `07c-normal-closing`, then `07b-blueprint-closing`, using Metal and eight Blender threads.
Run the selected batch with reduced CPU priority:

```sh
nice -n 15 python3 sim/coffee_sorter/demo_video/bulk_full_hd.py \
  --credentials /path/to/private-agent-fs.json --device METAL \
  --only 07c-normal-closing --only 07b-blueprint-closing --source-bundles
```

The credential path can be `/dev/stdin` when a parent process supplies the JSON through a private pipe.
Never include credential values in shell arguments or logs.
The selected batch uploads each verified MP4 and manifest without creating the final nine-clip ZIP.
It also uploads native-frame ZIP parts under `full-hd/sources/<segment-id>/`.
Each part stays below 40 MiB because agent-fs limits uploads to 50 MiB.
The worker uploads `ready.json` only after every part passes upload and download checks.
That index records each part's path, hash, byte count, and members.
It also records the manifest and video hashes.

The remote monitor must verify all indexed parts before importing a completed local clip.
It must reject unsafe paths, duplicate members, mismatched hashes, and conflicts with partial CPU renders.
It must compare the imported files with the canonical MP4 and manifest uploads.
The remote batch then preserves the verified complete clip instead of rendering it again.
The Lead retains final archive creation and Slack delivery in `#x-hackspain`.
Local rendering requires the Mac to remain awake. Reduced CPU priority does not limit GPU utilization.

### Preview commands

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
The clips and editable assets are backed up in agent-fs.
See `thoughts/taras/research/coffee-demo-video/ASSET_ARCHIVE.md` for paths, checksums, and the pending slow-motion framing revision.
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
