# Separate video preview renders

Date: 2026-09-19.
Status: All nine separate preview clips are complete and verified.
Visual acceptance: Taras reported that the slow-motion discard remains difficult to see.
That shot needs revised framing before a final-resolution render.
Taras will record the UI separately.

The [agent-fs archive record](ASSET_ARCHIVE.md) lists the uploaded clips, editable assets, and verification results.

The review page is http://127.0.0.1:8894/clips.html.
It exposes each clip after encoding and verification.
Outputs remain under `/private/tmp/coffee-demo-video-previews/segments-v2/`.
The ordinary clips use `sim/coffee_sorter/demo_video/render_segments.py`.
The verified slow-motion clip uses `sim/coffee_sorter/demo_video/render_slowmo.py`.
The edit package is `/private/tmp/coffee-demo-video-previews/coffee-demo-clips.zip`.
It contains nine MP4s with descriptive filenames, without intermediate or rejected renders.

## Segment status

| Segment | Motion | Duration | Status |
|---|---|---:|---|
| 01 Bean detail | Camera slide and focus movement on one held recorded instant | 3 s | Complete |
| 02 Conveyor reveal | Moving camera and consecutive recorded poses | 3 s | Complete |
| 03 Overhead inspection | Locked camera and consecutive recorded poses | 3 s | Complete |
| 04A Discharge, blueprint | Locked camera and consecutive recorded poses | 2 s | Complete |
| 04B Discharge, normal | Same timing and camera as blueprint | 2 s | Complete |
| 05 Ultra-slow-motion air jet | Verified high-rate poses and pulse contact | 4 s | Complete |
| 07A Closing, clay | Lateral camera movement and consecutive recorded poses | 3 s | Complete |
| 07B Closing, blueprint | Same timing and camera as clay | 3 s | Complete |
| 07C Closing, normal | Same timing and camera as clay | 3 s | Complete |

## Render contract

The preview uses CPU Cycles, 12 samples, eight threads, 640 by 360 pixels, and 30 fps.
Each Blender subprocess holds the shared runtime lock throughout its execution.
The ordinary clips use the approved Blender scene for each look.
The slow-motion clip rebuilds the machine from the fresh capture's recorded geometry, with the same presentation assets.
Dynamic clips apply one actual recorded pose sample per output frame.
No generated intermediate bean positions appear in those clips.
Motion blur remains disabled.
The historical replay stores slightly irregular timestamps near the nominal 30 Hz sampling interval.
The encoded video uses a constant 30 fps timeline.

Every frame manifest stores its source index, source time, pose hash, camera position, camera target, focus distance, and pose errors.
The manifest stores each frame hash and the encoded video hash.
The renderer verifies the encoded dimensions, frame rate, and decoded frame count with FFprobe.
The clean clips contain no soundtrack, titles, or transition effects.
Taras can use the matched mode passes for the final edit.

## Completed evidence

### 01 Bean detail

- Path: `/private/tmp/coffee-demo-video-previews/segments-v2/01-bean-macro/preview.mp4`
- Duration: 3.000 seconds.
- Frames: 90.
- Blender process time: 190.532 seconds.
- Codec: H.264, 640 by 360, 30 fps.
- Browser playback advanced beyond 0.5 seconds without a media error.
- Visual checks covered the initial focus and the sharper later focus.

The opening intentionally begins out of focus.
This shot holds the bean poses and moves only the camera and focus.
It does not claim ultra-slow-motion physical movement.

### 04B Discharge, normal

- Path: `/private/tmp/coffee-demo-video-previews/segments-v2/04b-normal-discharge/preview.mp4`
- Duration: 2.000 seconds.
- Frames: 60.
- Blender process time: 143.393 seconds.
- Source frames: 45 through 104, with timestamps from 1.500 to 3.468 simulated seconds.
- All 60 frames contain distinct recorded pose sets.
- Browser playback advanced beyond 0.5 seconds without a media error.

Frame hashes and recorded-pose tolerances passed for the completed clips.
The locked camera has only float32 interpolation roundoff, below the `1e-6` metre comparison tolerance.

### 02 Conveyor reveal

