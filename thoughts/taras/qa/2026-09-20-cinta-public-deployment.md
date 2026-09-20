# CINTA public deployment QA

Date: 2026-09-20

## Release

Public URL: `https://hack-growth.dev/`

Source revision:
`30758f7dd499027ffe76a8f7646377c0f94fa596`

Immutable amd64 image:
`sha256:5b6e55122ff3a6df0369913e92adcd320038fd558b5132a3a133b1a7cd06c238`

Frozen model:
`89513398373c6e0e81286419962feb3e312742de14a76d02dd0d819ad5264a5a`

The service uses the unchanged specialty policy. Good is Keep. The other nine
classes are Reject.

## Infrastructure result

The service runs as `compose-coffee-1`. It has no published host port. Caddy
reaches `coffee:8890` through `compose_default`.

The container runs as UID and GID 10001. It uses a read-only root filesystem,
an OSMesa backend, eight CPUs, 8 GiB memory, and a 256 PID limit.

The process inventory showed one live server, one multiprocessing resource
tracker, and one engine worker. The worker started without a browser.

The service remained healthy with one session. Simulation time advanced. The
state reported the exact source and model hashes.

The initial cumulative engine rate was `0.119557x`. This is about 11.96 percent
of physical real time. The deployment does not meet a real-time target.

The private rollback directory is:

```text
/srv/hackspain-coffee/backups/20260920T005400Z
```

It uses `root:root` ownership and mode 0700. Its files use mode 0600. It stores
the prior Compose source, environment, Caddyfile, service inventory, DNS state,
and rendered configuration.

Ten unrelated Compose services kept the same container identity and image.

## DNS and TLS

Vercel record `rec_d9fffa1e5e311286ea6c263f` adds this apex record:

```text
hack-growth.dev A 142.132.165.127
```

The wildcard and unrelated records remain unchanged. Vercel authoritative DNS,
Cloudflare, and Google returned `142.132.165.127`.

Caddy kept the three existing routes. It added only `hack-growth.dev` to
`coffee:8890`. The tracked, durable, and active Caddyfiles have SHA-256:

```text
00bcca56cca8fbafe901c1deb96cdc61a7c7b7681d809a0a27d37a2cd9a1dce1
```

The first certificate attempt saw stale Vercel addresses. Caddy retried after
the 60-second DNS TTL. Let’s Encrypt then issued a production certificate.

## Browser and WSS

The Mac system resolver still cached the old Vercel address during QA. The
isolated agent-browser session used this hostname mapping:

```text
hack-growth.dev 142.132.165.127
```

The mapping changed only that isolated browser session. It did not alter the
system resolver or a browser profile.

The mapped browser verified these results:

- HTTPS page delivery passed.
- `live.js`, `timeline.mjs`, Three.js, and GLTFLoader returned 200.
- The machine, Good, Black, and Broken GLBs returned 200.
- The favicon returned 404. This cosmetic request does not affect the app.
- The UI displayed `Synced`.
- `wss://hack-growth.dev/ws` returned the release source and model hashes.
- Two browser clients received session ID
  `18bb1eb6-4f71-4766-bedb-ad58af045825`.
- A 320 by 568 viewport had no document overflow.
- A 1440 by 900 viewport had no document overflow.

An independent normal-DNS check ran from the swarm host. It resolved the apex
to `142.132.165.127`. HTTPS and WSS both passed with the same engine session.

Screenshots:

```text
/private/tmp/cinta-public-mobile.png
/private/tmp/cinta-public-desktop.png
```

## Public command checks

One browser click sent one Stone injection. It created one retained manual
record. The final card showed:

```text
[!] Unexpected
Expected Reject
Actual Spilled
Decision stone, Pulse commanded
```

This negative result is preserved. The deployment does not claim that every
Stone reaches Reject.

A same-set policy command returned `ok=true` and `changed=false`. It preserved
the policy version and score epoch.

The public protocol still exposes bounded `inject` and `set_reject_policy`
commands. It exposes no training, file path, model path, pause, or restart
capability. Continuous restart returns HTTP 409.

## Persistence and rollback

A private network-isolated preflight used the production amd64 image. A clean
stop exited with code zero. It wrote `commands.jsonl`, `final-state.json`,
`report.json`, and `service-profile.json` to persistent host storage.

The public service writes to `/srv/hackspain-coffee/runs/`. Its restart policy
is `unless-stopped`.

Initial rollback removes only coffee. It restores the saved Compose and Caddy
sources. It removes only the new apex A record. It preserves every run
directory.

## Future worker credentials

The future worker credential file is:

```text
/srv/hackspain-coffee/secrets/generated-items.env
```

The parent uses mode 0700. The file uses `root:root` ownership and mode 0600.
It contains these selected variable names:

- `OPENROUTER_API_KEY`
- `TYPESAFE_API_KEY`

The current engine does not mount or load this file.

## Verification commands

```bash
docker exec compose-coffee-1 python /app/deploy/check_live.py --window 8
dig @ns1.vercel-dns.com hack-growth.dev A +short
dig @1.1.1.1 hack-growth.dev A +short
dig @8.8.8.8 hack-growth.dev A +short
agent-browser skills get core
agent-browser open https://hack-growth.dev/
agent-browser snapshot -i -c
```

Taras retains final functional acceptance.
