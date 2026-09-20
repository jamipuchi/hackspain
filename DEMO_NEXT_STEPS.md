# CINTA demo and next steps

## Working addresses

- Public demo: [https://hack-growth.dev/](https://hack-growth.dev/)
- Public health: [https://hack-growth.dev/health](https://hack-growth.dev/health)
- Public state: [https://hack-growth.dev/state](https://hack-growth.dev/state)
- Local demo: [http://127.0.0.1:8899/](http://127.0.0.1:8899/), when the canonical local service runs
- Replacement draft: [jamipuchi/hackspain PR #7](https://github.com/jamipuchi/hackspain/pull/7)

The public URL serves the last working release. Generated-item creation and Reset defaults are not deployed there.

## Current public demo

1. Open the public page and wait for the running state.
2. Show **Overview**, **Sorting**, and **Belt**.
3. Switch between **3D** and **2D**.
4. Open **Items** and show the built-in catalog and shared policy.
5. Open **Details** and show source, model, session, policy, browser FPS, and simulation speed.
6. Explain that `Sim 0.119557x` means the engine runs slower than wall time.
7. Drop a test Stone only when a shared-session mutation is acceptable.
8. Compare the expected decision with the retained physical outcome.

One verified public Stone spilled. Do not promise that a Reject decision always reaches the Reject bin.

Browser FPS measures rendering. The **Sim** badge measures simulated seconds per wall second.

## Replacement candidate for judges

The candidate accepts an item description. Paid mode authorizes each new provider stage automatically after a cache miss.

The queue prevents another paid request after a confirmed or uncertain response for the same stage.

It renders the item, validates its asset, estimates physics, retrains the classifier, and prepares an immutable activation bundle.

Hard failures block activation. These include invalid assets, failed training, insufficient coverage, and new-label recall below 90%.

Physical Keep, anomaly, and overall accuracy failures appear as **Needs review** warnings. Judges should see these warnings as measured limitations.

Two fresh objects completed provider generation, rendering, and physics checks. Neither object completed activation.

The blue token produced no new-label training observations. The coral token exposed a stale victim label in the candidate preset.

The coral trainer fix passes focused real Engine and loader tests. The full corrected flow still needs E2E verification.

### Note for judges

This is a simulator demo with estimated generated-object physics. The engine runs slower than real time.

The active catalog keeps a fixed object count. Replacement prefers an eligible kept item, then a non-protected Reject item.

Current native-bin scoring measures `74.77%` accuracy and `12.89%` Keep loss on one paired sample.

Provider cache replay can recover an identical answered request. It does not prove fresh generation for arbitrary descriptions.

Generated-item activation, Reset defaults, and production hardening remain unfinished until the final E2E passes.

## Reset defaults

Run a safe preview from the repository checkout:

```bash
./deploy/hack-growth.dev/reset-live-state.sh --dry-run
```

Apply the reset from the laptop only after the dry run succeeds:

```bash
./deploy/hack-growth.dev/reset-live-state.sh --apply
```

The script targets only the `coffee` service on SSH host `hackspain`.

Reset defaults restores the built-in catalog and model. It preserves Wall of Fame history, generated assets, failed submissions, and verified previews.

The integrated button and laptop flow still need final deployment E2E proof.

## Verify before presenting

Run these checks shortly before the public demo:

```bash
curl --fail --show-error https://hack-growth.dev/health
curl --fail --show-error https://hack-growth.dev/state
curl --fail --show-error https://hack-growth.dev/live.js >/dev/null
curl --fail --show-error https://hack-growth.dev/timeline.mjs >/dev/null
```

Confirm that `/state` reports source `30758f7dd499027ffe76a8f7646377c0f94fa596` until the replacement deploys.

Use agent-browser for final browser evidence:

```bash
agent-browser skills get core
agent-browser open https://hack-growth.dev/
agent-browser snapshot
```

Confirm HTTPS, WSS, advancing simulation time, stable reconnect, shared sessions, Items, warnings, generated assets, mobile layout, and Reset defaults.

## Release path

1. Freeze the reviewed combined source and final bundled model.
2. Complete one real generated-item flow through automatic paid stage authorization.
3. Verify hard failures, warnings, activation, restart, and recovery.
4. Run Reset defaults and verify preserved Wall of Fame evidence.
5. Capture desktop and mobile browser evidence.
6. Build the immutable image and verify all identities.
7. Deploy, then verify private health, public HTTPS, public WSS, recovery, and reset.

## Runnable guides

- [Run one local service](README.md#run-one-local-service)
- [Live engine guide](sim/coffee_sorter/LIVE.md)
- [Generated-item Manual E2E](thoughts/taras/plans/2026-09-19-cinta-item-controls.md#manual-e2e)
- [Deployment and rollback guide](thoughts/taras/deployment/hack-growth.dev/README.md)
- [Known issues](KNOWN_ISSUES.md)
