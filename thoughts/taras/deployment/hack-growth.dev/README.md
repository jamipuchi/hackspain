# hack-growth.dev CINTA deployment guide

Date: 2026-09-20.

This guide deploys the current CINTA demo. It does not enable generated-item
work, provider calls, training, or automatic model activation.

The historical infrastructure inspection used commit
`c599bd9988b9fff95c78209040e31e28697a4041`. A release must use the selected
reviewed commit instead. Record that full SHA before each build.

## Verified infrastructure

- SSH alias `hackspain` reaches host `hack-spain-swarm`.
- The host is x86_64 and runs Docker 29.8.1 with Compose 5.5.1.
- The host has 32 CPUs and 122 GiB RAM.
- Public IPv4 `142.132.165.127` belongs to that host.
- `compose-caddy-1` owns ports 80 and 443.
- `/srv/compose/docker-compose.yml` owns the running Compose stack.
- The shared Docker network is `compose_default`.
- Caddy serves the existing API, file, and object-store routes.
- Vercel manages the `hack-growth.dev` DNS zone.
- The apex and wildcard currently point at Vercel.

The existing Caddy source uses an inline Compose config. The deployment must
move that exact content into `/srv/compose/Caddyfile`. It must preserve every
existing route.

## Reviewed implementation

Deployment files live under `deploy/hack-growth.dev/`:

- `Dockerfile` builds the Python 3.13 service image.
- `build.sh` requires a clean committed checkout.
- `compose.yml` defines one private `coffee` service.
- `start.sh` creates one unique evidence directory for each start.
- `check_live.py` requires a healthy worker and advancing simulation time.
- `Caddyfile` preserves existing routes and adds `hack-growth.dev`.
- `release.env.example` documents immutable release values.

`sim/coffee_sorter/live.py` keeps loopback defaults. Public mode requires an
explicit bind, allowed Host, and allowed Origin.

The required public command is:

```bash
python /app/sim/coffee_sorter/live.py \
  --host 0.0.0.0 \
  --port 8890 \
  --allowed-host hack-growth.dev \
  --allowed-origin https://hack-growth.dev \
  --preset /app/sim/coffee_sorter/configs/continuous_demo.json \
  --out <unique-run-directory>
```

Missing public Host or Origin values fail startup. Exact matching rejects
credentials, paths, wildcards, and unrelated origins.

## Public behavior

The public page exposes these controls:

- one Stone injection button,
- ten shared Keep or Reject policy toggles.

A same-origin WebSocket client can inject any supported catalog class. It can
also change the shared policy. The current demo has no visitor identity or
login layer.

Admission bounds apply to the whole service:

- four connected clients,
- 16 pending commands,
- 256 admitted commands per 60-second epoch.

The continuous command surface contains only `inject` and
`set_reject_policy`. Continuous restart returns HTTP 409. The protocol has no
pause, training, file path, preset path, or model path command.

Public startup uses the unchanged specialty preset. It does not inherit the
local Keep-all diagnostic session. Keep-all can still trigger air through the
anomaly rule.

## Image contract

The image pins the Python base by digest. It installs the resolved dependency
set and a headless OSMesa runtime. It runs as UID and GID 10001.

The image contains the frozen model and adjacent manifest. The build verifies
these SHA-256 values:

```text
model     89513398373c6e0e81286419962feb3e312742de14a76d02dd0d819ad5264a5a
manifest  6409f13385bc0e7335ed48546a13999dd3e5d10a23d48491baeeae1e95015387
```

The build records the full release SHA in the image label and
`/app/source-revision.txt`. The worker exports the same value through
`/state.source_revision`. Startup fails if Compose supplies a different SHA.

`Dockerfile.dockerignore` allows only runtime source, reviewed assets, exact
model archives, and deployment scripts into the build context.

Build only a clean reviewed commit:

```bash
set -eu
RELEASE_SHA=<reviewed-full-sha>
git fetch origin main
git merge-base --is-ancestor "$RELEASE_SHA" origin/main
git switch --detach "$RELEASE_SHA"
test -z "$(git status --porcelain)"
./deploy/hack-growth.dev/build.sh > /tmp/cinta-release.env
cat /tmp/cinta-release.env
```

Record the full image ID from `CINTA_IMAGE`. Do not use a mutable tag as the
Compose release value.

