# Frozen acceptance result

The configuration failed acceptance because every seed exceeded the good-loss limit.
Every seed met the capture lower bound, cohort size, zero-unresolved requirement, and 500 admitted objects/s.
Each run used six simulated seconds, 250 Hz inspection, and one native thread.
The exposure record marks seeds 111, 112, and 113 as used. No tuning followed these results.

| Seed | Capture | Capture 95% lower | Good loss | Good-loss 95% upper | Eligible | Unresolved | Wall seconds | Engine rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 111 | 264/311 (84.89%) | 80.48% | 120/1989 (6.03%) | 7.17% | 2300 | 0 | 28.70 | 0.209x |
| 112 | 322/368 (87.50%) | 83.73% | 124/1932 (6.42%) | 7.60% | 2300 | 0 | 29.74 | 0.202x |
| 113 | 313/362 (86.46%) | 82.55% | 96/1938 (4.95%) | 6.01% | 2300 | 0 | 29.11 | 0.206x |
| Pooled | 899/1041 (86.36%) | 84.14% | 340/5859 (5.80%) | 6.43% | 6900 | 0 | n/a | n/a |

Spills and unresolved objects remain in the denominators.
The cohort includes spawn times from 0.8 through run end minus 0.6 seconds.
Every seed admitted 3,000 objects over six simulated seconds without pool starvation.
Floating-point rate values were 499.99999999997 objects/s. The evaluator applies only a 1e-9 tolerance to that gate.
Pooled results do not override any failed per-seed requirement.

## Remaining failures

Miss attribution: `{'classification_or_tracking': 65, 'hit_not_captured': 50, 'merged_without_target': 0, 'not_detected': 21, 'targeted_not_hit': 6, 'unresolved': 0}`. These labels identify stages, not proven causes.

Missed required classes: `{'stone': 14, 'stick': 33, 'broken': 35, 'faded': 30, 'husk': 21, 'shell': 7, 'black': 2}`.

Lost keep objects: `{'own_pulse': 293, 'outcome_reject': 339, 'other_contact': 46, 'no_jet_contact': 1, 'outcome_spilled': 1}`.

All three runs recorded 0 late decisions. Good loss persists despite on-time scheduling.

## Runtime and isolation

The mean stage costs below describe these evaluation runs, in milliseconds per simulated second.
They include evaluation bookkeeping and activity checks. They do not measure browser performance.

- physics: 1186.6.
- inspection_render: 743.5.
- detection: 889.1.
- model_inference: 866.5.
- control: 63.2.
- evaluation: 1061.6.

The frozen manifest is `freeze.json`. Exact compressed model bytes are `model-selected.joblib.gz`.
The manifest covers source, consumed assets, model, policy, preset, evaluator, dependencies, and native thread limits.
The evaluator checked frozen hashes before and after every seed.
The source checkpoint before evaluation was `e7cb111`.

Preflight found port 8890 completed and no competing simulation CLI.
A later coordination message mentioned a separate port-8892 probe, so its timestamps required an audit.
That probe wrote its completed report at 15:21:26.532 UTC.
Taras's port-8890 session wrote its completed report at 15:23:48.194 UTC.
The reserved batch started at 15:23:55.299 UTC. Both competing simulations had finished.
Idle HTTP processes remained running. Neither consumed simulation time during this batch.
The automatic activity check monitors 8890 and known simulation CLIs. Other live ports require explicit coordination.
No other session process was stopped by this task.
