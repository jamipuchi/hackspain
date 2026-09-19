# Coffee quality delivery

The implementation improves motion, intended-pulse capture, and inference cost.
The frozen configuration fails acceptance because it still rejects too many keep objects.
Taras retains functional QA and acceptance. The branch is `codex/coffee-quality`. No merge occurred.

## What changed and why

| Change | Measured reason | Result and cost |
|---|---|---|
| Physics step: 2 ms to 1 ms | Post-feed bounce affected every class, especially shells, husks, and stones. | Maximum clearance decreased for all ten classes. Shell clearance fell from 48.88 to 7.98 mm. Physics cost approximately doubled. |
| Light-object force scaling | Eighteen cohort shells or husks received their own pulse, then spilled. | Capture rose from 140/190 to 156/190. Good loss fell from 57/1110 to 50/1110 with the model fixed. |
| Equivalent numeric tree traversal | Small-batch model inference was the largest measured stage. | Inference fell from 2140.7 to 858.3 ms per simulated second. The comparable engine run became 22.1% faster. |
| Bootstrap from the selected preset | Training previously used separate default physics and camera settings. | The final model records the actual physical preset. Retraining improved class accuracy but worsened good loss. |
| Rejection threshold: 0.5 to 0.8 | Recorded scores identified seven avoidable keep losses against one captured defect at risk. | The actual comparison improved capture from 161/190 to 165/190 and good loss from 67/1110 to 62/1110. |

The camera remains at 250 Hz. The default feed remains 500 objects/s.
Pool sizes, maximum jet force, jet width, native thread limits, and session limits remain unchanged.
The controller uses predicted class mass for force scaling. Evaluator truth never enters control.
The shared engine, policy, and snapshot interfaces remain compatible.

The physical comparison uses one baseline model artifact throughout.
Retraining and threshold selection are separate changes, documented in `SELECTED.md`.
The final model does not retain the best observed 4.50% development good-loss result.
The earlier historical live baselines use different models and sessions. They are not isolated physical comparisons with this work.

## Frozen acceptance

| Seed | Capture | Capture 95% lower | Good loss | Good-loss 95% upper |
|---|---:|---:|---:|---:|
| 111 | 264/311 (84.89%) | 80.48% | 120/1989 (6.03%) | 7.17% |
| 112 | 322/368 (87.50%) | 83.73% | 124/1932 (6.42%) | 7.60% |
| 113 | 313/362 (86.46%) | 82.55% | 96/1938 (4.95%) | 6.01% |
| Pooled | 899/1041 (86.36%) | 84.14% | 340/5859 (5.80%) | 6.43% |

Every seed included 2,300 eligible objects, zero unresolved objects, and 500 admitted objects/s.
Every seed passed the 80% capture lower bound. Every seed failed the 2% good-loss upper bound.
Runs took 28.70, 29.74, and 29.11 wall seconds for six simulated seconds.
They recorded zero late decisions. No competing simulation overlapped these runs.
The configuration remained frozen afterward. Seeds 111, 112, and 113 are now exposed.

See `ACCEPTANCE.md`, `acceptance-summary.json`, and the full compressed reports for evidence.
The seven requested rate scenarios appear in `RATES.md`, including classification coverage and confidence intervals.

## Remaining failures

Of 340 lost keep objects, 293 received an associated own rejection pulse. Another 46 received other jet contact.
One keep object spilled without recorded jet contact.
This makes perception, tracking, and policy the next investigation priority. Approximate object attribution prevents a stronger causal claim.

The 142 missed defects include 65 classification or tracking cases and 50 contacts without capture.
Six targets missed their own pulse. Twenty-one required defects had no full camera detection.
Sticks, broken objects, faded objects, and husks account for most misses.
No post-acceptance tuning occurred.

Motion still has penetration and inertia-model limitations. `MOTION.md` preserves rejected alternatives and their negative results.
The model uses synthetic training images. Hardware behavior and lighting robustness remain unproven.
The optimized classifier uses pinned scikit-learn internals and falls back for unsupported models or larger batches.
Dependency upgrades require renewed numerical equivalence checks.

## Browser demonstration

The unchanged shared UI ran on port 8891 through agent-browser.
It recognized one injected stone, scheduled its pulse, recorded own-pulse contact, and sent it to reject.
The stone resolved 1.431 wall seconds after physical spawn. Browser acknowledgment took 27.9 ms.

| Measurement | Observed value |
|---|---:|
| Simulation duration | 10.001 s |
| Engine active wall time | 50.987 s |
| Engine speed | 0.196x real time |
| Admitted feed, including one injection | 500.05 objects/s |
| Browser FPS, median | 60 |
| Running pose packets | 491 |
| Measured pose frequency | 9.625 Hz |