## Host paths

Run every host command from this section through Rollback inside one root
shell. This boundary makes the private redirects and backup reads consistent.

```bash
ssh hackspain
sudo -i
set -eu
test "$(id -u)" -eq 0
umask 077
```

Use these persistent paths:

```text
/srv/hackspain-coffee/
  deploy/
    compose.yml
    release.env
  releases/<release-sha>/source/
  runs/<start-id>/
  secrets/generated-items.env
```

Create the evidence root before Compose starts. The service cannot repair host
ownership because it runs without root privileges.

```bash
sudo install -d -o root -g root -m 0755 /srv/hackspain-coffee
sudo install -d -o 10001 -g 10001 -m 0750 /srv/hackspain-coffee/runs
sudo install -d -o root -g root -m 0755 /srv/hackspain-coffee/deploy
sudo install -d -o root -g root -m 0755 /srv/hackspain-coffee/releases
sudo install -d -o root -g root -m 0700 /srv/hackspain-coffee/secrets
```

Each process creates a unique directory under `runs/`. Continuous mode writes
a rotating `commands.jsonl`. Rotation uses 1 MiB files and two backups. A
controlled stop writes `final-state.json` and `report.json`.

Preserve every run directory during upgrades and rollback.

## Release installation

Clone the public fork into a new immutable release directory. Check out the
exact reviewed SHA.

```bash
set -eu
RELEASE_SHA=<reviewed-full-sha>
RELEASE_ROOT="/srv/hackspain-coffee/releases/$RELEASE_SHA"
sudo install -d -o "$USER" -g "$USER" -m 0755 "$RELEASE_ROOT"
git clone https://github.com/tarasyarema/hackspain.git "$RELEASE_ROOT/source"
git -C "$RELEASE_ROOT/source" checkout --detach "$RELEASE_SHA"
test "$(git -C "$RELEASE_ROOT/source" rev-parse HEAD)" = "$RELEASE_SHA"
test -z "$(git -C "$RELEASE_ROOT/source" status --porcelain)"
cd "$RELEASE_ROOT/source"
./deploy/hack-growth.dev/build.sh > /tmp/cinta-release.env
```

Copy the reviewed deployment files and the generated environment file:

```bash
sudo install -o root -g root -m 0644 \
  deploy/hack-growth.dev/compose.yml \
  /srv/hackspain-coffee/deploy/compose.yml
sudo install -o root -g root -m 0600 \
  /tmp/cinta-release.env \
  /srv/hackspain-coffee/deploy/release.env
```

Confirm the environment contains the exact release SHA and immutable image ID.

## Compose preflight

Create one private backup directory from the root shell. Capture the existing
service state before any change. The rendered Compose files can contain secrets.

```bash
set -eu
umask 077
BACKUP_DIR="/srv/hackspain-coffee/backups/$(date -u +%Y%m%dT%H%M%SZ)"
sudo install -d -o root -g root -m 0700 "$BACKUP_DIR"
cd /srv/compose
sudo docker compose --env-file .env -f docker-compose.yml \
  ps --format json > "$BACKUP_DIR/services-before.json"
sudo docker inspect compose-caddy-1 > "$BACKUP_DIR/caddy-before.json"
sudo docker cp compose-caddy-1:/etc/caddy/Caddyfile \
  "$BACKUP_DIR/Caddyfile.before"
sudo cp docker-compose.yml "$BACKUP_DIR/docker-compose.before.yml"
sudo cp .env "$BACKUP_DIR/compose.env.before"
sudo docker compose --env-file .env -f docker-compose.yml \
  config --format json > "$BACKUP_DIR/compose.before.json"
sudo chmod 0600 "$BACKUP_DIR"/*
```

Render the candidate without changing services:

```bash
cd /srv/compose
docker compose \
  --env-file /srv/compose/.env \
  --env-file /srv/hackspain-coffee/deploy/release.env \
  -f docker-compose.yml \
  -f /srv/hackspain-coffee/deploy/compose.yml \
  config --format json > "$BACKUP_DIR/compose.candidate.json"
chmod 0600 "$BACKUP_DIR/compose.candidate.json"
```

Compare the existing services after removing only the intended coffee, Caddy
config, and shared-network additions:

