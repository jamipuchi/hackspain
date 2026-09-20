# Collection scorer validation

The scorer now requires positive native contact with a named collector floor.
That contact emits one outcome and retires the object once.
Ground contact or escape past both end walls emits Spill.
The existing upstream lateral escape guard remains unchanged.
The shared settling deadline is 1.1 simulated seconds.

The prior splitter-height outcome and early retirement are removed.
Force, controller timing, model, sampling, and the 488-slot pool remain unchanged.
This revision names the existing end walls without moving any geometry.

## Scorer-only route evidence

Seeds 7, 8, 9, and 42 each use two consecutive pooled-body reuses for both Stone policies.
The second injection waits for actual retirement.

| Policy | Accept contact | Reject contact | Downstream Spill | Unresolved |
| --- | ---: | ---: | ---: | ---: |
| Keep | 6 | 0 | 2 | 0 |
| Reject | 8 | 0 | 0 | 0 |

These outcomes reproduce the preserved physical observer findings.
They demonstrate the geometry defect before the authorized geometry change.
Every object emits one outcome and retires once.
The maximum collection age is 0.848 seconds.

All sixteen initial masses, axes, poses, velocities, and body IDs match the preserved baseline.
Predicted classes, rejection decisions, scheduling decisions, jet-hit steps, and own-pulse event counts also match.
Delayed second injections alter camera phase and relative decision timing.
The largest relative fire-time change is 26.13 microseconds.
Two pulse durations differ by less than two nanoseconds.
Measured availability time differs because it contains wall-clock inference time.
The report preserves these differences rather than claiming exact control equality.

## Verification

All 57 focused tests pass under the shared exclusive runtime lock.
The test modules cover lifecycle, physical properties, continuous retention, metrics, and open-set evidence.
The evidence directory contains their complete output.
Compiled inspection confirms all five exported surfaces belong to world body zero.

Independent Standards review passes after replacing anonymous wall selection with stable names.
Spec review identified the expanded lateral guard and an insufficient old-cutoff regression.
The implementation restores the guard and tests a fresh airborne body beyond x=0.50.
The sixteen diagnostic routes supply the physical regression that the splitter-height test cannot provide.

`Bean.last_pos` and `Bean.last_quat` retain the contact solve pose before recycling.
The Engine owner must copy `last_quat` into retained history during integration.
The trainer owner must consume retained Engine outcomes instead of active pooled bodies.
Those ownership dependencies were sent to the coordinator.

## Artifacts

`evidence/2026-09-20-cinta-collection/scorer-stone-attempt-1` contains the sixteen object records and their comparisons.
Its saved runner hash matches the recorded source identity.
`evidence/cinta_collection_routes.py` is the reusable diagnostic command.
Each run obtains and releases `/private/tmp/hackspain-coffee-runtime.lock` independently.
No new seed, paid request, retraining, or deployment occurred.

The exact four-box geometry candidate remains a separate revision and validation stage.
