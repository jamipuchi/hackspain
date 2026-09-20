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

Paid mode authorizes each new provider stage automatically after a cache miss. Each stage can cost money.

A confirmed or uncertain response blocks another paid request for that stage. Cache-only recovery remains available after an interrupted response.

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

The old crossing score and the native bin score disagree on the same `4,902` mature trajectories.

The crossing score measured `95.72%` accuracy, `89.34%` capture, and `2.95%` Keep loss.

The native bin score measured `74.77%` accuracy, `46.84%` capture, and `12.89%` Keep loss.

Native scoring also measured `12.36%` spills and `6.45%` unresolved objects. Of the final active objects, `248` were older than five seconds.

These paired numbers compare two scoring rules on current trajectories. They are not a historical public-physics comparison.

The paired score is reproducible. The cause of long-lived active objects still needs verification.

Collection candidate 4 increased Keep loss from `4.0480%` to `5.4205%`. The candidate exceeded the allowed one-point regression.

The replacement keeps the accepted original collection geometry and honest native scoring. Congestion and stuck objects remain unresolved.

The first blue token rendered and passed physics. Training collected zero observations for its new label, so activation stopped as a hard failure.

The coral token rendered and passed physics. Training then exposed a stale victim label in the candidate preset.

The trainer fix is committed and passes real Engine and loader tests. The coral token has not completed activation with that fix.

## Reset defaults

Reset defaults restores the deployed built-in catalog, model, default policy, and a fresh session.

It preserves Wall of Fame history and referenced assets. It archives the removed active generated item before restoring defaults.

Terminal failed or invalid submissions remain visible as **Needs review** entries. A verified preview remains available when one exists.

The reset also creates recoverable backups of the prior active state and jobs. It does not delete bundles, history, or provider cache.

Focused reset tests pass. The complete reset flow still needs integrated browser and deployment E2E verification.

## Pending release evidence

The replacement is not a deployment claim yet. These checks remain pending:

1. Complete one real generated-item flow through training, activation, restart, and recovery.
2. Confirm warning and hard-failure behavior with the final bundled model.
3. Confirm Reset defaults preserves Wall of Fame entries and verified previews.
4. Record desktop and mobile browser flows.
5. Build the final image and verify source, model, bundle, provider cache, HTTPS, and WSS identities.

Generation, rendering, and physics passed for two fresh objects. No fresh object has activated yet.

## Evidence and commands

- [Current public deployment status](thoughts/taras/deployment/hack-growth.dev/README.md#current-status)
- [Generated-item Manual E2E](thoughts/taras/plans/2026-09-19-cinta-item-controls.md#manual-e2e)
- [Collection scorer evidence](thoughts/taras/qa/2026-09-20-cinta-collection-scorer.md)
- [Physics repair evidence](thoughts/taras/qa/2026-09-20-cinta-physics-repair.md)