```bash
python3 - "$BACKUP_DIR" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
before = json.loads((root / "compose.before.json").read_text())
candidate = json.loads((root / "compose.candidate.json").read_text())
candidate.get("services", {}).pop("coffee", None)
candidate.get("configs", {}).pop("caddyfile", None)
candidate.get("networks", {}).pop("coffee_shared", None)
before.get("configs", {}).pop("caddyfile", None)
if before != candidate:
    raise SystemExit("Unrelated rendered Compose configuration changed.")
PY
```

The rendered service must meet these rules:

- exactly one `coffee` service,
- no published host port,
- one worker process,
- external network `compose_default`,
- persistent evidence bind mount,
- 8 CPU, 8 GiB, and 256 PID limits,
- read-only root filesystem,
- full `CINTA_SOURCE_REVISION` value.

Start only the coffee service:

```bash
docker compose \
  --env-file /srv/compose/.env \
  --env-file /srv/hackspain-coffee/deploy/release.env \
  -f docker-compose.yml \
  -f /srv/hackspain-coffee/deploy/compose.yml \
  up -d --no-deps coffee
```

Do not recreate the complete Compose stack.

## Caddy ownership repair

Install the reviewed Caddyfile as durable source:

```bash
sudo install -o root -g root -m 0644 \
  <release-source>/deploy/hack-growth.dev/Caddyfile \
  /srv/compose/Caddyfile
```

Change only the `caddyfile` config entry in
`/srv/compose/docker-compose.yml`. Replace inline content with this file:

```yaml
configs:
  caddyfile:
    file: ./Caddyfile
```

Validate the candidate before reload:

```bash
cd /srv/compose
docker compose \
  --env-file /srv/compose/.env \
  --env-file /srv/hackspain-coffee/deploy/release.env \
  -f docker-compose.yml \
  -f /srv/hackspain-coffee/deploy/compose.yml \
  config --format json > "$BACKUP_DIR/compose.caddy-candidate.json"
chmod 0600 "$BACKUP_DIR/compose.caddy-candidate.json"
docker cp Caddyfile compose-caddy-1:/etc/caddy/Caddyfile.candidate
docker exec compose-caddy-1 caddy validate \
  --config /etc/caddy/Caddyfile.candidate \
  --adapter caddyfile
```

After validation, install the candidate at the canonical container path. Then
reload Caddy gracefully:

```bash
docker cp Caddyfile compose-caddy-1:/etc/caddy/Caddyfile
docker exec compose-caddy-1 caddy reload \
  --config /etc/caddy/Caddyfile \
  --adapter caddyfile
```

Restore `$BACKUP_DIR/Caddyfile.before` and reload it if the new route fails.
Restore `$BACKUP_DIR/docker-compose.before.yml` if the durable source change
fails.

## DNS

Change only the apex `hack-growth.dev` record. Point it to
`142.132.165.127`. Preserve the wildcard and every unrelated record.

Do not add an AAAA record without a verified public IPv6 address.

Verify propagation:

```bash
dig +short A hack-growth.dev
dig +short AAAA hack-growth.dev
```

Caddy obtains the certificate after the apex resolves and ports 80 and 443
reach the host.

## Verification

First verify the private service from its container:

```bash
docker exec compose-coffee-1 python /app/deploy/check_live.py --window 12
```

Check release identity:

```bash
python3 - <<'PY'
import json
from urllib.request import urlopen

expected_source = "<reviewed-full-sha>"
expected_model = "89513398373c6e0e81286419962feb3e312742de14a76d02dd0d819ad5264a5a"
with urlopen("https://hack-growth.dev/state", timeout=10) as response:
    state = json.load(response)
assert state["source_revision"] == expected_source, state["source_revision"]
assert state["model_version"] == expected_model, state["model_version"]
PY
```

Check page assets and status:

```bash
curl --fail --show-error https://hack-growth.dev/health
curl --fail --show-error https://hack-growth.dev/live.js >/dev/null
curl --fail --show-error https://hack-growth.dev/timeline.mjs >/dev/null
```

Use `agent-browser` for the real browser check:

```bash
agent-browser skills get core
agent-browser open https://hack-growth.dev/
agent-browser snapshot
```

Confirm all items:

