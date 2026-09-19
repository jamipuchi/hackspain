# Coffee, sorted

A self-contained three.js replay of real MuJoCo coffee-sorter trajectories. Open `index.html` through a local HTTP server, or use the [published page](https://hack.agent-swarm.dev/p/07a0b9015bac42d0b779988efdcfb9e8).

```sh
cd sim/coffee_sorter/web
python3 -m http.server 8000
# Open http://localhost:8000. No npm install or internet needed for playback.
```

Play/pause, scrub, choose 0.1×–1× speed, orbit/zoom, or select overview, sorting, and inspection cameras. “Highlight decisions” tints beans using recorded controller decisions; unknown/unmatched beans stay untinted. It never uses final outcomes to invent a decision.

## What is recorded

- Four seconds, seed 7, green arabica, specialty policy, 1,000 beans/s, 0.06 N air jets, 2 ms physics steps.
- 4,000 unique beans; peak 551 concurrent; 121 samples at nominal 30 fps. Sample times use actual completed simulation steps, with alternating 32/34 ms spacing.
- `replay.json`: 2,782,621 bytes (2.78 MB decimal). Gzip: 972,967 bytes. The complete HTML is approximately 1.84 MB, including bundled three.js and compressed data.
- Every visible row references stable bean identity/class/dimensions, then stores position, orientation and the latest matched camera decision. Positions are quantized to 0.1 mm, quaternions to 0.0001 per component; position rounding error is at most 0.05 mm per axis. The browser interpolates positions and uses quaternion slerp between recorded poses. It does not simulate bean motion.
- 842 scheduled valve openings are recorded directly from `sim.fire`, including exact on/off times and nozzle indices. Air-puff halos fade for 65 ms after valve close for visibility; the plume is a visual cue, not a fluid simulation. No unrecorded valves fire.
- HUD counters come from simulation outcomes at the splitter: resolved beans, defects rejected, and good beans rejected or spilled. They include startup; these are counts for this short replay, not benchmark accuracy claims. Final counts: 3,552 / 306 / 129. Outcomes are recorded before each physics integration; tests account for that 2 ms timestamp convention. Beans can remain visible downstream after an outcome is recorded, until body recycling.
- Source revision `340e734d06a91b589248ab6d35f20520ebab22b6` from `coffee-sorter-closed-loop`. The JSON includes hashes of all six simulation/controller source modules and of the trusted trained model.

## Geometry and materials

The exporter extracts compiled MuJoCo box/cylinder transforms and dimensions for the feeder, conveyor, rollers, camera housing, ejector manifold, splitter and bins. The viewer reconstructs these using procedural geometry, plus visual camera supports. All 64 nozzles use the exported layout. No machine glTF download is needed.

Each class uses one `InstancedMesh`; low-poly creased ellipsoids retain the simulated per-bean dimensions and orientations. Stone classes use boxes; sticks use elongated ellipsoids. Broken beans have simplified ellipsoid surfaces rather than the original half-bean visual mesh. Class colors derive from the simulation profile; optional decision tints are a display aid.

Steel, frame, rubber belt and beans use PBR materials. A generated [RoomEnvironment](https://threejs.org/docs/pages/RoomEnvironment.html) supplies reflections; ACES tone mapping and soft machine shadows finish the scene. Floor lighting uses a cheaper diffuse material. The stationary machine shadow map is rendered once; beans receive shadows but do not cast dynamic shadows. Additive sprites supply the jet glow without a full-screen bloom pass.

[Poly Haven HDRIs are CC0](https://polyhaven.com/license), but a generated studio environment avoids additional bytes and keeps the single-page/offline contract. Procedural machine geometry also preserves the sim dimensions. Asset evaluation ended there.

Vendored three.js **0.180.0** (leading whitespace normalized in two files), with MIT license in `vendor/THREE-LICENSE.txt`. No CDN, font, image, model or data request is required by the standalone page. Modern WebGL 2 and `DecompressionStream` support are required. Software-renderer detection reduces internal resolution to 0.6×; hardware rendering uses up to 1.5× device pixel ratio.

## Rebuild the page

Node/npm and Python 3 are build-time dependencies only. esbuild is pinned in `package-lock.json`.

```sh
npm ci
npm run build
npm test
```

`template.html` contains the interface, `viewer.js` contains the viewer, and `build_page.py` bundles only local sources and embeds gzip-compressed `replay.json`. Commit the generated `index.html` alongside its sources. The builder refuses to exceed the swarm Page 5 MiB body limit.

## Export a different run

This branch starts from `main` and does not copy unrelated closed-loop changes into its diff. Re-exporting requires the improved simulator checkout and its trained model; playback needs neither. With a clean checkout of the source revision:

```sh
# Run from the repository root; use your own suitable worktree directory.
git worktree add --detach /tmp/coffee-replay-sim 340e734d06a91b589248ab6d35f20520ebab22b6
python3 -m venv /tmp/coffee-replay-env
/tmp/coffee-replay-env/bin/pip install -r /tmp/coffee-replay-sim/sim/coffee_sorter/requirements.txt
# On a headless Linux worker, follow that checkout's setup_mesa.sh first.
# Use an existing trusted models/green_arabica.joblib, or train a new model:
(cd /tmp/coffee-replay-sim/sim/coffee_sorter && /tmp/coffee-replay-env/bin/python run.py train --profile green_arabica --seconds 24 --rate 900 --boost 5)
/tmp/coffee-replay-env/bin/python sim/coffee_sorter/export_replay.py \
  --sim-dir /tmp/coffee-replay-sim/sim/coffee_sorter \
  --seconds 4 --fps 30 --rate 1000 --jet-force 0.06 --seed 7 \
  --output sim/coffee_sorter/web/replay.json
(cd sim/coffee_sorter/web && npm run build && npm test)
```

`--model` can select a different **trusted** local joblib model. Retraining changes the model hash and predictions. Controller latency includes measured wall time, so another export need not be bit-identical even with the same seed. Source hashes and the dataset preserve the provenance of the shipped recording.

The exporter requires component membership from the closed-loop Inspector. It matches decisions only to actual component members; unmatched decisions remain unknown rather than guessing which bean they refer to. The exporter observes the controller, never feeds ground truth into it.

Optional direct physics verification compares the first three recorded frames to a fresh MuJoCo run and verifies source hashes:

```sh
COFFEE_SIM_DIR=/tmp/coffee-replay-sim/sim/coffee_sorter \
  /tmp/coffee-replay-env/bin/python -m unittest discover \
  -s sim/coffee_sorter/web -p 'test_*.py'
```

## Scope limits

Four seconds rather than sixteen; simplified bean surfaces; visual air puffs rather than fluid dynamics; static machine shadows and glow sprites rather than dynamic bean shadows and full-screen bloom. No claim of 60 fps on a GPU is made from this worker's software-rendered browser. See `QA.md` for measurements and browser checks.
