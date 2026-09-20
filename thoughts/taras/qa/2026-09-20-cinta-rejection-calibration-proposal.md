# CINTA rejection calibration proposal

## Scope

This document records a bounded configuration screen after the Stone physics repair.
The tuning stage selected no new setting.
No production configuration changed.

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

## Tuning result

All cases used the accepted physics and trusted model.
Each mixed-feed case ran four simulated seconds at 500 requested objects per second.
Each force used four tuning seeds.
The combined mixed-feed cohort contains 5,200 objects per force.

| Force | Isolated Stone Reject | Mixed Stone Reject | Reject capture | Keep loss | Spill |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.060 N | 2/8 | 6/21 | 678/760, 89.21% | 185/4,440, 4.17% | 7/5,200, 0.135% |
| 0.075 N | 2/8 | 10/22 | 677/762, 88.85% | 202/4,438, 4.55% | 18/5,200, 0.346% |
| 0.090 N | 4/8 | 13/22 | 665/762, 87.27% | 220/4,438, 4.96% | 42/5,200, 0.808% |

All isolated decisions were on time and received their own pulse contact.
The impulse means were 360, 450, and 540 microN s.
Each force admitted exactly 500 objects per simulated second.
No case starved the pool.
No cohort object remained unresolved.

The 0.075 N setting improved mixed-feed Stone routing.
It did not improve isolated Stone routing.
It also reduced aggregate reject capture and increased Keep loss and spill.

The 0.090 N setting improved both Stone checks.
It reduced aggregate reject capture by 1.94 percentage points.
It increased Keep loss by 0.79 percentage points.
It increased spill by 0.67 percentage points.
The spill increase exceeds the 0.5 percentage-point rejection limit.

The tuning stage therefore selected no candidate.
The held-out seeds 43, 59, 71, and 83 were not run or inspected.
This negative result preserves the 0.060 N production setting.

Measured mean engine rates were 0.194x, 0.203x, and 0.222x.
Peak resident memory stayed between 298.7 and 300.2 MB across force groups.
Concurrent host load can affect wall speed.
The force change does not add a new runtime code path.

The compact tuning result is in `evidence/cinta-rejection-calibration/tuning-summary.json`.
That file records every raw artifact path, byte size, and SHA-256 hash.

## Repeatable commands

Use the prepared interpreter from the repository root.
Run one case per process so the shared lock is released between cases.

```bash
PYTHON=/Users/taras/Documents/code/hackspain/.venv-coffee/bin/python
SCRIPT=thoughts/taras/qa/evidence/cinta_rejection_calibration.py
OUT=/private/tmp/cinta-rejection-calibration/tuning

for force in 0.06 0.075 0.09
do
  for seed in 11 17 23 31
  do
    "$PYTHON" "$SCRIPT" isolated --force "$force" --seed "$seed" \
      --output "$OUT/isolated-force-$force-seed-$seed.json"
    "$PYTHON" "$SCRIPT" feed --force "$force" --seed "$seed" --seconds 4 \
      --output "$OUT/feed-force-$force-seed-$seed.json"
  done
done
```

Do not run held-out seeds when tuning selects no candidate.

## Smallest remaining investigation

Test one earlier pulse window at `lead_s = 0.003`.
Keep force at 0.060 N and keep pulse duration unchanged.
Do not edit controller code.

The controller schedules `t_on = t_fire - lead_s`.
It clamps this value only when the decision becomes available later.
The 220 mm camera-to-ejector path provides about 73 ms at 3 m/s.
Typical mixed-feed control paths were 8.1 to 8.5 ms at the median.
The 4 ms latency floor therefore leaves substantial nominal headroom.

The eight isolated baseline routes started 1.08 to 1.57 ms after jet-region entry.
Their pulse windows remained open 4.54 to 5.18 ms after region exit.
Adding 1.5 ms of lead should start between 0.42 ms early and 0.07 ms late.
It should still leave 3.04 to 3.68 ms after exit.

This shift can recover one or two useful 1 ms physics steps.
It does not increase valve duration or commanded force.
Earlier actuation can still hit neighboring objects.
The mixed-feed Keep loss and spill gates therefore remain necessary.

Use the same tuning seeds and reserve the same held-out seeds.
Record `t_fire`, `t_available`, actual `t_on`, region entry, region exit, impulse, and collateral contacts.
Compare only 1.5 ms and 3.0 ms lead settings.
Run held-out cases only when the 3.0 ms candidate passes every tuning gate.

## Validation

The diagnostic wrapper compiled with the prepared Python 3.13 environment.
All 24 tuning cases completed and produced valid JSON.
The focused physics suite passed 12 tests.

```bash
/Users/taras/Documents/code/hackspain/.venv-coffee/bin/python \
  -m py_compile thoughts/taras/qa/evidence/cinta_rejection_calibration.py

cd sim/coffee_sorter
/Users/taras/Documents/code/hackspain/.venv-coffee/bin/python \
  -m unittest test_sim_physics.py
```

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
| `evidence/cinta-rejection-calibration/tuning-summary.json` | `3a2f75955c3f4a9943bb6d69bdfa21bc9984610407cf207c6a53e37112476e0b` |
| `evidence/cinta-rejection-calibration/tuning-raw.tar.gz` | `220e21a972548a0d27f1b412686c741b28b20ebbeb27ff8c7740f0eabd470e85` |

The full corrected audit supersedes the earlier reduced-pool 3 of 8 result.
Run the screen only from the accepted two-commit physics chain.
