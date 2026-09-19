#!/usr/bin/env bash
# Same recorded frames, eight times slower. The lower panel crops discharge;
# the upper panel retains the original HUD. Final frame held for readability.
set -euo pipefail
cd "$(dirname "$0")"
ffmpeg -y -loglevel error -ss 1.18 -t 0.24 -i overview_h264.mp4 \
  -filter_complex '[0:v]split=2[hud][flight];[hud]crop=1280:182:0:0[h];[flight]crop=512:320:540:260,scale=1280:800[f];[h][f]vstack=inputs=2,setpts=8*PTS,fps=50,tpad=stop_mode=clone:stop_duration=2[v]' \
  -map '[v]' -an -r 50 -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p -movflags +faststart unknown_8x_slow.mp4
