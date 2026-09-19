# Coffee quality execution

Taras owns functional QA and acceptance. This task owns physical quality and engine performance.

## Isolation and boundaries

- Base: `origin/codex/coffee-core-live` at `10d0975`.
- Both `10d0975` and `91dedc3` are ancestors.
- Branch: `codex/coffee-quality`.
- Worktree: `/private/tmp/hackspain-coffee-quality`.
- Existing checkouts and the service on port 8890 remain intact.
- Engine, policy, and snapshot interfaces remain compatible.
- Shared UI, assets, language work, and the magnet experiment remain outside scope.

## 1. Baseline

Bootstrap the model once. Preserve its bytes and provenance for physical comparisons.
Use seed 8 and sequential short runs. Record actual latency, hashes, thread counts, quality, and timing.

### Verification

```bash
.venv-coffee/bin/python sim/coffee_sorter/bootstrap_model.py
.venv-coffee/bin/python sim/coffee_sorter/engine.py --preset sim/coffee_sorter/configs/default_demo.json --seconds 2 --out /tmp/coffee-quality-baseline
```

## 2. Motion

Measure collision-bottom clearance, insertion overlap, contact impulses, and motion near inspection and jets.
Correct one demonstrated source. Compare every class at the same requested throughput and fixed model.

### Verification

```bash
.venv-coffee/bin/python -m py_compile sim/coffee_sorter/sim.py sim/coffee_sorter/scene.py
.venv-coffee/bin/python thoughts/taras/research/coffee-quality/measure_motion.py --help
```

## 3. Intended pulse contact

Measure schedule headroom, availability clamps, and simultaneous spatial membership before any jet changes.
Keep evaluator identities, classes, velocities, and future positions outside control.
Compare capture and total good loss after correcting the dominant measured cause.

### Verification

```bash
.venv-coffee/bin/python -m py_compile sim/coffee_sorter/controller.py sim/coffee_sorter/engine.py
.venv-coffee/bin/python thoughts/taras/research/coffee-quality/measure_pulses.py --help
```

## 4. Runtime

Profile the selected configuration. Correct the largest measured cost without weakening camera inspection or throughput.
Report changed wall latency and its scheduling effect.

### Verification

```bash
.venv-coffee/bin/python -m py_compile sim/coffee_sorter/classifier.py sim/coffee_sorter/engine.py
.venv-coffee/bin/python sim/coffee_sorter/engine.py --preset sim/coffee_sorter/configs/default_demo.json --seconds 2 --out /tmp/coffee-quality-runtime
```

## 5. Freeze and evaluate

Bootstrap the final model separately. Preserve the baseline model.
Freeze source, model, policy, and preset hashes before reserved evaluation.
Use seeds 111, 112, and 113 once. Report each seed and pooled counts.
Require 2,000 eligible objects per seed, zero unresolved objects, and at least 500 admitted objects/s.
Require capture Wilson lower bound at least 80% and good-loss Wilson upper bound at most 2% for every seed.
Retain spills and unresolved objects in denominators. Keep cohort spawn times within `[0.8, end - 0.6]`, inclusive.
Failed seeds become exposed. Do not tune on them and claim untouched acceptance.

### Verification

```bash
.venv-coffee/bin/python sim/coffee_sorter/bootstrap_model.py
.venv-coffee/bin/python thoughts/taras/research/coffee-quality/evaluate_frozen.py --help
```

## Manual E2E

```bash
cd /private/tmp/hackspain-coffee-quality
.venv-coffee/bin/python sim/coffee_sorter/live.py --port 8891 --preset sim/coffee_sorter/configs/default_demo.json
curl --fail http://127.0.0.1:8891/health
agent-browser open http://127.0.0.1:8891
agent-browser snapshot
```

Taras reviews motion and sorting behavior. Browser FPS, pose frequency, engine speed, and admitted throughput remain separate measurements.

## Production capacity note

Taras reports approximately 16 vCPUs and 128 GB RAM for production.
Parallel inference can use that capacity after a measured comparison.
Local speed measurements do not establish production speed.
This task retains its restriction against shared-host jobs and public deployment.
