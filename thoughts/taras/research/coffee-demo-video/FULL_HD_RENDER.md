# Native Full HD coffee clips

Date: 2026-09-19.
Status: The remote batch is rendering. Full delivery remains pending.
Full HD preset commit: `18a40b0` on `codex/coffee-demo-video`.
Remote batch commit: `6dffdf1`.

Taras authorized native Full HD renders of all nine existing clips.
This pass preserves their framing, timing, camera paths, and recorded bean poses.
It excludes the separately recorded UI.
Taras retains visual acceptance. The slow-motion framing feedback remains unresolved.

## Settings

- Native resolution: 1920 by 1080, without upscaling.
- Playback: 30 fps.
- Cycles: 48 samples, adaptive threshold 0.035, denoising.
- Render device: Metal for the first two completed clips, CPU for the remaining clips.
- Active CPU threads: eight. Taras permits up to 14 for this remote job.
- Video: H.264, CRF 16, YUV 4:2:0.
- Shared exclusive lock: `/private/tmp/hackspain-coffee-runtime.lock`.
- Local output: `/private/tmp/coffee-demo-video-previews/segments-1080p/`.
- Gallery: http://127.0.0.1:8894/clips.html?quality=1080p.

The gallery identifies the selected resolution and verifies encoded dimensions before showing a clip as ready.
The original preview gallery remains available with `?quality=preview`.
The local gallery does not automatically receive files from the remote worker.

## Remote handoff

The independent container `coffee-demo-full-hd` runs on SSH host `hackspain`.
The host stores its source, runtime, and data under `/opt/coffee-demo-render-20260919/`.
The source checkout uses branch `codex/coffee-demo-video`.
The container mounts `source/` at `/render/source` without write access.
It mounts `data/` at `/private/tmp/coffee-demo-video-previews`.
The host render lock is bind-mounted at `/private/tmp/hackspain-coffee-runtime.lock`.
The container currently allows eight CPUs and 16 GiB RAM.
Its private credential mount contains only the two agent-fs connection fields.
The worker does not depend on an active SSH session or the Mac.

The official Blender 5.2.2 archive passed its SHA-256 check:
`84098912789dc450e95697c4184fb8a90acbe5111c2ba4aede3fecb57806a168`.
The image `coffee-demo-render:20260919` contains the required FFmpeg and agent-fs commands.
Existing services remain unchanged.

The first three remote inspection frames passed native PNG dimensions and hash checks.
Their render times were 41.476, 41.011, and 42.204 seconds.
The full batch resumed these CPU frames after the test ended.
The renderer source remains unchanged, which preserves the partial sequence contract.

The local queue and its current Blender process stopped after remote verification.
All local files remain intact, including the unused partial Metal inspection render.
The local heartbeat is paused to prevent duplicate work.

Swarm Lead task: `8d7515a6-f830-4140-bcf5-2a1d09cea153`.
The API confirmed assignment to Lead and initial status `pending`.
The task owns monitoring, recovery, verified uploads, final verification, and delivery in Slack `#x-hackspain`.
It requires exact agent-fs paths for each verified clip and the final ZIP.
It also requires seven-day signed links and Slack delivery evidence.
Taras permits up to 14 CPU threads, but the current eight-thread render retains its existing checkpoint contract.

```sh
ssh hackspain 'docker inspect --format "{{.State.Status}} {{.State.ExitCode}}" coffee-demo-full-hd'
ssh hackspain 'docker logs --tail 30 coffee-demo-full-hd'
ssh hackspain 'cat /opt/coffee-demo-render-20260919/data/segments-1080p/batch.json'
```

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
The remote batch verified these completed Full HD uploads during handoff:

| Clip | Bytes | SHA-256 |
|---|---:|---|
| `01-bean-macro-1080p.mp4` | 973405 | `43d162ce8f2d9b016d41409356890931045c0efab446355e0d6a06b3e009f869` |
| `02-conveyor-reveal-1080p.mp4` | 3539873 | `b567be8152e98c900f93dafd2741b2e3a4a36a0313c0f5cfd867172b156a151a` |

Their existing agent-fs bytes and manifests matched the copied local files.
The batch currently renders `03-overhead-inspection`.
The remaining seven clips and final ZIP are not complete at handoff.

## Subsequent local assistance

Taras authorized two local closing clips after the remote handoff.
The Mac tests passed for `07c-normal-closing` at native 1920 by 1080.
The three frames took 16.295, 9.297, and 8.293 seconds.
Their PNG dimensions and SHA-256 hashes passed independent checks.
The Mac continues that clip, then renders `07b-blueprint-closing`.
The selected worker uses Metal, eight Blender threads, and Unix priority 15.

The worker uploads completed MP4s, manifests, and bounded native-frame archives automatically.
Agent-fs limits each upload to 50 MiB, so native-frame parts stay below 40 MiB.
The completion index is `full-hd/sources/<segment-id>/ready.json` under the existing archive prefix.
The index appears only after all source parts pass upload verification and downloaded-byte comparison.
The original unrestricted local queue and heartbeat remain stopped.

Lead follow-up task: `2d41f9fa-865a-4d5f-b8eb-208076844006`.
The task received instructions to reuse verified agent-fs source parts before rendering the closing clips.
This agent-fs handoff replaces the proposed SSH transfer of local clips.
The Lead still owns remote recovery, final verification, archive creation, and delivery in Slack `#x-hackspain`.
