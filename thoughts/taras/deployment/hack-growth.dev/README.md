# hack-growth.dev coffee demo deployment guide

Date: 2026-09-19. Baseline: `c599bd9988b9fff95c78209040e31e28697a4041`.

This document is a deployment plan. It does not record a deployment, a DNS
change, a package installation, or a server configuration change.

## Verified facts

- The `hackspain` SSH alias reaches the swarm host.
- The host runs Docker, Docker Compose `v5.5.1`, and Caddy `v2.11.4`.
- Caddy listens publicly on ports 80 and 443 in the `compose-caddy-1` container.
- Existing services use `/srv/compose/docker-compose.yml` and `compose_default`.
- Caddy reads `/etc/caddy/Caddyfile`. Its management source is not yet known.
- The host has 32 CPUs, 122 GiB RAM, and 27 GiB free on `/srv`.
- The host Python is 3.12.3. The coffee guide requires Python 3.13.
- `sim/coffee_sorter/live.py` runs one shared engine and worker process.
- Continuous mode starts without a browser. Its preset disables time limits.
- `/health`, `/state`, and `/ws` are the current health, state, and WebSocket routes.
- The selected frozen model and manifest are tracked as compressed source artifacts.

The existing Caddy configuration validates. This inspection did not read its
contents or change its configuration.

## Public deployment blocker

The current service cannot safely serve `https://hack-growth.dev`.

`sim/coffee_sorter/live.py` accepts only `--host 127.0.0.1`. Its middleware
allows only loopback Host and Origin values. `sim/coffee_sorter/live_web/live.js`
constructs `ws://` URLs. A browser on an HTTPS page must use `wss://`.
`live.py` currently serves only `/` and `/live.js`. It does not serve 3D assets.

Complete this small application change before deployment:

1. Keep `127.0.0.1` as the local default.
2. Add an explicit public origin configuration. Example value: `https://hack-growth.dev`.
3. Allow only the configured public Host and Origin in public mode.
4. Permit an internal Docker listener such as `0.0.0.0:8890` in public mode.
5. Build the browser WebSocket URL from `location.protocol`.
6. Keep the browser and WebSocket on the same public origin.
7. Add a reviewed same-origin route for required 3D assets.

Do not rewrite Host or Origin headers in Caddy to bypass the current checks.
The application must validate the public host itself.

## Target shape

```text
browser
  HTTPS and WSS
       |
Caddy on the swarm host
       |
compose_default network
       |
one coffee service and one shared engine
       |
read-only frozen model and UI assets     writable evidence volume
```

Run exactly one coffee service replica. Each replica creates a separate
simulation. Horizontal replicas cannot share one engine session or score epoch.

The coffee service must not publish port 8890 on the host. Caddy reaches it
through `compose_default` by its Compose service name.

## DNS and HTTPS

Before Caddy receives the site configuration, create an apex `A` record:

```text
hack-growth.dev.  A  <IPv4 address resolved by: ssh -G hackspain>
```

Add an `AAAA` record only after the host has a public IPv6 address. Do not
guess one. Check propagation from an external resolver:

```bash
dig +short A hack-growth.dev
dig +short AAAA hack-growth.dev
```

