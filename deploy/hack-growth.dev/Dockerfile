# syntax=docker/dockerfile:1.7
FROM python:3.13-slim-bookworm@sha256:2325bb286ec344af3e5898cc224b5844e2707ac6e26b1632516fd3edc84a5e26

ARG SOURCE_REVISION
LABEL org.opencontainers.image.source="https://github.com/tarasyarema/hackspain"
LABEL org.opencontainers.image.revision="${SOURCE_REVISION}"

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
       ca-certificates libgl1 libglfw3 libosmesa6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY thoughts/taras/research/coffee-quality/requirements-resolved.txt /app/requirements-resolved.txt
RUN python -m pip install --no-cache-dir -r /app/requirements-resolved.txt

COPY sim/coffee_sorter/assets /app/sim/coffee_sorter/assets
COPY sim/coffee_sorter/configs/continuous_demo.json /app/sim/coffee_sorter/configs/continuous_demo.json
COPY sim/coffee_sorter/live_web /app/sim/coffee_sorter/live_web
COPY sim/coffee_sorter/visual_assets/browser /app/sim/coffee_sorter/visual_assets/browser
COPY sim/coffee_sorter/web/vendor /app/sim/coffee_sorter/web/vendor
COPY sim/coffee_sorter/assets.py \
     sim/coffee_sorter/classifier.py \
     sim/coffee_sorter/controller.py \
     sim/coffee_sorter/engine.py \
     sim/coffee_sorter/live.py \
     sim/coffee_sorter/profiles.py \
     sim/coffee_sorter/rolling_scores.py \
     sim/coffee_sorter/scene.py \
     sim/coffee_sorter/sim.py \
     sim/coffee_sorter/vision.py \
     /app/sim/coffee_sorter/

COPY thoughts/taras/research/coffee-quality/model-selected.joblib.gz /tmp/model.joblib.gz
COPY thoughts/taras/research/coffee-quality/model-selected.manifest.json.gz /tmp/model.manifest.json.gz
RUN mkdir -p /app/sim/coffee_sorter/models \
    && gzip -dc /tmp/model.joblib.gz > /app/sim/coffee_sorter/models/live_green_arabica.joblib \
    && gzip -dc /tmp/model.manifest.json.gz > /app/sim/coffee_sorter/models/live_green_arabica.manifest.json \
    && echo '89513398373c6e0e81286419962feb3e312742de14a76d02dd0d819ad5264a5a  /app/sim/coffee_sorter/models/live_green_arabica.joblib' | sha256sum -c - \
    && echo '6409f13385bc0e7335ed48546a13999dd3e5d10a23d48491baeeae1e95015387  /app/sim/coffee_sorter/models/live_green_arabica.manifest.json' | sha256sum -c - \
    && rm /tmp/model.joblib.gz /tmp/model.manifest.json.gz

COPY deploy/hack-growth.dev/start.sh /app/deploy/start.sh
COPY deploy/hack-growth.dev/check_live.py /app/deploy/check_live.py
RUN printf '%s' "$SOURCE_REVISION" | grep -Eq '^[0-9a-f]{40}$' \
    && printf '%s\n' "$SOURCE_REVISION" > /app/source-revision.txt \
    && chmod 0555 /app/deploy/start.sh /app/deploy/check_live.py \
    && groupadd --gid 10001 cinta \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent cinta

USER 10001:10001
WORKDIR /app/sim/coffee_sorter
EXPOSE 8890
ENTRYPOINT ["/app/deploy/start.sh"]
