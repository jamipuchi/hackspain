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
    "$PYTHON" "$SCRIPT" isolated --force "$force" --lead-ms 1.5 --seed "$seed" \
      --output "$OUT/isolated-force-$force-seed-$seed.json"
    "$PYTHON" "$SCRIPT" feed --force "$force" --lead-ms 1.5 --seed "$seed" --seconds 4 \
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

## Timing comparison result

The bounded timing comparison kept force at 0.060 N.
It kept pulse duration and every production file unchanged.
The baseline used 1.5 ms lead.
The candidate used 3.0 ms lead.

Both settings used tuning seeds 11, 17, 23, and 31.
Each mixed-feed run requested 500 objects per simulated second for four simulated seconds.
The combined scoring cohort contains 5,200 resolved objects per setting.

| Quantity | 1.5 ms baseline | 3.0 ms candidate |
| --- | ---: | ---: |
| Isolated Stone Reject | 2/8 | 3/8 |
| Mixed Stone Reject | 6/21 | 10/21 |
| Reject capture | 678/760, 89.21% | 683/760, 89.87% |
| Keep loss | 185/4,440, 4.17% | 184/4,440, 4.14% |
| Spill | 7/5,200, 0.135% | 8/5,200, 0.154% |
| Late reject decisions | 0/1,230 | 1/1,230 |
| Own contact pairs | 1,243 | 1,250 |
| Collateral contact pairs | 103 | 102 |
| Summed active wall time | 77.36 s | 81.68 s |
| Maximum peak memory | 302.19 MB | 301.33 MB |

All eight isolated objects were predicted as Stone under both settings.
Every isolated reject decision was scheduled and received its own pulse contact.
The candidate increased mean isolated delivered impulse from 360 to 465 microN s.

The baseline commanded pulse start averaged 1.462 ms after jet entry.
Physics-step activation averaged 2.000 ms after entry.
The candidate commanded pulse start averaged 0.038 ms before entry.
Physics-step activation averaged 0.625 ms after entry.

The candidate improved both measured Stone outcomes.
Aggregate reject capture improved by 0.66 percentage points.
Keep loss fell by 0.02 percentage points.
Spill rose by 0.02 percentage points.

The original candidate measurement did not clear the wall-cost gate.
It increased summed active wall time by 5.59%.
Concurrent host load can affect this measurement.

Seed 17 also produced one late Black reject decision with no scheduled pulse.
That object received no pulse and reached Accept.
The event does not violate the physical Reject evidence gate.

The paired baseline made the same decision at 3.188 simulated seconds.
Its predicted fire time was 3.260793 seconds.
Its result became available at 3.200698 seconds.
The controller therefore had 62.095 ms before its late deadline.

The candidate predicted fire at 3.260782 seconds.
Its result became available at 3.264311 seconds.
It missed the 3.262782-second deadline by 1.529 ms.
The candidate availability latency was 76.311 ms.
The baseline availability latency was 12.698 ms.

The controller late check does not use pulse lead.
The paired fire prediction changed by only 0.011 ms.
Entry pose and velocity were also nearly identical.
The evidence attributes the late flag to a compute delay, not earlier lead.

### Counterbalanced repeat

The repeat reused the four tuning seeds.
It is a repeated measurement, not independent seed coverage.
It ran mixed-feed cases only.

The run order was baseline then candidate for seed 11.
Seed 17 used candidate then baseline.
Seed 23 used baseline then candidate.
Seed 31 used candidate then baseline.

Each case used a separate process and lock window.
No case waited materially for the shared lock.
Recorded one-minute load averages ranged from 8.24 to 11.29.

| Quantity | Repeat baseline | Repeat candidate | Combined baseline | Combined candidate |
| --- | ---: | ---: | ---: | ---: |
| Mixed Stone Reject | 6/21 | 10/21 | 12/42 | 20/42 |
| Reject capture | 678/760, 89.21% | 683/760, 89.87% | 1,356/1,520, 89.21% | 1,366/1,520, 89.87% |
| Keep loss | 185/4,440, 4.17% | 185/4,440, 4.17% | 370/8,880, 4.17% | 369/8,880, 4.16% |
| Spill | 7/5,200, 0.135% | 8/5,200, 0.154% | 14/10,400, 0.135% | 16/10,400, 0.154% |
| Late reject decisions | 0 | 1 | 0 | 2 |
| Active wall time | 75.76 s | 81.33 s | 153.12 s | 163.02 s |
| Execution wall time | 78.23 s | 83.80 s | 158.08 s | 168.00 s |

The repeat candidate increased active wall time by 7.35%.
The combined repeated measurements increased active wall time by 6.46%.
Both exceed the unchanged 5% gate.

Repeat process CPU time rose by only 1.79%.
The seed 31 candidate interval caused most of the wall difference.
Its active wall time was 23.19 seconds.
The paired baseline took 18.91 seconds.
This supports a host scheduling contribution.
It does not satisfy the operational wall gate.

