#!/usr/bin/env bash
# Publish the required visuals and physical tradeoff; requires authenticated agent-fs.
# Run from any directory after experiments and review finish.
set -euo pipefail
cd "$(dirname "$0")"
publish_prefix=hackspain/coffee-sorter/2026-09-19/generalization
publish_log=$(mktemp -d /tmp/coffee-generalization-publish.XXXXXX)
for publish_file in comparison/confusion_matrices.png comparison/camera_strip_contact_sheet.png openset/threshold_sweep.png openset/demo/overview_h264.mp4 openset/demo/unknown_8x_slow.mp4 physical-thresholds/physical_tradeoff.png; do
  publish_name=$(basename "$publish_file")
  agent-fs write "$publish_prefix/$publish_file" --file "$publish_file" --json > "$publish_log/$publish_name.json"
  agent-fs download "$publish_prefix/$publish_file" -o "$publish_log/$publish_name" > /dev/null
  cmp "$publish_file" "$publish_log/$publish_name"
  sha256sum "$publish_file"
done
printf 'PASS: six visual artifacts uploaded and byte-verified; receipts: %s\n' "$publish_log"
