# CINTA known issues

This file separates the current public demo from the replacement release candidate.

## Current public deployment

The working public demo is [https://hack-growth.dev/](https://hack-growth.dev/).

It runs source `30758f7dd499027ffe76a8f7646377c0f94fa596`. Keep the deployed service on this source until the replacement passes every release gate.

Keep both main branches at their current accepted tips until the replacement passes every release gate.

The public engine runs below wall-clock speed. The measured release rate was `0.119557x` real time.

The **Sim** badge reports simulation speed. Browser FPS does not report engine speed or sorting throughput.

One verified public Stone injection spilled after an expected Reject decision. The current demo does not guarantee each object reaches its intended bin.

The public service uses one shared engine. Policy changes affect every visitor, and the service accepts at most four browser connections.

Generated-item creation and activation are not deployed. The public Items view shows only the active built-in catalog.

## Replacement candidate limits

The replacement release uses cached provider replay only. It mounts no provider credential file and performs no paid request.

An exact cache miss stops in `operator_required`. An operator must review any future uncached request before a paid call.

The approved star replay uses cached generation and a cached physics estimate. The estimate assumes a solid-gold contact box.

The contact box measures approximately `16.9 x 16.1 x 2.0 mm`. Its estimated mass is `0.010502674 kg` at `19300 kg/m3`.

The estimate does not measure the real object. It also does not model the star's concave valleys.

The visual GLB and the physics proxy serve different purposes. Final evidence must identify which representation each view uses.

Earlier baseline compatibility QA reported `92.24%` reject capture, `3.96%` Keep loss, and `11` spills across four runs.

Those older results are not current candidate metrics. They do not approve the replacement geometry, final model, or public rollout.

Collection candidate 4 failed the Keep-loss gate. It lost `194/3579`, or `5.4205%`, of Keep objects.

The baseline lost `145/3582`, or `4.0480%`. Candidate 4 increased loss by `1.3725` percentage points, above the `1.0` point limit.

No further physics tuning is authorized. The failed candidate remains evidence, not a release configuration.

The current activator also remains blocked. A drain-report failure can leave the engine rate at `0.0`.

A pointer `fsync` failure can split the active pointer from the running worker. Exact retry does not yet repair missing archive or history records.

The spawned failed-start and rollback path still needs a valid process proof. The activator stays excluded until focused corrections pass both reviews.

The candidate still needs final agent-browser recordings. These recordings must cover desktop, mobile, cached recovery, activation, rollback, and the generated GLB.

## Pending replacement gates

The replacement cannot deploy until these gates pass:

1. Accept the final collection geometry and physical outcome evidence.
2. Refresh the initial model from the frozen source with seeds 7 and 9 for exactly 16 seconds.
3. Validate the preserved seed-17 anomaly evidence without recollection or retuning.
4. Integrate only reviewed queue, worker, startup, activation, and visual commits.
5. Run one real cached job through render, physics, training, activation, restart, and rollback.
6. Prove the generated GLB appears in the live scene and the gallery.
7. Record final desktop and mobile browser flows with agent-browser.
8. Build the final image and verify its source, model, bundle, and cache identities.
9. Verify private health, public HTTPS, public WSS, recovery, and rollback.

## Fixed candidate findings

These fixes exist in reviewed candidate commits. They are not all deployed yet.

- Pooled objects now refresh supported MuJoCo mass and inertia state.
- Outcome scoring waits for collection instead of freezing at the splitter.
- The browser can load verified generated GLBs and use a labelled proxy fallback.
- WebGL context loss returns the page to the supported 2D view.
- Queue history, process groups, previews, and parent death have bounded handling.
- The Blender image runs as a non-root user with a read-only root filesystem.
- Cached release packaging includes no provider credentials.
- Model provenance binds the catalog revision and hashed training sources.

A fixed finding becomes a release claim only after combined integration and final end-to-end validation.

## Evidence and commands

- [Current public deployment status](thoughts/taras/deployment/hack-growth.dev/README.md#current-status)
- [Public deployment verification](thoughts/taras/deployment/hack-growth.dev/README.md#verification)
- [Generated-item Manual E2E](thoughts/taras/plans/2026-09-19-cinta-item-controls.md#manual-e2e)
- [Current collection scorer evidence](thoughts/taras/qa/2026-09-20-cinta-collection-scorer.md)
- [Physics repair evidence](thoughts/taras/qa/2026-09-20-cinta-physics-repair.md)
