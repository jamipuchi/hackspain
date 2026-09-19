# Coffee bean optical sorter — MuJoCo simulation

**THEKER Robotics · HackSpain '26 · "Automatiza una tarea que hoy hace una persona"**

The task: **hand-sorting green coffee**. In producing countries and in specialty roasteries, people
still pick defective beans (black, sour, insect-damaged, broken, shells, husks) and foreign matter
(stones, sticks) off a table or a slow belt, all day. It is repetitive, needs a trained eye, and the
variability is exactly what hand-coded automation struggles with: every bean is a different size,
colour, orientation and shade, defects are subtle, and beans touch and overlap.

This repo simulates a **belt-type optical sorter** end to end in MuJoCo and runs a full
perception → decision → actuation loop on it at **thousands of beans per second**:

```
 vibratory feeder ─▶ 3 m/s belt (singulates beans) ─▶ top-down camera strip ─▶ beans fly off the belt end
                                                            │                          │
                                              segmentation + features         64 air-jet valves fire on
                                              learned classifier              predicted time of flight
                                              open-set anomaly detector       (defects knocked below a splitter)
                                                            │                          │
                                                       decision  ──────────────▶  accept bin / reject bin
```

Why not a robot arm? A pick-and-place arm does ~1 bean/s. Industrial optical sorters do 1–2 t/h
per chute (~2000–4000 beans/s) with a camera and pneumatic ejectors. We model the terminal that is
actually coherent with the task, and we model it honestly: the controller only ever sees camera
pixels, and the valves push whatever is physically inside the jet (so a misfire can knock a good
bean into the reject bin, and that shows up in the metrics).

## Layout

| file | what |
| --- | --- |
| `profiles.py` | **Product profiles as data**: classes, priors, sizes, colours, textures, densities, severity. `green_arabica` (10 classes) and `roasted` (quakers/burnt) for the generalisation demo. |
| `assets.py` | Procedural bean textures (mottling, crease, insect holes) and the half-bean mesh. |
| `scene.py` | MJCF builder: belt, guides, feeder, camera bridge, ejector manifold with 64 nozzle sites, splitter, bins, body pools. |
| `sim.py` | Physics: Poisson feed with ground truth, moving belt (friction transports beans), air-jet force fields, accept/reject capture, body recycling. |
| `vision.py` | Inspection camera (orthographic, 2080×192 px = 0.25 mm/px over a 0.52×0.048 m strip, 250 fps sim time), belt segmentation, vectorised per-blob features (shape moments, colour, dark-spot count …). |
| `classifier.py` | Gradient-boosted classifier trained from simulated ground truth + Mahalanobis anomaly detector on the "good" class (open-set: never-seen foreign objects still get rejected). |
| `controller.py` | Multi-frame tracking, probability fusion, velocity estimate, time-of-flight valve scheduling, mass-adaptive pulse length, latency accounting. |
| `run.py` | CLI: `preview`, `train`, `run` (metrics + MP4), `bench` (rate sweep), `viewer` (live MuJoCo window). |
| `render.py` | Overview cameras, HUD, MP4 writer. |

## Run

```bash
cd ~/robotics/coffee_sorter
../.venv/bin/python run.py preview                          # renders -> runs/preview/*.png
../.venv/bin/python run.py train --profile green_arabica    # learn the classifier from sim ground truth -> models/
../.venv/bin/python run.py run --rate 2000 --seconds 8 --video   # full sorting run -> runs/<ts>/metrics.json, overview.mp4
../.venv/bin/python run.py bench --rates 500,1000,2000,3000     # throughput sweep -> bench.png
../.venv/bin/mjpython run.py viewer --rate 1500             # live window (macOS needs mjpython)
```

Environment: the `../.venv` from the parent folder (MuJoCo 3.13, numpy, OpenCV, scikit-learn, matplotlib).

