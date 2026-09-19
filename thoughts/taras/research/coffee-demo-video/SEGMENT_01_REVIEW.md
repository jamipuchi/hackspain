# Segment 1 review

Status: Ready for Taras's visual review. Do not render segment 2 yet.

## Output

- Video: `/private/tmp/coffee-demo-video-previews/segments/01-machine-overview-v1/01-machine-overview-preview.mp4`
- Blender scene: `/private/tmp/coffee-demo-video-previews/segments/01-machine-overview-v1/segment.blend`
- Manifest: `/private/tmp/coffee-demo-video-previews/segments/01-machine-overview-v1/manifest.json`
- PNG frames: `/private/tmp/coffee-demo-video-previews/segments/01-machine-overview-v1/frames/`

## Measured result

- Duration: 4.000 seconds.
- Video: H.264, 640 by 360 pixels, 24 frames per second.
- Frames: 96 encoded and 96 decoded without error.
- Render: 12 Cycles samples with eight threads.
- Blender render time: 128.217 seconds.
- Complete Blender process time: 133.381 seconds.
- Encoding time: 0.704 seconds.
- Video size: 81,448 bytes.
- Video SHA-256: `898e69a089298608f1089236c6b84b406da000b5e69b9950af722b04c782397a`

The camera moves 0.22 metres forward with fixed rotation.
The move reads as a subtle establishing push.
All 523 beans keep their old frame 60 poses.
The maximum position error is zero.

## Reproduce

Use a new output directory for each revision:

```sh
python3 sim/coffee_sorter/demo_video/render_intro_segment.py \
  --output-dir /private/tmp/coffee-demo-video-previews/segments/01-machine-overview-v2
```

## Review question

Choose whether the camera push should stay subtle or become stronger.
Segment 2 remains paused until Taras reviews this segment.
