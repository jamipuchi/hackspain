# Isolated Blender runtime

Verified here: Blender 4.5.4 LTS, build `b3efe983cc58`, Linux x64. CPU-only Cycles. The runtime stays outside Git and does not change services, system packages or DNS.

## Download and verify

```bash
mkdir -p /workspace/personal/tools
cd /workspace/personal/tools
curl -fL -o blender-4.5.4-linux-x64.tar.xz \
  https://download.blender.org/release/Blender4.5/blender-4.5.4-linux-x64.tar.xz
curl -fL -o blender-4.5.4.sha256 \
  https://download.blender.org/release/Blender4.5/blender-4.5.4.sha256
printf '%s  %s\n' \
  2e6ef8e99fc36327270429ddc8e7bad2859dd878a5a137d2e0bf0f02f6792505 \
  blender-4.5.4-linux-x64.tar.xz | sha256sum -c -
awk '$2=="blender-4.5.4-linux-x64.tar.xz"{print}' blender-4.5.4.sha256 | sha256sum -c -
XZ_DEFAULTS='--threads=4' tar -xJf blender-4.5.4-linux-x64.tar.xz
```

## Missing shared libraries on this worker

This Ubuntu 24.04 worker lacked `libSM.so.6`, `libICE.so.6`, `libGL.so.1`, `libGLX.so.0` and `libGLdispatch.so.0`. The isolated package extraction below supplies them. A desktop Linux installation may already have them.

```bash
COFFEE_BLENDER_LIBS=/workspace/personal/tools/blender-libs
mkdir -p "$COFFEE_BLENDER_LIBS/lists/partial" \
  "$COFFEE_BLENDER_LIBS/cache/archives/partial" \
  "$COFFEE_BLENDER_LIBS/debs" "$COFFEE_BLENDER_LIBS/root"
printf 'Dir::State::lists "%s/lists";\nDir::Cache "%s/cache";\nDir::State::status "/var/lib/dpkg/status";\n' \
  "$COFFEE_BLENDER_LIBS" "$COFFEE_BLENDER_LIBS" > "$COFFEE_BLENDER_LIBS/apt.conf"
APT_CONFIG="$COFFEE_BLENDER_LIBS/apt.conf" apt-get update
(
  cd "$COFFEE_BLENDER_LIBS/debs"
  APT_CONFIG="$COFFEE_BLENDER_LIBS/apt.conf" apt-get download libsm6 libice6 libgl1 libglx0 libglvnd0
  for package in ./*.deb; do
    dpkg-deb -x "$package" "$COFFEE_BLENDER_LIBS/root"
  done
)
export LD_LIBRARY_PATH="$COFFEE_BLENDER_LIBS/root/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
/workspace/personal/tools/blender-4.5.4-linux-x64/blender --background --version
```

APT packages resolve from the current Ubuntu repositories; this is not an OS dependency lock. The Blender archive itself is pinned by SHA-256. No GPU, purchase or external rendering service is needed.
