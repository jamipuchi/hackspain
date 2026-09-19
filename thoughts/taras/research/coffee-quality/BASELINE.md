# Baseline checkpoint

The source is `10d0975`, including capsule correction `91dedc3`.
The new model hash is `3e0532d24da3a872ae931b43b9f048ceda5435e13bdaaf65087d54c37705bdbd`.
The model remains fixed during physical comparisons. Its backup is `/tmp/coffee-quality-baseline-model.joblib`.
Bootstrap took 25.538 seconds. Separate seeds 7 and 9 supplied training and holdout objects.
The observation-weighted holdout accuracy is 93.64%. This excludes physical sorting and anomaly policy.

The two-second development run used seed 8 and the unmodified default preset.
It admitted 1,000 objects, with zero pool starvation and one native OpenMP thread.
The cohort contains 301 objects. It captured 33/40 required defects and lost 17/261 keep objects.
The capture lower bound is 68.05%. The good-loss upper bound is 10.18%.
No cohort object remained unresolved. These counts do not establish acceptance.

The engine used 9.547 wall seconds, or 0.2095 simulated seconds per wall second.
Model inference used 2,050.9 ms per simulated second. It is the largest measured component.
Measured latency remains active in scheduling. Repeat outcomes can depend on host load.
The service on port 8890 reported `ready` before and after this run.
No competing simulation process was active in the process snapshot.

Full hashes, native-thread limits, counts, timings, and object evidence are in `baseline-report.json`.