Caddy obtains and renews public certificates when the public name resolves to
the host and ports 80 and 443 are reachable. Its
[HTTPS guide](https://caddyserver.com/docs/quick-starts/https) documents this
requirement. The existing Caddy container already owns both ports.

After the public-origin code change, the required Caddy route is:

```caddyfile
hack-growth.dev {
    reverse_proxy coffee:8890
}
```

Caddy reverse proxy supports WebSocket upgrades without a separate upgrade
block. See the [Caddy reverse proxy documentation](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy).

The source that builds or mounts `/etc/caddy/Caddyfile` remains unconfirmed.
Confirm that source before changing any route. Do not edit a container-only
file if Compose recreates the container from another source.

## Coffee image and persistent paths

Build a dedicated image from the stated baseline. It needs Python 3.13 and
the packages pinned in:

```text
thoughts/taras/research/coffee-quality/requirements-resolved.txt
```

Validate the selected model during image build or release preparation. Do not
retrain when the service starts.

```bash
mkdir -p sim/coffee_sorter/models
gzip -dc thoughts/taras/research/coffee-quality/model-selected.joblib.gz \
  > sim/coffee_sorter/models/live_green_arabica.joblib
gzip -dc thoughts/taras/research/coffee-quality/model-selected.manifest.json.gz \
  > sim/coffee_sorter/models/live_green_arabica.manifest.json
sha256sum sim/coffee_sorter/models/live_green_arabica.joblib
```

The expected model SHA-256 is:

```text
89513398373c6e0e81286419962feb3e312742de14a76d02dd0d819ad5264a5a
```

The running service validates the manifest, model bytes, classes, features,
profile, layout, feed rate, and camera cadence before it starts.

Use three storage classes:

| Path class | Mode | Purpose |
| --- | --- | --- |
| selected model and manifest | read-only | frozen continuous engine input |
| `live_web` and reviewed 3D assets | read-only | page, JavaScript, GLB, and textures |
| `/var/lib/hackspain-coffee/runs` | writable persistent volume | reports, final state, service profile, and rotating command log |

Continuous mode writes `commands.jsonl` with 1 MiB rotation and two backups.
It also writes final state and a report when the process stops.

## Compose service contract

This is a template, not a checked-in Compose file. Replace every angle-bracket
value after the public-origin change and image validation.

```yaml
services:
  coffee:
    image: <coffee-image-built-from-c599bd9>
    command:
      - /app/.venv-coffee/bin/python
      - sim/coffee_sorter/live.py
      - --host
      - 0.0.0.0
      - --port
      - "8890"
      - --preset
      - sim/coffee_sorter/configs/continuous_demo.json
      - --out
      - /var/lib/hackspain-coffee/runs
      # Add exact public host and origin options after the preflight code change.
    restart: unless-stopped
    init: true
    environment:
      MUJOCO_GL: <validated-headless-backend>
    volumes:
      - <frozen-model-directory>:/app/sim/coffee_sorter/models:ro
      - <reviewed-static-assets>:/app/sim/coffee_sorter/visual_assets:ro
      - coffee-runs:/var/lib/hackspain-coffee/runs
    networks:
      - compose_default
    cpus: <benchmark-approved-cpu-limit>
    mem_limit: <benchmark-approved-memory-limit>

networks:
  compose_default:
    external: true

volumes:
  coffee-runs:
```

Use a dedicated service name, `coffee`, because the Caddy upstream references
that name. Do not publish a `ports:` entry. Do not use `deploy.replicas`.

Docker recommends a production-specific Compose file and explicit restart
policy. See [Docker Compose production guidance](https://docs.docker.com/compose/how-tos/production/).

The service already limits OMP, OpenBLAS, MKL, and VECLIB threads to one.
That does not prove capacity. The measured coffee engine remained below real
time on local hardware. Set CPU and memory limits only after a host benchmark.
Keep one core group available for Caddy and existing swarm services.

## Separate generation and retraining jobs

Do not place object generation, Blender rendering, or model training inside the
continuous service container. They can consume CPU, memory, disk, and provider
credentials while the public engine runs.

Run each as an explicit asynchronous job with its own output directory. Give
it a CPU and memory limit. Do not run it while the engine performance benchmark
or public demo needs exclusive resources.

The object generator requires `OPENROUTER_API_KEY` for uncached provider work.
Keep provider credentials in the host's existing secret mechanism. Do not put
them in images, Compose files, logs, or the public service environment.

Treat a generated object as data pending review. Publish it only after it is
validated, integrated into reviewed assets, and copied to a persistent
read-only asset path. A model update needs the paired manifest validation and
a controlled coffee-service restart.

## Public control decision

The current page permits injection commands from every connected browser. It
has rate and pending-command limits. It does not provide public-user identity.

Before public exposure, choose one policy:

1. Public visitors can inject the existing safe demo object.
2. Only invited users can inject through a small access control layer.

Record the chosen policy with the Caddy route. Do not add unrelated account or
administration features as part of this deployment.

## Launch, health, logs, and rollback

After all preflight items pass, validate the selected Compose configuration:

```bash
cd /srv/compose
docker compose -f docker-compose.yml -f <coffee-compose-file> config
```

Start or replace only the coffee service. Do not restart the full swarm stack.

```bash
docker compose -f docker-compose.yml -f <coffee-compose-file> up -d --no-deps coffee
docker compose -f docker-compose.yml -f <coffee-compose-file> ps coffee
docker compose -f docker-compose.yml -f <coffee-compose-file> logs --tail=200 coffee
```

`/health` returns HTTP 503 when the worker fails or continuous state heartbeats
stop for more than three seconds. It otherwise returns HTTP 200. `/state`
includes the session ID, mode, rolling scores, and retention counters.

Run this smoke test after DNS, HTTPS, and WSS are ready:

```bash
curl --fail --show-error https://hack-growth.dev/health
curl --fail --show-error https://hack-growth.dev/state > /tmp/hack-growth-state.json
curl --fail --show-error https://hack-growth.dev/live.js >/dev/null
```

Open `https://hack-growth.dev` in two browsers. Confirm one session ID,
continuous movement with no clients, HTTPS page delivery, and a `wss://`
connection. Inject one object only if the chosen public-control policy permits it.

Rollback means selecting the previous validated image and the previous frozen
model plus manifest. Preserve the run volume. Replace only `coffee` with the
same scoped Compose command. Check `/health`, `/state`, and the two-browser
session result after rollback.

## Deployment gate

Do not deploy until all items pass:

- The public-host and WSS application change is reviewed.
- Caddy configuration ownership is confirmed.
- DNS resolves to the swarm host.
- Caddy can obtain the certificate.
- The Python 3.13 image and headless MuJoCo backend pass a smoke start.
- The frozen model and manifest hash match.
- The coffee process has one replica and persistent run storage.
- A host benchmark sets CPU and memory limits.
- Taras selects the public injection policy.
- HTTPS, WSS, `/health`, `/state`, and two-browser sharing pass.

No server result is claimed by this document.
