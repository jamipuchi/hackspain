# Separate video preview renders

Date: 2026-09-19.
Status: Rendering the approved scenes as separate preview clips.
Taras will record the UI separately.

The review page is http://127.0.0.1:8894/clips.html.
It exposes each clip after encoding and verification.
Outputs remain under `/private/tmp/coffee-demo-video-previews/segments-v2/`.
The source is `sim/coffee_sorter/demo_video/render_segments.py`.

## Segment status

| Segment | Motion | Duration | Status |
|---|---|---:|---|
| 01 Bean detail | Camera slide and focus movement on one held recorded instant | 3 s | Complete |
| 02 Conveyor reveal | Moving camera and consecutive recorded poses | 3 s | Queued |
| 03 Overhead inspection | Locked camera and consecutive recorded poses | 3 s | Queued |
| 04A Discharge, blueprint | Locked camera and consecutive recorded poses | 2 s | Queued |
| 04B Discharge, normal | Same timing and camera as blueprint | 2 s | Queued |
| 05 Ultra-slow-motion air jet | Verified high-rate poses and pulse contact | Pending capture | Capture authorized |
| 07A Closing, clay | Lateral camera movement and consecutive recorded poses | 3 s | Queued |
| 07B Closing, blueprint | Same timing and camera as clay | 3 s | Queued |
| 07C Closing, normal | Same timing and camera as clay | 3 s | Queued |

## Render contract

The preview uses CPU Cycles, 12 samples, eight threads, 640 by 360 pixels, and 30 fps.
Each Blender subprocess holds the shared runtime lock throughout its execution.
The render source is the approved Blender scene for each look.
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

## High-rate capture coordination

Taras explicitly authorized a fresh recording with air-contact evidence.
The engine task owns that isolated capture. This task does not edit the simulator or shared UI.
The requested evidence includes dense pose timestamps, valve intervals, per-bean contacts, source provenance, and physical outcomes.
All nearby objects must remain in the capture.
The macro render released the runtime lock before the reserved capture gap.
The air-jet preview must wait for the verified capture.
