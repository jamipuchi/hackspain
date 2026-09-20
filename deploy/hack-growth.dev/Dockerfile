# syntax=docker/dockerfile:1.7
FROM python:3.13-slim-bookworm@sha256:2325bb286ec344af3e5898cc224b5844e2707ac6e26b1632516fd3edc84a5e26

ARG SOURCE_REVISION
ARG BLENDER_VERSION=5.2.2
ARG BLENDER_SHA256=84098912789dc450e95697c4184fb8a90acbe5111c2ba4aede3fecb57806a168
ARG TARGETARCH
LABEL org.opencontainers.image.source="https://github.com/tarasyarema/hackspain"
LABEL org.opencontainers.image.revision="${SOURCE_REVISION}" \
      dev.cinta.blender.version="${BLENDER_VERSION}" \
      dev.cinta.blender.archive.sha256="${BLENDER_SHA256}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MUJOCO_GL=osmesa \
    PYOPENGL_PLATFORM=osmesa \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    VECLIB_MAXIMUM_THREADS=1 \
    CINTA_SOURCE_REVISION="${SOURCE_REVISION}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates curl libgl1 libglfw3 libice6 libosmesa6 libsm6 libxi6 \
       libxkbcommon0 libxrender1 xz-utils \
    && rm -rf /var/lib/apt/lists/*

RUN test "$TARGETARCH" = amd64 \
    && curl --fail --location --show-error \
       "https://download.blender.org/release/Blender5.2/blender-${BLENDER_VERSION}-linux-x64.tar.xz" \
       --output /tmp/blender.tar.xz \
    && printf '%s  %s\n' "$BLENDER_SHA256" /tmp/blender.tar.xz | sha256sum -c - \
    && tar --extract --xz --file /tmp/blender.tar.xz --directory /opt \
    && mv "/opt/blender-${BLENDER_VERSION}-linux-x64" /opt/blender \
    && ln -s /opt/blender/blender /usr/local/bin/blender \
    && rm /tmp/blender.tar.xz \
    && if ldd /opt/blender/blender | grep -q 'not found'; then ldd /opt/blender/blender; exit 1; fi \
    && blender --background --version

ENV HOME=/tmp/cinta-home \
    XDG_CACHE_HOME=/tmp/cinta-home/cache \
    XDG_CONFIG_HOME=/tmp/cinta-home/config \
    XDG_STATE_HOME=/tmp/cinta-home/state \
    TMPDIR=/tmp

WORKDIR /app

RUN dpkg-query -W -f='${binary:Package}=${Version}\n' \
       ca-certificates curl libgl1 libglfw3 libice6 libosmesa6 libsm6 libxi6 \
       libxkbcommon0 libxrender1 xz-utils \
       > /app/blender-packages.txt \
    && chmod 0444 /app/blender-packages.txt

COPY thoughts/taras/research/coffee-quality/requirements-resolved.txt /app/requirements-resolved.txt
RUN python -m pip install --no-cache-dir -r /app/requirements-resolved.txt

COPY sim/coffee_sorter/assets /app/sim/coffee_sorter/assets
COPY sim/coffee_sorter/configs/continuous_demo.json /app/sim/coffee_sorter/configs/continuous_demo.json
COPY sim/coffee_sorter/generator /app/sim/coffee_sorter/generator
COPY sim/coffee_sorter/live_web /app/sim/coffee_sorter/live_web
COPY sim/coffee_sorter/object_catalog /app/sim/coffee_sorter/object_catalog
COPY sim/coffee_sorter/visual_assets/browser /app/sim/coffee_sorter/visual_assets/browser
COPY sim/coffee_sorter/web/vendor /app/sim/coffee_sorter/web/vendor
COPY sim/coffee_sorter/assets.py \
     sim/coffee_sorter/bootstrap_model.py \
     sim/coffee_sorter/classifier.py \
     sim/coffee_sorter/controller.py \
     sim/coffee_sorter/engine.py \
     sim/coffee_sorter/item_job_generate.py \
     sim/coffee_sorter/item_job_physics.py \
     sim/coffee_sorter/item_job_render.py \
     sim/coffee_sorter/item_jobs.py \
     sim/coffee_sorter/live.py \
     sim/coffee_sorter/object_catalog.py \
     sim/coffee_sorter/object_definitions.py \
     sim/coffee_sorter/profiles.py \
     sim/coffee_sorter/requirements.txt \
     sim/coffee_sorter/rolling_scores.py \
     sim/coffee_sorter/scene.py \
     sim/coffee_sorter/sim.py \
     sim/coffee_sorter/train_candidate.py \
     sim/coffee_sorter/validate_bundle.py \
     sim/coffee_sorter/validate_object_route.py \
     sim/coffee_sorter/vision.py \
     /app/sim/coffee_sorter/

COPY thoughts/taras/research/coffee-quality/model-selected.joblib.gz /tmp/model.joblib.gz
COPY thoughts/taras/research/coffee-quality/model-selected.manifest.json.gz /tmp/model.manifest.json.gz
RUN mkdir -p /app/sim/coffee_sorter/models \
    && gzip -dc /tmp/model.joblib.gz > /app/sim/coffee_sorter/models/live_green_arabica.joblib \
    && gzip -dc /tmp/model.manifest.json.gz > /app/sim/coffee_sorter/models/live_green_arabica.manifest.json \
    && echo 'd793e25a3662585eda545cbe0dc84796d9fdd2e0787bdd89a10bfb441ed861ad  /app/sim/coffee_sorter/models/live_green_arabica.joblib' | sha256sum -c - \
    && echo 'c87ff9ecdb147917a5fa8c3967b8c62603aa15c2062004977365e3ca8e6d39cf  /app/sim/coffee_sorter/models/live_green_arabica.manifest.json' | sha256sum -c - \
    && rm /tmp/model.joblib.gz /tmp/model.manifest.json.gz

RUN python -m py_compile \
      /app/sim/coffee_sorter/object_catalog.py \
      /app/sim/coffee_sorter/object_definitions.py \
      /app/sim/coffee_sorter/item_jobs.py \
      /app/sim/coffee_sorter/item_job_generate.py \
      /app/sim/coffee_sorter/item_job_render.py \
      /app/sim/coffee_sorter/item_job_physics.py \
      /app/sim/coffee_sorter/validate_object_route.py \
      /app/sim/coffee_sorter/train_candidate.py \
      /app/sim/coffee_sorter/bootstrap_model.py \
      /app/sim/coffee_sorter/validate_bundle.py \
    && cd /app/sim/coffee_sorter \
    && python -c 'import item_jobs, object_catalog, profiles; object_catalog.load_catalog(object_catalog.CATALOG_ROOT)'

COPY deploy/hack-growth.dev/start.sh /app/deploy/start.sh
COPY deploy/hack-growth.dev/check_live.py /app/deploy/check_live.py
COPY deploy/hack-growth.dev/blender_smoke.py /app/deploy/blender_smoke.py
COPY deploy/hack-growth.dev/reset_live_state.py /app/deploy/reset_live_state.py
RUN printf '%s' "$SOURCE_REVISION" | grep -Eq '^[0-9a-f]{40}$' \
    && printf '%s\n' "$SOURCE_REVISION" > /app/source-revision.txt \
    && python -m py_compile /app/deploy/reset_live_state.py \
    && chmod 0555 /app/deploy/start.sh /app/deploy/check_live.py /app/deploy/reset_live_state.py \
    && chmod 0444 /app/deploy/blender_smoke.py \
    && groupadd --gid 10001 cinta \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent cinta

RUN install --directory --owner 10001 --group 10001 /tmp/blender-smoke \
    && su --shell /bin/sh cinta --command \
       'blender --background --threads 1 --factory-startup --python-exit-code 1 --python /app/deploy/blender_smoke.py -- /tmp/blender-smoke' \
    && test -s /tmp/blender-smoke/smoke.png \
    && test -s /tmp/blender-smoke/smoke.glb \
    && rm -rf /tmp/blender-smoke

USER 10001:10001
WORKDIR /app/sim/coffee_sorter
EXPOSE 8890
ENTRYPOINT ["/app/deploy/start.sh"]