The live cohort captured 574/671 defects and lost 176/3629 keep objects, with zero unresolved objects.
This separate seed-8 session included one manual stone injection. It is not pooled with acceptance.
The screenshot is `demo-completed.png`. Raw browser samples and engine evidence are preserved beside this report.

An initial idle connection dropped its first injection before acknowledgment. No command reached the worker.
Reloading the page and injecting again worked. The shared UI owner received this observation.
This task did not edit shared UI files to address it.

The service was restarted into a ready state for Taras at `http://127.0.0.1:8891`.
The measured session remains preserved under `/tmp/coffee-quality-demo`.
The ready session writes to `/tmp/coffee-quality-taras-ready`.
The original port-8890 service and all existing worktrees remain intact.

## Runtime limits

The measured inference improvement is real, but the complete engine remains slower than real time.
After optimization, physics, evaluation bookkeeping, image detection, and model inference all consume substantial wall time.
Measured wall latency remains part of pulse scheduling. One retained development run had two late decisions during a compute spike.

Taras reports approximately 16 vCPUs and 128 GB RAM for production.
This sequential implementation does not automatically use that parallel capacity. No production or shared-host benchmark ran.
Browser FPS and pose frequency do not establish physical engine speed.

## Reproduction

Use an unused worktree path and branch name. These commands preserve the existing checkouts.

```bash
git -C /Users/taras/Documents/code/hackspain fetch origin
git -C /Users/taras/Documents/code/hackspain worktree add -b codex/coffee-quality-review /private/tmp/hackspain-coffee-quality-review origin/codex/coffee-quality
cd /private/tmp/hackspain-coffee-quality-review
python3.13 -m venv .venv-coffee
source .venv-coffee/bin/activate
python -m pip install -r thoughts/taras/research/coffee-quality/requirements-resolved.txt
mkdir -p sim/coffee_sorter/models
gzip -dc thoughts/taras/research/coffee-quality/model-selected.joblib.gz > sim/coffee_sorter/models/live_green_arabica.joblib
gzip -dc thoughts/taras/research/coffee-quality/model-selected.manifest.json.gz > sim/coffee_sorter/models/live_green_arabica.manifest.json
python -m py_compile sim/coffee_sorter/sim.py sim/coffee_sorter/controller.py sim/coffee_sorter/engine.py sim/coffee_sorter/classifier.py sim/coffee_sorter/bootstrap_model.py
```

The exact selected model SHA-256 is `89513398373c6e0e81286419962feb3e312742de14a76d02dd0d819ad5264a5a`.
The freeze records every consumed source, asset, policy, and preset hash.
Run the following measurements sequentially while other simulation workers are idle.

```bash
python sim/coffee_sorter/engine.py --preset sim/coffee_sorter/configs/default_demo.json --seconds 4 --out /tmp/coffee-quality-reproduce
python thoughts/taras/research/coffee-quality/measure_rates.py --manifest thoughts/taras/research/coffee-quality/freeze.json --out /tmp/coffee-quality-rates-reproduce
python thoughts/taras/research/coffee-quality/benchmark_inference.py --features thoughts/taras/research/coffee-quality/inference-features.npz --repeats 3
python thoughts/taras/research/coffee-quality/measure_motion.py --help
python thoughts/taras/research/coffee-quality/measure_pulses.py --seconds 4 --out /tmp/coffee-quality-pulses-reproduce
```

Use `python sim/coffee_sorter/bootstrap_model.py` to reproduce training separately.
It uses seeds 7 and 9 with the default physical preset. It may produce different artifact metadata on another environment.
Restore the archived model afterward when exact frozen bytes are required.

The reserved evaluation command and historical freeze procedure remain in `SELECTED.md`.
The committed exposure record intentionally prevents presenting a repeated run as untouched acceptance.

## Manual E2E

Run this command only when port 8891 is available. The current ready service already occupies that port.

```bash
python sim/coffee_sorter/live.py --port 8891 --preset sim/coffee_sorter/configs/default_demo.json --out /tmp/coffee-quality-manual
curl --fail http://127.0.0.1:8891/health
agent-browser --session coffee-quality-manual open http://127.0.0.1:8891
agent-browser --session coffee-quality-manual snapshot
```

Select **Inject stone**. Verify prediction, scheduled rejection, actual contact, and physical outcome separately.
If the idle connection leaves an injection pending, reload the page and repeat once.
Taras reviews visible motion and functional behavior. The acceptance failure remains explicit.

The architecture diagram and source map are in `ARCHITECTURE.md`.
