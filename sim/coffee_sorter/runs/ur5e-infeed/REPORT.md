# UR5e infeed pick-cell evidence

## Result

This is a **kinematic ideal-grasp result**, not physical grasp or end-to-end
hardware validation. Camera pixels select red foreign matter by measured size;
the real Menagerie UR5e follows mink/DAQP trajectories while the belt remains
moving. A success requires camera selection, tolerance-gated ideal attachment,
lift, and ideal placement at the reject-bin pose. Tool proximity alone is never
counted.

| case | belt m/s | large-debris denominator | success | missed | outside configured workspace | timed out | max queue wait s | non-large selected |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| nominal | 0.10 | 2 | 2 | 0 | 0 | 0 | 0.00 | 0 |
| burst | 0.28 | 4 | 3 | 1 | 0 | 0 | 3.38 | 0 |
| workspace | 0.10 | 1 | 0 | 0 | 1 | 0 | 0.00 | 0 |

The nominal case spaces two reachable oversized pieces. The burst case combines
a 0.28 m/s belt (2.8x nominal) and tight arrivals; its misses and observed queue
waits remain in the evidence, but this run does not isolate or prove overload
causality. The workspace case exercises the configured x exclusion and does not
claim to certify the arm's physical reach. One deterministic run per case is not
a robustness or statistical claim.

## Method and limits

- Oversize means camera-measured maximum footprint **>= 45 mm**.
- Grasp/reach tolerances are 18/12 mm; pick timeout is 3.2 s; IK timestep is 0.02 s.
- The camera runs at a fixed, class-independent 12.5 Hz cadence.
  Pixel detections are proximity-associated against all arrived feed objects;
  simulator identity and known object height are disclosed oracle inputs for
  association/evaluation and ideal attachment. Selection itself calls the
  measured-size production rule and is never gated by true class.
- Objects move continuously at the configured belt speed until ideal attachment;
  the belt is not stopped. The arm has no actuator dynamics, gripper geometry,
  contact closure, payload slip, collision avoidance, or safety-rated controls.
  Belt/bin fixture collisions are disabled, and bin capture is an ideal placement
  rather than a dynamic drop/contact result.
- Queue residence includes every selected object, ending at service, policy
  exclusion, a terminal miss or run end; run-end waits are right-censored.
- Joint position ranges come from the pinned model; configured velocity limits,
  observed peaks, 10 Hz sampled joint/EE trajectory, per-object events, and
  preserved outcome denominators are in the JSON/CSV artifacts.

## Reproduce

```bash
cd sim/coffee_sorter
python3 -m venv .venv-picking
.venv-picking/bin/pip install -r requirements.txt -r requirements-picking.txt
MENAGERIE=$(./setup_ur5e_pick.sh)
# If the host lacks OSMesa, run ./setup_mesa.sh and apply the printed exports.
TEST_LOG=$(mktemp)
set -o pipefail
MUJOCO_GL=osmesa PYOPENGL_PLATFORM=osmesa \
MUJOCO_MENAGERIE_DIR="$MENAGERIE" REQUIRE_UR5E_MODEL=1 \
  .venv-picking/bin/python -m unittest test_ur5e_infeed.py -v 2>&1 | tee "$TEST_LOG"
MUJOCO_GL=osmesa PYOPENGL_PLATFORM=osmesa \
MUJOCO_MENAGERIE_DIR="$MENAGERIE" REQUIRE_UR5E_MODEL=1 \
  .venv-picking/bin/python -m unittest discover -p 'test_*.py' -v 2>&1 | tee -a "$TEST_LOG"
MUJOCO_GL=osmesa PYOPENGL_PLATFORM=osmesa \
  .venv-picking/bin/python run_ur5e_infeed.py \
  --menagerie-dir "$MENAGERIE" --output runs/ur5e-infeed \
  --tests-log "$TEST_LOG" --rerun
```

Menagerie source: `google-deepmind/mujoco_menagerie` commit
`8161bba264d7fa7c99ca301e91e7fb44737676ad`, UR5e assets under BSD-3-Clause. The robot checkout is
cached outside this repository by `setup_ur5e_pick.sh`; no large external assets
are copied into Git. Exact Python package pins are in `requirements-picking.txt`.
The optional root-free Mesa helper resolves its listed Ubuntu packages at runtime;
that system recipe is unpinned and the runtime OS is recorded in metrics.

## Artifact map

- `metrics.json`, plus per-case `metrics.json`: configuration/source identity and counts.
- `feed_manifest.json`: deterministic inputs and classification denominator.
- `events.json`: camera observation, selection, ideal attachment and terminal events.
- `trajectory.csv`: 10 Hz sampled state, end-effector target error and six joint positions.
- `outcomes.png`, per-case `contact_sheet.png`, and short `overview.mp4` videos.
- [`tests.log`](tests.log): actual focused and full-project unittest results.
- `completion.json`: final SHA-256 inventory of the saved suite artifacts.

Test counts and status are reported only by the linked log; this report does not
embed a generated hard-coded pass count.
