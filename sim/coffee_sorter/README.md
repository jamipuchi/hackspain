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

**Next**

- [ ] speed up `vision.detect` (statistics over foreground pixels only) and train the classifier
- [ ] first closed-loop run with metrics + video
- [ ] rate sweep 500 → 3000 beans/s, latency vs the 73 ms camera-to-jet budget
- [ ] `roasted` profile without touching the controller (generalisation)
- [ ] UR5e (Menagerie + mink) picking oversize foreign matter off the infeed — the one thing the air jets cannot do
- [ ] one-slide summary

## Renders so far

`runs/preview/overview.png`, `runs/preview/discharge.png`, `runs/preview/topdown.png`, and the camera strip
`runs/preview/inspection_labeled.png` (boxes = detected blobs, text = ground-truth class where matched).

## Handoff notes (for whoever continues)

- Python env: `~/robotics/.venv` (created with `uv`; add packages with `uv pip install --python ../.venv/bin/python <pkg>`). Run everything from this folder with `../.venv/bin/python run.py ...`. Assets regenerate automatically (`assets.build()` is called by the CLI).
- **Known bottleneck**: `vision.Inspector.detect` builds `xs, ys = np.divmod(np.arange(H*W), W)` and runs ~20 `np.bincount` calls over all 400k pixels per frame (~40–50 ms). Restrict everything to `idx = np.flatnonzero(mask)` (foreground ≈ 5 % of pixels) and precompute the coordinate grids once in `__init__`. Target: < 5 ms per frame, so 250 fps of simulated camera costs ~1 s wall per simulated second. `run.py train` was started once and killed for being slow because of this; no model exists in `models/` yet.
- Training then is: `../.venv/bin/python run.py train --profile green_arabica --seconds 24 --rate 900 --boost 5` → `models/green_arabica.joblib` + `runs/train_green_arabica/{report.json,confusion.png}`. `--boost` multiplies defect priors so classes are balanced.
- The closed loop is `run.py run` (untested end to end until a model exists). Things to verify on the first run: (1) valves actually deflect beans below the splitter (`per_class[...]['rejected']` vs `['targeted']` in `metrics.json`; tune `JET_FORCE`, `Policy.base_pulse`, or `Layout.split_z_drop`), (2) `late_decisions` is 0 (latency budget is `(ej_x - cam_x)/belt_speed` = 73 ms), (3) `spilled_rate` stays low (beans lost off the belt sides or bouncing; `Bean.last_pos` says where).
- Body pool: 1150 ellipsoid beans ≈ 2000 beans/s × 0.5 s transit. If `pool_starved` grows in `metrics.json`, raise `Layout.n_ellipsoid` (physics cost is roughly linear in active contacts).
- Physics conventions: x = belt travel, y = across the belt, z = up; belt surface at `Layout.belt_z = 0.60`; belt end at x = 0; camera strip centred at `cam_x = -0.12`; nozzles at `ej_x = 0.10`; splitter blade at `split_x = 0.34`, `split_z_drop = 0.125` below the belt.
- Do not put beans back on `implicit` integrator hoping for stability: it is 10× slower here; the per-body rotational damping/armature in `sim.spawn` is what keeps `implicitfast` stable.
- Robot-arm extension idea (not started): an infeed inspection belt at ~0.3 m/s ahead of the sorter with a UR5e from `../mujoco_menagerie/universal_robots_ur5e` and differential IK from `mink` (see `../demos/mink_ur5e_ik.py`) picking stones/sticks/clumps that air jets cannot move. Keep it a separate module; the fast sorter must not depend on it.
