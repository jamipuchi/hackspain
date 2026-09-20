# CINTA known issues

This file separates the working public demo from the replacement candidate.

## Current public deployment

The working public demo is [https://hack-growth.dev/](https://hack-growth.dev/).

It runs source `30758f7dd499027ffe76a8f7646377c0f94fa596`. Generated-item creation and Reset defaults are not deployed there.

The public engine runs slower than wall time. Its measured release rate was `0.119557x` real time.

The **Sim** badge reports engine speed. Browser FPS reports rendering speed and does not measure sorting throughput.

One verified public Stone injection spilled after an expected Reject decision. The demo cannot guarantee that every object reaches its intended bin.

The public service uses one shared engine. Policy changes affect every visitor, and the service accepts at most four browser connections.

## Replacement candidate scope

The accepted candidate can use the real provider to generate an item. It then renders, validates, retrains, and prepares an activation bundle.

A real provider request needs an explicit operator grant and can cost money. A confirmed response must not trigger another paid request automatically.

Candidate validation uses a measured simulator heuristic. It is not a calibrated confidence estimate or a production accuracy guarantee.

Each trained label needs at least 30 observations and 10 unique training objects. Each holdout label needs at least 10 unique objects.

The new label needs at least 90% holdout recall. Missing coverage or lower new-label recall blocks activation.

Invalid assets, failed model training, incompatible presets, and incorrect label order also block activation.

The demo can show these physical quality failures as visible **Needs review** warnings:

- Overall holdout accuracy below 90%.
- Keep anomaly fraction above 5%.
- Fewer than 30 resolved Keep outcomes.
- Physical Keep acceptance below 95%.

These warnings disclose measured failures. They do not prove reliable sorting.

## Known sorting limits

Earlier baseline QA measured `92.24%` reject capture, `3.96%` Keep loss, and 11 spills across four runs.

Those results do not approve the replacement candidate or guarantee future outcomes.

Collection candidate 4 increased Keep loss from `4.0480%` to `5.4205%`. The candidate exceeded the allowed one-point regression.

The replacement keeps the accepted original collection geometry. Physical Keep loss, anomaly warnings, spills, and overall accuracy remain visible limitations.

## Reset defaults

Reset defaults restores the deployed built-in catalog, model, default policy, and a fresh session.

It preserves Wall of Fame history and referenced assets. It archives the removed active generated item before restoring defaults.

Terminal failed or invalid submissions remain visible as **Needs review** entries. A verified preview remains available when one exists.

The reset also creates recoverable backups of the prior active state and jobs. It does not delete bundles, history, or provider cache.

Focused reset tests pass. The complete reset flow still needs integrated browser and deployment E2E verification.

## Pending release evidence

The replacement is not a deployment claim yet. These checks remain pending:

1. Run one real generated-item flow through provider, render, physics, training, activation, restart, and recovery.
2. Confirm warning and hard-failure behavior with the final bundled model.
3. Confirm Reset defaults preserves Wall of Fame entries and verified previews.
4. Record desktop and mobile browser flows.
5. Build the final image and verify source, model, bundle, provider cache, HTTPS, and WSS identities.

## Evidence and commands

- [Current public deployment status](thoughts/taras/deployment/hack-growth.dev/README.md#current-status)
- [Generated-item Manual E2E](thoughts/taras/plans/2026-09-19-cinta-item-controls.md#manual-e2e)
- [Collection scorer evidence](thoughts/taras/qa/2026-09-20-cinta-collection-scorer.md)
- [Physics repair evidence](thoughts/taras/qa/2026-09-20-cinta-physics-repair.md)
