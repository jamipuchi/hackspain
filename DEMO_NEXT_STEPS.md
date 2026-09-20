# CINTA demo and next steps

## Working addresses

- Public demo: [https://hack-growth.dev/](https://hack-growth.dev/)
- Public health: [https://hack-growth.dev/health](https://hack-growth.dev/health)
- Public state: [https://hack-growth.dev/state](https://hack-growth.dev/state)
- Local demo: [http://127.0.0.1:8899/](http://127.0.0.1:8899/), when the canonical local service runs
- Replacement draft: [jamipuchi/hackspain PR #7](https://github.com/jamipuchi/hackspain/pull/7)

The public URL serves the last working release. Generated-item activation remains unavailable there.

## Short demo flow

1. Open the public demo and wait for the running state.
2. Show **Overview**, **Sorting**, and **Belt** to explain the machine path.
3. Switch between **3D** and **2D** to separate rendering from engine behavior.
4. Open **Items** and show the active built-in catalog with its Keep or Reject policy.
5. Open **Details** and point to the session, source, model, policy, and simulation speed.
6. Explain that `Sim 0.119557x` means the engine runs slower than wall-clock time.
7. Use **Drop test stone** only when a shared-session mutation is acceptable.
8. Compare the expected decision with the retained physical outcome.
9. State that generated-item creation is still a release candidate.

Do not promise that a Reject decision always reaches the Reject bin. One verified public Stone spilled.

Do not present browser FPS as simulation speed. Use the **Sim** badge for engine speed.

## Verify before presenting

Run these checks shortly before the demo:

```bash
curl --fail --show-error https://hack-growth.dev/health
curl --fail --show-error https://hack-growth.dev/state
curl --fail --show-error https://hack-growth.dev/live.js >/dev/null
curl --fail --show-error https://hack-growth.dev/timeline.mjs >/dev/null
```

Confirm that `/state` reports source `30758f7dd499027ffe76a8f7646377c0f94fa596` until the replacement deploys.

Use agent-browser for the presentation browser:

```bash
agent-browser skills get core
agent-browser open https://hack-growth.dev/
agent-browser snapshot
```

Confirm these points:

- HTTPS loads without a certificate warning.
- The page connects to `wss://hack-growth.dev/ws`.
- Simulation time advances without a browser command.
- The running session ID remains stable after reconnect.
- The Items modal opens and shows the built-in catalog.
- The page labels engine speed separately from browser FPS.
- Mobile panels start collapsed in a clean browser profile.

If you change a shared policy, record the starting policy first. Restore it after the demo.

## Replacement release path

Complete these steps in order:

1. Freeze the accepted combined source with the original collection geometry.
2. Run the authorized 16-second model refresh under the shared runtime lock.
3. Validate source hashes, catalog revision, model identity, and preserved seed-17 rows.
4. Integrate reviewed queue, worker, startup, activation, and visual commits.
5. Run one exact cached star job through creation, activation, restart, and recovery.
6. Run the coffee-only reset and confirm it preserves Wall of Fame history and assets.
7. Capture the final desktop and mobile flows with agent-browser.
8. Build the immutable image and run the read-only runtime checks.
9. Deploy with no credential mount and no paid-provider flag.
10. Verify private health, public HTTPS, public WSS, identities, recovery, and reset.

A cache miss must remain `operator_required`. It must not start a paid request.

The mixed-feed geometry candidate failed its quality gate. This demo defers that change and does not claim improved sorting quality.

## Runnable guides

- [Run one local service](README.md#run-one-local-service)
- [Live engine guide](sim/coffee_sorter/LIVE.md)
- [Generated-item Manual E2E](thoughts/taras/plans/2026-09-19-cinta-item-controls.md#manual-e2e)
- [Deployment build and rollback guide](thoughts/taras/deployment/hack-growth.dev/README.md)
- [Public deployment verification](thoughts/taras/deployment/hack-growth.dev/README.md#verification)
- [Known issues](KNOWN_ISSUES.md)

Refresh these facts after the replacement rollout. Record the deployed source, image digest, model hash, bundle hash, session ID, and final recordings.