- Path: `/private/tmp/coffee-demo-video-previews/segments-v2/02-conveyor-reveal/preview.mp4`
- Duration: 3.000 seconds.
- Frames: 90.
- Blender process time: 232.558 seconds.
- Source frames: 30 through 119.
- All 90 frames contain distinct recorded pose sets.
- The camera moves between the two approved reveal positions.
- Browser playback advanced beyond 0.5 seconds without a media error.

The closing animation will use a 40 mm lens in all three modes.
This slightly wider composition retains the machine's feet during the lateral movement.

### 03 Overhead inspection

- Path: `/private/tmp/coffee-demo-video-previews/segments-v2/03-overhead-inspection/preview.mp4`
- Duration: 3.000 seconds.
- Frames: 90.
- Blender process time: 191.697 seconds.
- Source frames: 30 through 119.
- All frame hashes and recorded-pose tolerances passed.
- Browser playback advanced beyond 0.5 seconds without a media error.

One screenshot request timed out during concurrent capture work.
The browser diagnostic reported no failures. Video playback checks still passed.
A second screenshot request failed with a temporarily unavailable browser resource.
The completed video playback checks continued to pass. No screenshot from that failed request is claimed as evidence.

### 04A Discharge, blueprint

- Path: `/private/tmp/coffee-demo-video-previews/segments-v2/04-blueprint-discharge/preview.mp4`
- Duration: 2.000 seconds.
- Frames: 60.
- Blender process time: 200.558 seconds.
- All frame hashes and recorded-pose tolerances passed.
- All source indices, timestamps, bean counts, and pose hashes match the normal pass.
- Camera, target, and focus match the normal pass within `1e-6` metres.
- Browser playback advanced beyond 0.5 seconds without a media error.

### 07A Closing, clay

- Path: `/private/tmp/coffee-demo-video-previews/segments-v2/07-warm-closing/preview.mp4`
- Duration: 3.000 seconds.
- Frames: 90.
- Blender process time: 155.868 seconds.
- All frame hashes and recorded-pose tolerances passed.
- All frames use the 40 mm lens. The final image retains the machine's feet.
- Browser playback advanced beyond 0.5 seconds without a media error.

### 07B Closing, blueprint

- Path: `/private/tmp/coffee-demo-video-previews/segments-v2/07b-blueprint-closing/preview.mp4`
- Duration: 3.000 seconds.
- Frames: 90.
- Blender process time: 263.692 seconds.
- All frame hashes passed.
- Camera, target, focus, lens, source indices, timestamps, and pose hashes match the clay pass exactly.
- Browser playback advanced beyond 0.5 seconds without a media error.

### 07C Closing, normal

- Path: `/private/tmp/coffee-demo-video-previews/segments-v2/07c-normal-closing/preview.mp4`
- Duration: 3.000 seconds.
- Frames: 90.
- Blender process time: 160.556 seconds.
- All frame hashes passed.
- Camera, target, focus, lens, source indices, timestamps, and pose hashes match the clay pass exactly.
- Browser playback advanced beyond 0.5 seconds without a media error.

### 05 Verified slow motion

- Path: `/private/tmp/coffee-demo-video-previews/segments-v2/05-ultra-slowmo-v2/preview.mp4`
- Duration: 4.000 seconds.
- Frames: 120 actual samples from the 500 Hz capture.
- Playback speed: 16.667 times slower than simulated time.
- Blender process time: 199.638 seconds.
- All frame hashes, source pose hashes, source timestamps, bean counts, and recorded-pose tolerances passed.
- The manifest contains the independent audit and its SHA-256 hash.
- Both pulse samples and the endpoint retain unobstructed bean centres.
- Two single-frame centre crossings by other beans remain, as documented below.
- Browser playback advanced beyond 0.7 seconds without a media error.

### Final package checks

All nine MP4s independently passed decoded frame counts, H.264 format, 640 by 360 dimensions, and 30 fps checks.
All 780 PNG frame hashes match their manifests and recorded source poses.
Both discharge passes match within the documented camera tolerance.
All three closing passes have identical camera, focus, lens, timing, and pose values.
The gallery exposes nine ready clips and the download package.
The ZIP passed its integrity check and contains only the nine named MP4 files.
Its size is 2,996,332 bytes.
Its SHA-256 is `61c98461c88344486a3be47e93440d0eeaa78a2e7911a1e9833b79e3ba329d3a`.

