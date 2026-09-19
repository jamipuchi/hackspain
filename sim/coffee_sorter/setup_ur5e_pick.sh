#!/usr/bin/env bash
set -euo pipefail

# The robot meshes are deliberately cached outside Git. BSD-3-Clause license:
# https://github.com/google-deepmind/mujoco_menagerie/tree/8161bba264d7fa7c99ca301e91e7fb44737676ad/universal_robots_ur5e
cache_dir="${XDG_CACHE_HOME:-${HOME:?HOME must be set}/.cache}/coffee-sorter/mujoco_menagerie"
revision="8161bba264d7fa7c99ca301e91e7fb44737676ad"
if [[ ! -d "$cache_dir/.git" ]]; then
  mkdir -p "$(dirname "$cache_dir")"
  git clone --filter=blob:none --no-checkout https://github.com/google-deepmind/mujoco_menagerie.git "$cache_dir"
fi
git -C "$cache_dir" sparse-checkout set universal_robots_ur5e
git -C "$cache_dir" fetch --depth 1 origin "$revision"
git -C "$cache_dir" checkout --detach "$revision"
printf '%s\n' "$cache_dir"