For a fresh checkout, create a separate environment with the tested package versions:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
export MUJOCO_GL=osmesa  # headless CPU rendering; requires libOSMesa6
export LP_NUM_THREADS=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
.venv/bin/python check_detect.py --iterations 100
.venv/bin/python -m unittest discover -p 'test_*.py' -v
.venv/bin/python run.py train --profile green_arabica --seconds 24 --rate 900 --boost 5
.venv/bin/python run.py run --rate 2000 --seconds 8 --video
```

Run these commands from `sim/coffee_sorter/`. If Mesa is installed in a custom
location, add its library directory to `LD_LIBRARY_PATH`. EGL is another option
when the host provides a working EGL driver. The detector check compares every
feature and blob coordinate exactly against `cad5f9b`, then reports timing on a
camera frame when available. The timing target is hardware dependent.

Reproduce the overnight experiments in the same environment:

```bash
.venv/bin/python run.py bench --rates 500,1000,2000,3000 --seconds 4 --name rate-sweep
.venv/bin/python run_characterization.py latency
.venv/bin/python run_characterization.py tuning
.venv/bin/python plot_characterization.py rate --group-dir runs/rate-sweep --output runs/rate-sweep/rate_summary.png
.venv/bin/python plot_characterization.py latency --group-dir runs/latency-sweep --output runs/latency-sweep/latency_summary.png
```

The batch runner resumes only after validating a complete run's metrics,
decisions, evidence and six images. It reruns incomplete experiment directories.
Use a process supervisor with automatic restart disabled for long batches.
`--controller-delay-ms` adds simulated availability delay; the
`--fixed-controller-latency-ms` option imposes a minimum total latency and never
hides slower measured CPU work. `--target-nozzles` fixes valves per target;
`--nozzles` changes the physical bank size. They are different experiments.

## What the system handles (variability)

- 10 classes in the feed, with continuous variation in size (screen 14–18), colour, texture, orientation (random yaw + tilt), and position on a 0.5 m wide belt.
- Beans touching / overlapping in the image (the tracker and classifier see merged blobs; performance on them is measured, not hidden).
- Foreign objects of very different mass (a 1 g stone needs ~5× the air of a 0.2 g bean): the pulse length and number of nozzles adapt to the estimated mass.
- New products / defects = a new entry in `profiles.py` + `run.py train`. No controller code changes.

## Progress log

**Day 1 — physics and camera up**

- Scene builder, body pools (1150 beans + broken/stones/sticks pools), orthographic inspection camera, overview cameras.
- Found and fixed four instabilities before the plant ran at 2000 beans/s:
  1. Beans rolled like wheels at ~1000 rad/s on a *static* belt geom and hopped → belt is now a body on a slide joint whose position is reset every step while its velocity is held at 3 m/s (the MuJoCo conveyor idiom); friction alone transports the beans.
  2. Tiny ellipsoid-vs-ellipsoid contacts (generic convex collider) catapulted beans → each bean has an analytic **capsule** collision geom with the same length and resting height; the ellipsoid is visual only.
  3. Light, elongated bodies landing on a corner picked up hundreds of rad/s and the explicit gyroscopic term in `implicitfast` diverged → rotational damping and armature scaled to each body's own inertia (set at spawn), softer contacts.
  4. Beans spawning on top of each other at 2000/s → overlap-free hand-off spots (a real vibratory feeder meters a single layer).
- Runtime mass/size edits also need `dof_invweight0` / `body_invweight0` updated or the solver mis-scales contacts.
- Rendering with ~1200 bodies cost 46 ms per frame regardless of resolution → off-strip beans are moved to a hidden geom group before each inspection render (5.6 ms at 2080×192).
- The renderer draws the kinematics of the *previous* step: frames are timestamped `t - dt` and training labels use the rendered poses. Calibration error after that: 0.02 mm median.
- Status: physics stable for 5+ simulated seconds at 2000 beans/s (≈1.5–3 s wall per simulated second, no viewer), camera strip segments beans against the blue belt, 23/23 blobs matched to ground truth in the check frame.

**19 September — foreground detector and first trained classifier**

- Detector statistics now use foreground pixels and cached coordinate grids.
  Exact feature/coordinate equality against `cad5f9b` passes synthetic and real
  frames, all edges, alternate sizes and non-contiguous inputs. On this CPU with
  OpenCV using 32 threads, a 192×2080 frame with 9.237% foreground improved from
  79.038 ms to 5.443 ms median (new p95 5.635 ms), about 14.5× faster. The requested
  <5 ms target is **not met**; `check_detect.py --iterations 100 --max-p50-ms 5`
  correctly fails that threshold. No feature tolerance was relaxed.
- The requested 24 s, 900 beans/s, defect-boost 5 training run collected 61,929
  labelled blobs. The 46,446 / 15,483 train/test split gave 97.91% accuracy,
  96.99% defect recall and 1.02% good-bean false rejection.
- These are simulated, blob-level holdout scores. Repeated views of a bean can
  occur in both splits; they are not an independent-bean or real-camera accuracy
  claim. Closed-loop physical outcomes are reported separately.
- Fixed two runtime blockers: purity metrics tried to hash mutable `Bean`
  records, and overview rendering hid beans culled from the inspection strip.
  Regression tests cover those paths and controller scheduling.

The first 8 s, 2000 beans/s video run evaluated 13,070 beans at an effective
1976.4 beans/s. It removed 35.80% of defects, rejected 8.05% of good beans, and
spilled 5.03%; one decision was late and the pool starved on 170 attempts.
Classifier accuracy alone did not translate into good physical sorting.
The complete baseline is preserved in the task's `first-run.tar.gz` attachment.

The final run (`20260919_015039_green_arabica_2000`) evaluated 13,134 beans at
1983.1 simulated beans/s: **38.89% defect removal, 7.96% good-bean loss, 4.72%
spills, zero late decisions**, and 122 pool-starved attempts. Camera-centre
decisions now retain already-decided tracks for association until they disappear,
preventing repeat valve pulses. Nine regression tests pass. An intermediate run
(`20260919_014250_green_arabica_2000`) had duplicate actuation and is diagnostic
only; do not use it as final validation.

Latency now adds the modeled 4 ms exposure/transfer delay to measured CPU work,
including finalization. Final p50/p99/max were 40.87/45.69/81.73 ms against the
nominal 73.33 ms camera-centre-to-jet budget. Zero observed late reject decisions
is not a worst-case timing guarantee: maximum frame latency exceeded that budget.
Synthetic rendering time is excluded from hardware-camera latency. The video run
took 284.22 wall seconds for 8 simulated seconds, so this is not real-time execution.

The jets do physically reject beans: 146 of 155 jet-hit black beans were rejected,
but 281 black beans were targeted. Shell/husk spills and missed jet intersections
remain substantial. `per_class` now includes `jet_hit`, `targeted_rejected`, and
`jet_hit_rejected` to distinguish selection, physical actuation, and capture.
`JET_FORCE`, pulse lengths, and splitter geometry remain unchanged: increasing
force is not justified by these timing/coverage and light-fragment losses alone.
The model, reports, final metrics/decisions/video, tests and review evidence are in
the task's `final-validation.tar.gz` attachment.

**19 September — attributable visual baseline**

The overnight characterization baseline is now committed with a full 8 s HUD
video and six post-run annotated inspection sheets. It records 81.79% physical
accuracy, 45.45% rejection precision, 38.85% defect recall, 7.95% good false ejects,
4.72% spills and zero late reject decisions. All eligible beans, including 14
unresolved beans, remain in the denominator. Ever-merged defect recall is 26.23%
versus 47.04% for single-only beans. Twenty-two regression tests pass.
See [NIGHT_LOG.md](NIGHT_LOG.md) for the numbers, limits and phone video links.

The 4 s rate sweep is complete. From 500 to 2000 requested beans/s, physical
recall falls from 61.62% to 42.55% while good false ejects rise from 2.87% to
8.06%. At 3000 requested beans/s, the original pool admits only 2214 beans/s
and starves on 2867 attempts. That point measures a throttled plant, not a
3000-bean/s controller. [Nine-panel rate plot](runs/rate-sweep/rate_summary.png)
and [overlap cohorts](runs/rate-sweep/cohort_summary.png) show the tradeoffs.

The [latency sweep](runs/latency-sweep/latency_summary.png) adds 0/20/30/40/60 ms
at 1000 beans/s. Late reject decisions are 0/0/6.89/79.02/99.42%; physical recall
falls to 16.53% and 5.75% at the last two points. A positive median headroom does
not guarantee all tracks meet their deadlines. The plot shows actual track
headroom and the controller's 2 ms tolerance beyond predicted jet arrival.
Camera backlog is not modeled; this tests simulated availability and transport.

The physical screen compares jet force, pulse length, splitter height, valve
coverage and pool capacity. At 1000 beans/s, reducing force from 0.09 to 0.06 N
improved seed-0 recall 44.44% → 51.45% and reduced good false ejects
5.74% → 4.08%. Fresh seed 1 repeated the direction: recall 48.50% → 51.53%,
false ejects 6.27% → 4.26%. Use `--jet-force 0.06` as an explicit demo setting;
defaults remain unchanged pending longer paired runs. The enlarged pool admits
3000 beans/s without starvation, but good false ejects reach 10.10%.
[Tuning plot](runs/tuning-sweep/tuning_1000_summary.png),
[confirmation](runs/confirmation-seed1/confirmation_summary.png), and
[readable HUD demo](runs/confirmation-seed1/force-0.06/overview_h264.mp4)
are committed with metrics and annotated frames. Twenty-six tests pass.

**19 September — product transfer after retraining**

The stock roasted training command works with no shared controller, vision or
simulation edits. Matched seed-1 runs at 1,000 beans/s give green/roasted physical
recall 51.53%/39.51% and good false ejects 4.26%/3.82%, each over 2,600 eligible
beans. Roasted blob holdout accuracy is 98.62%; repeated bean views can cross
that split. Product configuration transfers; equivalent physical quality does not.
[Confusion matrices](runs/generalization/comparison/confusion_matrices.png),
[labelled camera contact sheet](runs/generalization/comparison/camera_strip_contact_sheet.png),
[full metrics](runs/generalization/comparison/metrics.json) and
[methodology](runs/generalization/comparison/methodology.json) preserve the evidence.
Reproduce with `python run_generalization.py all` in the environment above;
the committed staged models preserve the measured run when resuming.

**Next**

- [x] foreground-only `vision.detect` with exact equivalence proof; train the classifier
- [x] first closed-loop run with metrics + video
- [ ] meet the <5 ms detector target and improve physical rejection/yield/spills
- [x] rate sweep 500 → 3000 beans/s, latency vs the 73 ms camera-to-jet budget
- [x] physical tuning screen, separate merged-bean metrics, fresh-seed demo and phone evidence
- [ ] longer paired seeds for 0.06 N; improve merged-target jet intersection and capture
- [ ] model camera backlog before claiming hardware timing margin
- [x] `roasted` profile without touching the controller; matched quality measured
- [ ] UR5e (Menagerie + mink) picking oversize foreign matter off the infeed — the one thing the air jets cannot do
- [ ] one-slide summary

## Renders so far

`runs/preview/overview.png`, `runs/preview/discharge.png`, `runs/preview/topdown.png`, and the camera strip
`runs/preview/inspection_labeled.png` (boxes = detected blobs, text = ground-truth class where matched).

New closed-loop runs also save annotated inspection sheets and
`inspection_evidence.json`. Each sheet links a numbered camera blob to its
predicted class/confidence, fused track decision, valve indices and final
outcome. `FIRED` means that a queued pulse actually reached its activation time;
it does not imply a physical hit. `own hits` identifies constituents touched by
that track's pulse. These are post-run annotations using simulator ground truth,
not information available to the controller.

Merged-blob measurements count every projected bean centre inside a connected
component. Their binary action truth is whether any constituent should be
rejected under the current policy. Single-blob multiclass accuracy, merged-blob
action correctness and physical bean outcomes have different denominators and
must not be compared as interchangeable accuracy figures.

## Handoff notes (for whoever continues)

- See [NIGHT_LOG.md](NIGHT_LOG.md) for the overnight experiment record and phone-accessible evidence. [NIGHT_PLAN.md](NIGHT_PLAN.md) tracks characterization; [the generalization plan](runs/generalization/PLAN.md) tracks product transfer and unseen objects.
- Use the fresh-checkout environment instructions above. The previous workstation used `~/robotics/.venv`; environment paths are local and are not shipped. Assets regenerate automatically (`assets.build()` is called by the CLI).
- Foreground-only detection and cached coordinate grids are implemented. The measured detector median is 5.44 ms; the <5 ms target remains open. Use `check_detect.py` to measure the current host and verify exact feature equivalence.
- The first model was trained with `run.py train --profile green_arabica --seconds 24 --rate 900 --boost 5`. The model and training report are in the prior task's archive, linked in the night log. They are ignored by Git; train or restore them for a fresh checkout. `--boost` multiplies defect priors.
- The closed loop runs end to end. Compare targeted, jet-hit and rejected counts separately in `metrics.json`; classification does not guarantee physical capture. The nominal camera-centre budget is `(ej_x - cam_x)/belt_speed` = 73.33 ms. Spills and missed jet intersections remain the primary physical issues.
- Body pool: 1150 ellipsoid beans ≈ 2000 beans/s × 0.5 s transit. If `pool_starved` grows in `metrics.json`, raise `Layout.n_ellipsoid` (physics cost is roughly linear in active contacts).
- Physics conventions: x = belt travel, y = across the belt, z = up; belt surface at `Layout.belt_z = 0.60`; belt end at x = 0; camera strip centred at `cam_x = -0.12`; nozzles at `ej_x = 0.10`; splitter blade at `split_x = 0.34`, `split_z_drop = 0.125` below the belt.
- Do not put beans back on `implicit` integrator hoping for stability: it is 10× slower here; the per-body rotational damping/armature in `sim.spawn` is what keeps `implicitfast` stable.
- Robot-arm extension idea (not started): an infeed inspection belt at ~0.3 m/s ahead of the sorter with a UR5e from `../mujoco_menagerie/universal_robots_ur5e` and differential IK from `mink` (see `../demos/mink_ur5e_ik.py`) picking stones/sticks/clumps that air jets cannot move. Keep it a separate module; the fast sorter must not depend on it.