1. The page uses HTTPS.
2. The browser connects through `wss://hack-growth.dev/ws`.
3. Simulation time advances without a browser command.
4. Two clients show one stable session ID.
5. One Stone injection produces one retained manual result.
6. One same-set policy update is a no-op.
7. The policy and command bounds remain visible.

Capture service state after verification in the private backup directory:

```bash
cd /srv/compose
docker compose --env-file /srv/compose/.env -f docker-compose.yml \
  ps --format json > "$BACKUP_DIR/services-after.json"
chmod 0600 "$BACKUP_DIR/services-after.json"
```

Compare service names, images, and state. Only `coffee` and the intended Caddy
configuration may differ.

## Controlled stop evidence

For a planned stop, send Compose a 30-second grace period. Then inspect the
newest run directory.

```bash
docker stop -t 30 compose-coffee-1
find /srv/hackspain-coffee/runs -maxdepth 2 -type f \
  \( -name final-state.json -o -name report.json -o -name commands.jsonl \) \
  -print
```

Restart with the same scoped Compose command. A restart creates a new session
and score epoch.

## Rollback

Record these values before activation:

- prior immutable coffee image ID, if one exists,
- prior model release and model SHA,
- prior `release.env`,
- prior `/srv/compose/docker-compose.yml`,
- prior active Caddyfile,
- prior service inventory.

Rollback selects one complete prior release. It never mixes a model, manifest,
policy, or source from different releases.

Restore the prior release environment and Compose source. Replace only coffee:

```bash
docker compose \
  --env-file /srv/compose/.env \
  --env-file /srv/hackspain-coffee/deploy/release.env \
  -f /srv/compose/docker-compose.yml \
  -f /srv/hackspain-coffee/deploy/compose.yml \
  up -d --no-deps coffee
```

Restore and reload the prior Caddyfile if routing caused the failure. Preserve
all run directories. Repeat private health, source, model, HTTPS, WSS, and
two-client checks after rollback.

The first deployment has no prior coffee image. Its rollback removes only the
new coffee service:

```bash
docker compose \
  --env-file /srv/compose/.env \
  --env-file /srv/hackspain-coffee/deploy/release.env \
  -f /srv/compose/docker-compose.yml \
  -f /srv/hackspain-coffee/deploy/compose.yml \
  rm -sf coffee
```

Then restore the saved Compose file and Caddyfile from `$BACKUP_DIR`. Reload
the restored Caddyfile. Preserve every coffee run directory.

Before DNS mutation, record the authoritative apex response. The initial apex
uses Vercel's default ALIAS to `cname.vercel-dns-017.com.`. Initial rollback
removes only the explicit apex A record. It then verifies that the default apex
ALIAS returns. The wildcard and unrelated records remain unchanged.

## Provider credentials for future work

The current coffee service does not receive provider credentials. Future
generated-item workers use a separate root-owned file:

```text
/srv/hackspain-coffee/secrets/generated-items.env
```

The parent directory uses mode 0700. The file uses owner `root:root` and mode
0600. Only these names are currently required:

- `OPENROUTER_API_KEY`
- `TYPESAFE_API_KEY`

Do not put values in Git, images, public assets, logs, or the live engine
environment.

## Future generated items

The [generated-item controls plan](../../plans/2026-09-19-cinta-item-controls.md)
defines the future queue, validation, training, activation, and rollback flow.
This deployment does not implement that feature.

Future work uses one persistent root:

```text
/var/lib/hackspain-coffee/item-control/
  catalog/
  catalog/wall-of-fame/
  jobs/
```

The future worker must keep provider credentials separate from the engine. It
must allow only one trainer process. It must validate complete activation
bundles before a coordinated engine restart.

## Current status

The public deployment is active at `https://hack-growth.dev/`.

The deployed source is
`30758f7dd499027ffe76a8f7646377c0f94fa596`. The immutable amd64 image is
`sha256:5b6e55122ff3a6df0369913e92adcd320038fd558b5132a3a133b1a7cd06c238`.

The service, Caddy route, apex DNS record, production certificate, HTTPS page,
and WSS connection passed verification. See the
[public deployment QA report](../../qa/2026-09-20-cinta-public-deployment.md).

The measured public engine rate was about `0.1196x`. The system remains below
physical real time. One verified Stone injection spilled despite an expected
Reject result. The QA report preserves that negative result.
