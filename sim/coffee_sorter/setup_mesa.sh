#!/usr/bin/env bash
set -euo pipefail

# Root-free OSMesa recipe for Ubuntu 24.04-class hosts. Package versions resolve
# from the host's current APT repositories and are intentionally not an OS lock.
# Nothing is installed into the system package database.
mesa_dir="${XDG_CACHE_HOME:-${HOME:?HOME must be set}/.cache}/coffee-sorter/mesa"
mkdir -p "$mesa_dir/lists/partial" "$mesa_dir/cache/archives/partial"
printf '%s\n' \
  "Dir::State::lists \"$mesa_dir/lists\";" \
  "Dir::Cache \"$mesa_dir/cache\";" \
  'Dir::State::status "/var/lib/dpkg/status";' > "$mesa_dir/apt.conf"
APT_CONFIG="$mesa_dir/apt.conf" apt-get update
# Fresh download and extraction trees prevent mixing package generations.
download_dir=$(mktemp -d "$mesa_dir/cache/archives/run.XXXXXX")
runtime_root=$(mktemp -d "$mesa_dir/root.XXXXXX")
(
  cd "$download_dir"
  APT_CONFIG="$mesa_dir/apt.conf" apt-get download \
    libosmesa6 libllvm17t64 libdrm2 libelf1t64 libzstd1 libsensors5 \
    libedit2 libffi8 libpciaccess0 libglapi-mesa
  : > "$runtime_root/runtime-packages.txt"
  for package in ./*.deb; do
    dpkg-deb -f "$package" Package Version >> "$runtime_root/runtime-packages.txt"
    dpkg-deb -x "$package" "$runtime_root"
  done
)
printf '# Unpinned runtime package versions recorded in %s\n' "$runtime_root/runtime-packages.txt"
printf 'export LD_LIBRARY_PATH=%q${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}\n' "$runtime_root/usr/lib/x86_64-linux-gnu"
printf '%s\n' 'export MUJOCO_GL=osmesa PYOPENGL_PLATFORM=osmesa'
