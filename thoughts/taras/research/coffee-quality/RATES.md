# Feed-rate comparison

These are sequential development measurements with seed 8, six simulated seconds per rate, and the same frozen model, policy, physics, and camera.
Only the requested feed rate changes. No tuning followed these results.
All runs inspected at 250 Hz with 1 ms physics. All native numerical thread limits remained one.

**Sorting accuracy** means correctly rejected required defects plus correctly accepted keep objects, divided by all eligible objects.
It includes spills and unresolved objects as incorrect results. Most objects are keep objects, so capture and loss remain essential separate metrics.
**Class accuracy** measures the predicted class among objects with one unique associated predicted class.
Class coverage reports the fraction included in that calculation. Object associations are approximate.

| Requested objects/s | Sorting accuracy | Defect capture | Good loss | Class accuracy | Class coverage | Engine speed |
|---|---:|---:|---:|---:|---:|---:|
| 10 | 100.00% | 7/7 (100.00%) | 0/39 (0.00%) | 97.83% | 100.00% | 0.503x |
| 50 | 98.26% | 34/35 (97.14%) | 3/195 (1.54%) | 97.83% | 100.00% | 0.475x |
| 100 | 96.75% | 45/51 (88.24%) | 9/410 (2.20%) | 96.07% | 99.35% | 0.417x |
| 200 | 96.63% | 126/135 (93.33%) | 22/785 (2.80%) | 97.81% | 99.24% | 0.334x |
| 250 | 95.30% | 145/157 (92.36%) | 42/993 (4.23%) | 94.59% | 99.57% | 0.304x |
| 400 | 93.97% | 237/266 (89.10%) | 82/1574 (5.21%) | 94.00% | 99.57% | 0.241x |
| 500 | 93.39% | 293/346 (84.68%) | 99/1954 (5.07%) | 94.45% | 99.43% | 0.213x |

Every rate recorded zero late decisions and zero pool starvation.
The low-rate results have few defects. Seven captured defects at 10 objects/s do not establish perfect recall.
Capture is not monotonic in this small sample. These runs do not prove a sharp safe throughput limit.
The changing feed rate also changes contact patterns and cohort composition.

| Requested objects/s | Capture Wilson 95% | Good-loss Wilson 95% | Eligible | Unresolved | Admitted objects/s | Wall seconds | Max control compute ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| 10 | 64.57% to 100.00% | 0.00% to 8.97% | 46 | 0 | 10.000 | 11.937 | 40.87 |
| 50 | 85.47% to 99.49% | 0.52% to 4.42% | 230 | 0 | 50.000 | 12.640 | 33.15 |
| 100 | 76.62% to 94.50% | 1.16% to 4.12% | 461 | 0 | 99.833 | 14.401 | 35.14 |
| 200 | 87.82% to 96.45% | 1.86% to 4.21% | 920 | 0 | 200.000 | 17.958 | 37.16 |
| 250 | 87.12% to 95.57% | 3.14% to 5.67% | 1150 | 0 | 250.000 | 19.751 | 35.92 |
| 400 | 84.78% to 92.30% | 4.22% to 6.42% | 1840 | 0 | 400.000 | 24.908 | 36.14 |
| 500 | 80.51% to 88.09% | 4.18% to 6.13% | 2300 | 0 | 500.000 | 28.180 | 48.56 |

Maximum control compute time includes detection, inference, and control. It excludes the 4 ms availability floor.
The controller still uses actual wall compute latency to schedule each pulse.
The cohort uses inclusive spawn times from 0.8 through 5.4 simulated seconds.
Floating-point step accumulation can move one boundary object. The evaluator validates the exact recorded run-end boundary.

These measurements describe the local Mac. They do not predict speed on the proposed 16-vCPU production machine.
No browser runs during this comparison. Browser FPS and pose packet frequency are separate demonstration measurements.

The script verifies the frozen source, model, policy, preset, assets, and evaluator before and after every rate.
Each report records native threads and the temporary rate-specific preset hash.
Separate review found no remaining actionable Spec findings after fixing the default-rate copy and idle-server checks.

```bash
.venv-coffee/bin/python thoughts/taras/research/coffee-quality/measure_rates.py --manifest thoughts/taras/research/coffee-quality/freeze.json --out /tmp/coffee-quality-rates-repeat
```

Use a new output directory. Run only when other simulation workers are idle.
Repeating this development comparison does not create new acceptance evidence.