The repeat candidate also recorded one late Broken decision.
Its measured availability latency was 115.589 ms.
The paired baseline availability latency was 12.298 ms.
The late condition remains independent of pulse lead.

The held-out seeds 43, 59, 71, and 83 were not run or inspected.
The 3.0 ms candidate is not selected.
The production recommendation remains 1.5 ms lead and 0.060 N force.
Final rollout validation must use the newly trained release model.
The current evidence uses the frozen trusted model.

The compact result is in `evidence/cinta-rejection-calibration/timing-tuning-summary.json`.
The original raw evidence is in `evidence/cinta-rejection-calibration/timing-tuning-raw.tar.gz`.
The repeat raw evidence is in `evidence/cinta-rejection-calibration/timing-repeat-raw.tar.gz`.

### Timing commands

```bash
PYTHON=/Users/taras/Documents/code/hackspain/.venv-coffee/bin/python
SCRIPT=thoughts/taras/qa/evidence/cinta_rejection_calibration.py
OUT=/private/tmp/cinta-rejection-calibration/timing-tuning

for lead in 1.5 3.0
do
  for seed in 11 17 23 31
  do
    "$PYTHON" "$SCRIPT" isolated --force 0.06 --lead-ms "$lead" \
      --seed "$seed" --output "$OUT/isolated-lead-$lead-seed-$seed.json"
    "$PYTHON" "$SCRIPT" feed --force 0.06 --lead-ms "$lead" \
      --seed "$seed" --seconds 4 \
      --output "$OUT/feed-lead-$lead-seed-$seed.json"
  done
done
```

Do not run the held-out seeds while the wall-cost gate remains unresolved.

The counterbalanced repeat used these command pairs:

```bash
OUT=/private/tmp/cinta-rejection-calibration/timing-repeat
"$PYTHON" "$SCRIPT" feed --force 0.06 --lead-ms 1.5 --seed 11 --seconds 4 \
  --run-label order-01-baseline-seed-11 --output "$OUT/feed-order-01-lead-1.5-seed-11.json"
"$PYTHON" "$SCRIPT" feed --force 0.06 --lead-ms 3.0 --seed 11 --seconds 4 \
  --run-label order-02-candidate-seed-11 --output "$OUT/feed-order-02-lead-3.0-seed-11.json"
"$PYTHON" "$SCRIPT" feed --force 0.06 --lead-ms 3.0 --seed 17 --seconds 4 \
  --run-label order-03-candidate-seed-17 --output "$OUT/feed-order-03-lead-3.0-seed-17.json"
"$PYTHON" "$SCRIPT" feed --force 0.06 --lead-ms 1.5 --seed 17 --seconds 4 \
  --run-label order-04-baseline-seed-17 --output "$OUT/feed-order-04-lead-1.5-seed-17.json"
"$PYTHON" "$SCRIPT" feed --force 0.06 --lead-ms 1.5 --seed 23 --seconds 4 \
  --run-label order-05-baseline-seed-23 --output "$OUT/feed-order-05-lead-1.5-seed-23.json"
"$PYTHON" "$SCRIPT" feed --force 0.06 --lead-ms 3.0 --seed 23 --seconds 4 \
  --run-label order-06-candidate-seed-23 --output "$OUT/feed-order-06-lead-3.0-seed-23.json"
"$PYTHON" "$SCRIPT" feed --force 0.06 --lead-ms 3.0 --seed 31 --seconds 4 \
  --run-label order-07-candidate-seed-31 --output "$OUT/feed-order-07-lead-3.0-seed-31.json"
"$PYTHON" "$SCRIPT" feed --force 0.06 --lead-ms 1.5 --seed 31 --seconds 4 \
  --run-label order-08-baseline-seed-31 --output "$OUT/feed-order-08-lead-1.5-seed-31.json"
```

## Validation

The diagnostic wrapper compiled with the prepared Python 3.13 environment.
All 24 tuning cases completed and produced valid JSON.
All 16 timing comparison cases completed and produced valid JSON.
All eight counterbalanced repeat cases completed and produced valid JSON.
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
| `evidence/cinta-rejection-calibration/timing-tuning-summary.json` | `f9260c810f61d3750ae7f3f846950b5a0603e23c2cab52719c1fa4c62822e848` |
| `evidence/cinta-rejection-calibration/timing-tuning-raw.tar.gz` | `36cbd3d443b236db5ebdc0677a4975c5f8e9b2a0884bb0c08afb8ecc221201b0` |
| `evidence/cinta-rejection-calibration/timing-repeat-raw.tar.gz` | `80ed42b29f8444f74d071ada6aaee9f4c01415ea8932ffe54abb75f4bfe5c02a` |

The full corrected audit supersedes the earlier reduced-pool 3 of 8 result.
Run the screen only from the accepted two-commit physics chain.
