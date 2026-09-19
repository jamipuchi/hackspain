# Baseline video

The full 8 simulated seconds are retained as 400 frames at 25 fps (16 seconds playback).
`overview.mp4` is H.264, CRF 26, transcoded from the simulator video for phone playback.
The original source video SHA-256 was
`77227a45339d0b3f9eef4af3b0f7992349a820ed84a3609fd78e4d4bc78f2e5f`.

HUD glyph overlap was fixed before simulation by drawing each line once on a dark panel.
The video also carries a correction: its legacy valve counter counts queued commands.
Use `metrics.json` for actual activations and inspection sheets for per-track `FIRED`/own hits.
The correction overlay does not change simulated outcomes or numerical values.
`hud_frame200.png` was decoded from the delivered H.264 video and visually inspected.
