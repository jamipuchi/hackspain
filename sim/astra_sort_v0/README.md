# Astra sorts iron with a 3-axis electromagnet arm

GPT-6 Astra is the robot's brain. It gets the simulated overhead camera image and returns a
short movement program; the arm executes it; the outcome goes back to Astra as feedback for
the next round.

```
 MuJoCo overhead camera ──frame──▶ GPT-6 Astra ──JSON program──▶ SCARA arm + electromagnet
          ▲                                                              │
          └──────────────── "lifted piece_iron_2" / "nothing stuck" ◀────┘
```

## Video

`astra_sort_demo.mp4` (1920×1080, 36 s) is a recorded run: left the 3D simulation, top right the exact
frame GPT-6 receives and then its detections drawn on it, below that the text input and the JSON
output with the op being executed highlighted, bottom the feedback lines that go back into the next
prompt. Re-record any run with `--video path.mp4` (works headless; ffmpeg re-encodes to H.264).

## Run

```bash
cd ~/robotics
.venv/bin/mjpython astra_sort/run_demo.py                    # Astra + 3D viewer (mjpython is required for the viewer on macOS)
.venv/bin/mjpython astra_sort/run_demo.py --realtime         # same, slowed to wall-clock speed
.venv/bin/python   astra_sort/run_demo.py --headless         # Astra, no window, prints everything
.venv/bin/python   astra_sort/run_demo.py --planner mock --headless   # offline colour-threshold stand-in, no API
.venv/bin/python   astra_sort/run_demo.py --dry-run --headless        # one Astra call, print the program, don't move
```

Options: `--seed N` reproduces a layout, `--rounds N` caps the camera→plan→execute loops (default 5).
Each round writes `runs/<timestamp>/roundN_camera.png` (what Astra saw), `roundN_plan.png`
(Astra's detections + pick order drawn on it) and `roundN_plan.json`.

The OpenAI key is read from `OPENAI_API_KEY`, else `OPEN_AI_KEY`, else `~/Documents/leads-gpt/api/.env`.
One round costs about $0.05 (≈1.5k input + 0.6k output tokens) and takes 10–15 s.

## What Astra sees and says

Input: 960×720 overhead render with a labelled 100 px grid, plus the feedback lines from earlier
rounds. Output (strict JSON schema): a list of pieces `{id, u, v, material, ferrous, confidence,
reason}` and a program of ops:

| op | args | arm behaviour |
| --- | --- | --- |
| `pick` | `u, v` (pixels) | move above, lower magnet to piece height, magnet on, lift |
| `place` | `target: "iron_bin"` | move over bin, lower, magnet off |
| `home` | | park outside the workspace |
| `move_xy`, `move_z`, `magnet_on`, `magnet_off`, `wait` | | low-level primitives |

Pixels are converted to table coordinates by ray-casting through the camera model (`sim_camera.py`),
so Astra never needs to know metric coordinates.

## The scene (`scene.xml`)

- **Arm**: SCARA with J1 base yaw, J2 elbow yaw (links 0.25 m each), J3 vertical slide. Position
  actuators, real physics.
- **Electromagnet**: one inactive `weld` equality constraint per ferrous piece. `magnet_on`
  activates the weld of the nearest iron piece within 12 mm below the magnet face and 22 mm radius;
  `magnet_off` releases it. Non-ferrous pieces have no weld, so they never stick, which is exactly
  the failure Astra has to learn from if it misclassifies.
- **Pieces**: 3 iron (dark matte grey), 2 aluminum (bright silver), 1 brass (yellow), 2 plastic
  (red, blue). Random layout each run.
- **Workspace**: x 0.12–0.46 m, y ±0.18 m (white outline). Bin at (0.12, 0.26).
- **Camera**: `overhead` at 0.78 m looking straight down; an `oblique` camera exists too.

## Results so far

Seeds 7 and 3: Astra classified 8/8 pieces correctly each time and binned 3/3 iron pieces in one
round, one API call, ~$0.05. The mock planner also completes the sort but needs a second round to
discard a shadow it mistakes for iron.

## Files

| file | role |
| --- | --- |
| `run_demo.py` | the loop, logging, viewer, `--video` |
| `video.py` | dashboard video recorder (3D scene + camera + input/output + feedback) |
| `astra.py` | prompt, JSON schema, OpenAI Responses call |
| `arm.py` | SCARA IK, motion primitives, electromagnet welds, program executor, piece layout |
| `sim_camera.py` | rendering, pixel↔table mapping, grid + annotation overlays |
| `mock_planner.py` | offline colour-threshold planner |
| `scene.xml` | MuJoCo model |

## Next steps you might want

- Swap the SCARA for the Mitsubishi RV-5AS (`melfa_description` URDF) and drive it through
  MoveIt instead of the closed-form IK; the Astra interface does not change.
- Add distractors that look like iron (dark plastic) to exercise the feedback loop.
- Feed the `oblique` camera as a second image for depth cues.
