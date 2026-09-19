# Native Full HD coffee clips

Date: 2026-09-19.
Status: Rendering. The initial benchmark passed. Full delivery remains pending.
Source commit: `18a40b0` on `codex/coffee-demo-video`.

Taras authorized native Full HD renders of all nine existing clips.
This pass preserves their framing, timing, camera paths, and recorded bean poses.
It excludes the separately recorded UI.
Taras retains visual acceptance. The slow-motion framing feedback remains unresolved.

## Settings

- Native resolution: 1920 by 1080, without upscaling.
- Playback: 30 fps.
- Cycles: 48 samples, adaptive threshold 0.035, denoising.
- Render device: Apple M2 Pro Metal GPU.
- CPU thread limit: eight.
- Video: H.264, CRF 16, YUV 4:2:0.
- Shared exclusive lock: `/private/tmp/hackspain-coffee-runtime.lock`.
- Local output: `/private/tmp/coffee-demo-video-previews/segments-1080p/`.
- Gallery: http://127.0.0.1:8894/clips.html?quality=1080p.

The gallery identifies the selected resolution and verifies encoded dimensions before showing a clip as ready.
The original preview gallery remains available with `?quality=preview`.

## Initial verification

The first three macro frames rendered at native 1920 by 1080.
The frame manifest records zero position error against the recorded poses.
The first frame took 118.366 seconds, including GPU setup.
The next frames took 12.054 and 11.082 seconds.
The full queue resumes these verified frames instead of rendering them again.

Python syntax checks passed for both renderers.
Both renderers reject a Full HD request against existing low-resolution videos.
Both preserve existing verified previews when the requested settings match.
Invalid sample counts and frame limits fail before rendering starts.
The browser displays nine separate entries and the current frame progress.

## Delivery

The queue renders sequentially and uploads each completed clip with its manifest.
It verifies encoded dimensions, frame rate, frame count, local hash, upload hash, and downloaded bytes.

- Organization: `swarm`, `9d0f4b46-6113-49f7-8e8c-d315a64bd59d`.
- Drive: `default`, `ad84339c-9d70-462a-84cf-b58aba031ac5`.
- Remote prefix: `qa/hackspain/2026-09-19-coffee-demo/full-hd/`.
- Clip names: `<segment-id>-1080p.mp4`.
- Manifests: `manifests/<segment-id>.json`.
- Final package, pending: `qa/hackspain/2026-09-19-coffee-demo/coffee-demo-clips-1080p.zip`.

The completion check must verify all nine clips and all 780 frames before final delivery.
It must verify matched mode cameras and source poses against the original previews.
It must verify browser playback, ZIP integrity, and the final remote package bytes.
No complete Full HD clip or package is claimed by this initial status record.
