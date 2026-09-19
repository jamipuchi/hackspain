# Pulse contact checkpoint

The comparison holds the model fixed and uses seed 8, 500 objects/s, 250 Hz inspection, and 1 ms physics.
The evaluator samples actual pre-force positions during every intended pulse.
It records simultaneous x/y/z membership, schedule headroom, availability clamps, activation, and collateral contact.
All 311 measured contact pairs in the baseline match simulator contact pairs.
No scheduled baseline pulse required an availability clamp.
End-of-run pulses can remain unactivated. These records are separate from cohort outcomes.

The baseline has 18 cohort shells and husks that received their intended pulse, then spilled.
The minimum pulse duration overdrives these low-mass classes.
The selected correction scales force below the existing pulse floor using camera-predicted class mass.
It preserves the pulse window and nozzle selection. It does not use evaluator mass or identity.

| Four-second development result | Before | After |
|---|---:|---:|
| Captured required defects | 140/190 | 156/190 |
| Capture | 73.68% | 82.11% |
| Capture Wilson lower bound | 67.00% | 76.04% |
| Keep objects lost | 57/1110 | 50/1110 |
| Total good loss | 5.14% | 4.50% |
| Good-loss Wilson upper bound | 6.60% | 5.89% |
| Unresolved cohort objects | 0 | 0 |

The correction leaves one shell spill in this cohort. Eight stick decisions contact targets that later reach accept.
This reveals a remaining tradeoff. The single short comparison does not establish acceptance.
The force remains unchanged for predicted masses at or above 80% of the reference mass.
No jet widens, and no force increases.

Good loss remains primarily a perception or tracking problem in this development run.
Before the correction, 49 of 57 lost keep objects had associated own-pulse contact.
The associated rejection predictions include faded, stone, broken, insect, and stick.
Approximate component association prevents treating every case as proven single-object misclassification.

The original evaluator labeled every failed contact as insufficient deflection.
That was too broad for spills. The script now separates spills after contact and records final positions.
Archived baseline pulse categories retain the earlier label. Their outcome fields support the corrected interpretation.
A serialization failure preceded the successful baseline. Its partial report remains in `/tmp/coffee-quality-pulses-before-serialization-failed`.

Evaluation adds overhead outside controller timing. Do not use its wall time as the engine speed benchmark.
Measured controller latency remains active in scheduling throughout these runs.

```bash
.venv-coffee/bin/python thoughts/taras/research/coffee-quality/measure_pulses.py --seconds 4 --out /tmp/coffee-quality-pulses
```
