#!/bin/sh
set -eu

root=$(git rev-parse --show-toplevel)
revision=$(git rev-parse HEAD)
if [ -n "$(git status --porcelain)" ]; then
  echo 'Build only a committed, clean release checkout.' >&2
  exit 1
fi

tag="hackspain-coffee:${revision}"
docker build \
  --platform linux/amd64 \
  --file "$root/deploy/hack-growth.dev/Dockerfile" \
  --build-arg "SOURCE_REVISION=$revision" \
  --label "org.opencontainers.image.revision=$revision" \
  --tag "$tag" \
  "$root"

image_id=$(docker image inspect --format '{{.Id}}' "$tag")
label_revision=$(docker image inspect \
  --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$tag")
blender_version=$(docker image inspect \
  --format '{{index .Config.Labels "dev.cinta.blender.version"}}' "$tag")
blender_sha256=$(docker image inspect \
  --format '{{index .Config.Labels "dev.cinta.blender.archive.sha256"}}' "$tag")
test "$label_revision" = "$revision"
test -n "$blender_version"
test -n "$blender_sha256"
printf 'CINTA_SOURCE_REVISION=%s\nCINTA_IMAGE=%s\nBLENDER_VERSION=%s\nBLENDER_ARCHIVE_SHA256=%s\n' \
  "$revision" "$image_id" "$blender_version" "$blender_sha256"
docker run --rm --platform linux/amd64 --entrypoint cat "$tag" /app/blender-packages.txt
