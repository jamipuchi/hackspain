# CINTA rejection calibration proposal

## Scope

This document proposes a bounded configuration screen after the Stone physics repair.
It does not select or apply a new setting.

The screen changes only `jet_force_n`.
It keeps pulse duration, controller timing, nozzle selection, splitter geometry, model, and policy behavior fixed.

## Current evidence

The canonical audit used the full 488-body pool and MuJoCo 3.13.0.
The audit source artifact has SHA-256 `96de77c6dba49475db91805f3757e2507e340d8cebabb8a03a826a5284087eb7`.
The accepted physics commits are `2f247d40f4478503fb0f3126ea69d9f0d30b8c62` and `5fcb45246bdfb31659463531b275e1e44a84c300`.
The accepted final `sim.py` has SHA-256 `9382e659b308eae550822d1638cc2e7a1db81f9e52b1897ff0eaad12138bd86d`.
The trusted model has SHA-256 `89513398373c6e0e81286419962feb3e312742de14a76d02dd0d819ad5264a5a`.

The corrected passive route accepted all eight unforced Stones.
The frozen Keep and Reject cases each activated 24 valve pulses.
All eight Stones received their own pulse contact.
Only two Stones reached Reject.
Six Stones reached Accept.
No Stone spilled.

| Quantity | Observed range |
| --- | ---: |
| Jet-region residence | 6 to 7 ms |
| Pulse start after region entry | 1.13 to 1.74 ms |
| Pulse end after region exit | 4.23 to 5.24 ms |
| Delivered impulse | 240 to 600 microN s |
| Split height after contact | 0.4517 to 0.5057 m |
| Reject threshold | 0.4750 m |

The timing evidence does not support a longer pulse.
Every pulse was on time and remained open after the object left the jet region.
The useful force window is limited by spatial residence.

The two successful rejects received 600 microN s.
Four failed routes also received 600 microN s.
The two other failures received 240 and 300 microN s.
Mass, nozzle overlap, pose, and trajectory therefore affect the result.

A linear trajectory estimate suggests 0.044 to 0.116 N across these eight Stones.
This estimate is a screening aid only.
MuJoCo contacts and rotations are nonlinear.

The balanced corrected audit classified 76 of 80 objects correctly.
It routed 70 of 80 objects to the policy-relative physical outcome.
It retained six of eight Good objects.
It rejected three of eight Stones.
It recorded zero spills.

Historical crowded-feed screens used the old physics.
At 1,000 objects per simulated second, 0.12 N performed worse than 0.06 N.
Reject capture fell from 51.45% to 40.97%.
Good loss rose from 4.08% to 6.16%.
Spill rose from 2.00% to 3.23%.
Those results prevent a direct jump to 0.12 N.
They do not predict corrected-physics performance.

## Proposed screen

Use three force values: 0.06 N, 0.075 N, and 0.09 N.
The 0.06 N row is the corrected baseline.
The other rows bound a small increase below the rejected 0.12 N historical setting.

Use tuning seeds 11, 17, 23, and 31.
Use held-out seeds 43, 59, 71, and 83.
Do not inspect held-out results until one candidate wins the tuning stage.

Each tuning force receives two checks:

1. Run isolated Stone Keep and Reject routes with two slot reuses per seed.
2. Run a mixed feed at 500 requested objects per simulated second for four simulated seconds per seed.

The mixed-feed runner must load `configs/continuous_demo.json`.
The existing `run.py bench` defaults use a different pool and a 2 ms physics step.
They cannot represent the public continuous preset without a dedicated diagnostic wrapper.

Record these values for each force and seed:

- Stone prediction and reject decision.
- Valve schedule, activation, lateness, and own contact.
- Jet residence time and delivered impulse.
- Stone outcome and split position.
- Policy-relative reject capture and Keep loss.
- Spill and unresolved counts.
- Requested, admitted, resolved, and starved counts.
- Wall seconds per simulated second.
- Peak resident memory.
- Source, model, configuration, and artifact hashes.

Select at most one candidate from tuning data.
Preserve every negative result.
Then compare the selected candidate with 0.06 N on the held-out seeds.

## Acceptance gates

The following gates apply to the held-out comparison:

- All unforced Stones must remain Accept.
- Every reported Reject must have an activated command and own contact.
- Stone Reject outcomes must improve over the corrected 0.06 N baseline.
- Aggregate reject capture must not regress.
- Keep loss may rise by at most one percentage point.
- Spill may rise by at most 0.5 percentage points.
- Admitted and resolved rates must remain within 5% of baseline.
- Pool starvation must not increase.
- Wall cost and peak memory must remain within 5% of baseline.
- Model hashes and classification counts must remain unchanged for isolated paired routes.

These gates detect regressions.
They do not establish production accuracy.
The simulator remains synthetic, and the sample remains small.

## Rejected first moves

Do not increase pulse duration first.
The current pulse already extends beyond the useful contact window.

Do not move the splitter first.
That change reclassifies passive trajectories and directly changes Keep loss.
The historical 100 mm splitter screen increased Good loss to 9.73%.

Do not change classifier, anomaly, or policy semantics.
The current failure occurs after correct classification, scheduling, activation, and contact.

## Evidence provenance

| Artifact | SHA-256 |
| --- | --- |
| `/private/tmp/cinta-physics/full_after.json` | `d197823b017f05845cc6826b9e0fd548562530bbd569161a5cd8fbc0881befd9` |
| `/private/tmp/cinta-physics/full_before.json` | `7c3ca3fe4f9a4a663cbd6f747248e767f56b1871a18081e5bd7e6a825841df77` |
| `/private/tmp/cinta-physics/balanced_after.json` | `1ed15981a4e0b0b25331a9105a825b3fd4871bcc3daec774e9340bc3def43100` |
| `/private/tmp/cinta-physics/performance.json` | `6e0762613c09baed9f0368617943dc3265e0cdd95beccb28e6f11d68d0394101` |
| `runs/tuning-sweep/force-0.06/metrics.json` | `69049726a42dcbe59dc177cea152a8200da613087d8df09f1fda71c0e79c9cd4` |
| `runs/tuning-sweep/force-0.12/metrics.json` | `49b13f08c3b0ac82d99cc19fe878cb17004066cb2c45ad788c5aa9a1c626c331` |

The full corrected audit supersedes the earlier reduced-pool 3 of 8 result.
Run the screen only from the accepted two-commit physics chain.