## High-rate capture coordination

Taras explicitly authorized a fresh recording with air-contact evidence.
The engine task owns that isolated capture. This task does not edit the simulator or shared UI.
The requested evidence includes dense pose timestamps, valve intervals, per-bean contacts, source provenance, and physical outcomes.
All nearby objects must remain in the capture.
The macro render released the runtime lock before the reserved capture gap.
The air-jet preview must wait for the verified capture.
The first capture attempt reached its eight-minute wall limit without writing a replay.
The engine task reported historical scikit-learn inference as the bottleneck.
It is checking a faster inference path against the same model outputs before another attempt.
No physical contact claim follows from the failed attempt.
The second attempt replayed the recorded valve schedule through the original physics.
It produced 201 frames at 500 Hz but failed the comparison with the original poses.
The audit found differences in 5,952 of 6,010 common object poses.
The selected black bean had no recorded air contact in that candidate.
The candidate remains negative evidence. No video render uses it.
The fresh run used revision `44609edd63b456524d0dc100404417193023e668` and development seed 8.
It captured 326 frames at 500 Hz, 410 actual controller pulses, and 557 direct force-contact step records.
The engine agent reproduced the fresh run with exact, unrounded valve timestamps.
Its comparison found zero differences across 170,125 sampled object poses.
The independent reviewer confirmed all 170,125 poses across 326 timestamps, with zero encoded differences.
It also confirmed 410 fire commands, 1,736 decisions, 557 contact rows, and the source hashes.
The engine task explicitly approved rendering from the fresh capture.
Its durable audit is `/private/tmp/coffee-demo-video-previews/high-rate-capture/independent-audit.json`.
That audit supersedes the pending-review flag in the original author report.
The renderer requires the independent approval and stores the audit hash in its contract.
The reproduction remains verification evidence, not the render source.
Equality applies at the encoded resolution: 0.1 mm positions and `1e-4` quaternion components.
It does not establish equality of unquantized engine state.

The selected black bean is UID 1261. The selected good bean is UID 1256.
The black bean receives two direct force contacts at 1.628 and 1.630 simulated seconds.
The good bean receives no direct force contact.
The recorded outcomes are reject and accept, respectively.
The proposed interval is 1.478 through 1.762 seconds, inclusive.
Its 143 samples produce 4.767 screen seconds at 30 fps, or 16.667 times slower than the simulation.
The first visual study found the splitter hiding the good bean at the final captured moment.
The video therefore uses the first 120 samples: `[1.478, 1.718)`, ending at 1.716 simulated seconds.
Both recorded outcomes occur before that endpoint.
The shorter clip lasts 4.000 seconds at the same 16.667 times slower speed.
The second study verified visible bean centres at approach, first contact, and the shorter ending.
Both study directories remain available as evidence. The source capture and audit remain unchanged.
The first full sequence exposed intermediate occlusions that those three samples missed.
The revised study checks both bean centres in all 120 frames and renders seven representative frames.
The revised camera rises during approach and exit, and moves below the manifold during the pulse.
It omits the decorative right manifold support and clamp, in addition to the wall and trim cutaway.
It does not omit the recorded manifold geometry or any bean.
Study v6 verifies both pulse samples and the ending without centre occlusion.
Two single-frame centre occlusions remain from nearby beans: good UID 1256 at frame 68 and black UID 1261 at frame 78.
These last 33 milliseconds each. The surrounding beans remain unchanged.
The superseded full clip remains in `segments-v2/05-ultra-slowmo/` as negative visual evidence.
The delivered revision uses `segments-v2/05-ultra-slowmo-v2/`.
The renderer preserves all nearby beans and records its presentation cutaways.
It does not render invented airflow, interpolate poses, or claim hardware performance.
The capture used 1,000 objects per second and 0.06 N force.
It differs from the 500 objects per second live preview.
Direct contact means membership in the simulator force predicate, not a hardware sensor measurement.
