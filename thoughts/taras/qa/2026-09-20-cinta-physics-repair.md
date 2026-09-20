---
status: physics-pass-release-blocked
date: 2026-09-20
code_commits: [2f247d4, 5fcb452]
---

The physics repair passes its focused checks. The frozen model and current air policy do not meet a reliable Stone demonstration target.

The canonical 488-body pool now accepts all eight unforced Stones. With Reject selected, it rejects only two of eight Stones despite correct classification and air contact.

**Reproduction and repair**

The installed runtime is Python 3.13.12 with MuJoCo 3.13.0. Requirements and a runtime guard require that MuJoCo version.

Before correction, a 1.052 g Stone accelerated at -68.79 m/s². Updating only `body_mass` left the simple-body `dof_M0` cache at 0.15 g.

The supported mass/inertia/armature update is `mj_setConst(model, scratch_data)`, followed by `mj_forward(model, live_data)`. The scratch data prevents reference positions from replacing live positions. Geometry changes require recompilation through the supported API. [MuJoCo model changes](https://mujoco.readthedocs.io/en/latest/programming/simulation.html#model-changes), [3.13.0 constant computation](https://github.com/google-deepmind/mujoco/blob/3.13.0/src/engine/engine_setconst.c#L1523-L1560).

Full constant refresh costs 89 ms for 488 bodies and 580 ms for 1,238 bodies. The repair therefore uses a guarded, topology-specific internal-field equivalent.

It refreshes the complete six-DOF mass block, armature-aware inverse weights, subtree mass, length scales, and statistics. It also corrects inertia-axis mapping, primitive bounds, and the collision BVH leaf.

Both feeder paths share that setup. Reuse clears only the retired body's warmstart. Failed placement preserves parking compensation. Active poses, velocities, forces, time, and queued pulses remain intact.

The guard requires independent world-child free bodies, known geometry, one collision leaf, aligned inertia axes, fixed cameras/lights, and no coupled model elements.

This is not a generally supported MuJoCo update API. The read-only `ngravcomp` count retains its compiled value. Runtime gravity uses `flg_gravcomp` and individual body compensation. Existing HALF/capsule inertia approximations and rotational armature remain.

**Measured outcomes**

Trials use seeds 7, 8, 9, and 42. Each trial spawns a real Stone through the pool, then reuses the same body once.

| Canonical pool, eight Stones per mode | Before | After | After commands / activations / contacted objects |
|---|---|---|---|
| Air disabled | 8 Reject | 8 Accept | 0 / 0 / 0 |
| Keep, anomaly enabled | 2 Reject, 6 Spilled | 2 Reject, 6 Accept | 24 / 24 / 8 |
| Reject, anomaly enabled | 2 Reject, 6 Spilled | 2 Reject, 6 Accept | 24 / 24 / 8 |
| Keep, anomaly disabled for diagnosis | 8 Reject | 8 Accept | 0 / 0 / 0 |

The frozen classifier identifies every Stone correctly. Under Keep, class rejection probability is zero, but every Stone exceeds the anomaly threshold.

Corrected Reject trials receive 0.00024 to 0.00060 N·s of downward impulse. No decision is late. Contact alone does not establish adequate impulse or optimal timing.

The separate calibration task owns that distinction. This repair changes no controller thresholds, pulse policy, or validation gates.

The reduced five-body pool rejected three of eight Stones after correction. That result does not replace the canonical two-of-eight result.

A separate balanced sample contains 80 isolated objects, eight per class. Classification remains 76/80 correct. Correct physical outcomes change from 68/80 to 70/80.

Spills change from 5/80 to zero. Good retention remains 6/8 because two Good objects classify as Faded. This small balanced sample does not estimate production feed quality.

Ten free-flight samples across all four shapes and reuse produce -9.81 m/s² after correction. Independent force and torque comparisons match full `mj_setConst`.

**Cost and verification**

| Pool | Startup before / after | Mean spawn before / after | Physics speed before / after |
|---|---|---|---|
| 488 bodies | 0.280 / 0.241 s | 47 / 184 µs | 0.874 / 0.767 sim s per wall s |
| 1,238 bodies | 1.363 / 1.681 s | 42 / 345 µs | 0.504 / 0.484 sim s per wall s |

Each benchmark measures 400 spawns and two simulation seconds at 500 objects/s. All 1,000 feed objects spawn without starvation. These runs exclude rendering and inference.

These single-run timings vary with host load. They establish added spawn cost, not a production real-time guarantee.

The full-pool oracle compares constants after 400 mixed-shape uses. All comparisons pass `rtol=1e-10, atol=1e-12`.

The final adjacent suite passes 80 tests. It covers gravity, applied force, inertia axes, compiled collision bounds, topology rejection, lifecycle reuse, both feeders, controller behavior, and evidence separation.

Spec review: no remaining Critical, Important, or Minor findings. Standards review: no remaining findings. Both reviews used separate agents.

**Identity and reproduction**

- Baseline: `79eb2d836353777309b122a4ca6750b8100633bd`.
- Final code: `5fcb452`, following `2f247d4`.
- Baseline `sim.py` SHA-256: `a89f5665fa14bc14314892b7d40943531ae06a381e2c64759d22006434559c73`.
- Final `sim.py` SHA-256: `9382e659b308eae550822d1638cc2e7a1db81f9e52b1897ff0eaad12138bd86d`.
- Frozen model SHA-256: `89513398373c6e0e81286419962feb3e312742de14a76d02dd0d819ad5264a5a`.

The canonical after audit and final benchmark use the final source hash. The balanced summary retains its earlier source hash explicitly.

Run these commands from the repository root. The audit and benchmark acquire the shared runtime lock internally.

```sh
coffee_python=/Users/taras/Documents/code/hackspain/.venv-coffee/bin/python
coffee_model=/Users/taras/Documents/code/hackspain/sim/coffee_sorter/models/live_green_arabica.joblib
physics_evidence=thoughts/taras/qa/evidence/2026-09-20-cinta-physics-repair
git show 79eb2d8:sim/coffee_sorter/sim.py > /private/tmp/cinta-sim-before.py
"$coffee_python" "$physics_evidence/run_audit.py" --sim-source /private/tmp/cinta-sim-before.py --model "$coffee_model" --full-pool --output /private/tmp/cinta-before.json
"$coffee_python" "$physics_evidence/run_audit.py" --sim-source sim/coffee_sorter/sim.py --model "$coffee_model" --full-pool --output /private/tmp/cinta-after.json
"$coffee_python" "$physics_evidence/run_benchmark.py" --before-source /private/tmp/cinta-sim-before.py --after-source sim/coffee_sorter/sim.py --output /private/tmp/cinta-performance.json
"$coffee_python" - <<'PY'
import fcntl, sys, unittest
sys.path.insert(0, 'sim/coffee_sorter')
with open('/private/tmp/hackspain-coffee-runtime.lock', 'a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    suite = unittest.defaultTestLoader.loadTestsFromNames([
        'test_sim_physics', 'test_sim_lifecycle', 'test_controller',
        'test_sensor_realism', 'test_continuous_engine', 'test_evidence',
        'test_object_definitions'])
    result = unittest.TextTestRunner().run(suite)
    sys.exit(not result.wasSuccessful())
PY
```

For the balanced audit, replace `--full-pool` with `--all-classes` in each audit command.

Evidence: [Stone before](evidence/2026-09-20-cinta-physics-repair/stone-before.jsonl), [Stone after](evidence/2026-09-20-cinta-physics-repair/stone-after.jsonl), [balanced summary](evidence/2026-09-20-cinta-physics-repair/balanced-summary.json), [performance](evidence/2026-09-20-cinta-physics-repair/performance.json), [test log](evidence/2026-09-20-cinta-physics-repair/tests.txt).

Before release, retrain with the integrated physics/catalog, validate the Keep anomaly reference, and calibrate rejection on held-out seeds and realistic feed density.

The existing source-freeze test still targets historical commit `511f104`. Its three previously reported source mismatches are outside this focused suite. No protected-source assertion was weakened.

This task changed no service, public deployment, or live policy. It recommends the physics patch for training integration, not deployment of the frozen model.
