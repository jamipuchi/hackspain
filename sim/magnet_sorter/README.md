# GPT-6 sorts hardware with an Arduino servo arm and an electromagnet

Five **builds** share one software stack; pick one with `--build`:

| build | hardware | task |
| --- | --- | --- |
| `hobby_v1` | MDF board, 3× MG996R printed arm, IRF520 MOSFET, 5 V magnet | pick every ferrous part into one tray (mixed metals) |
| `taras_v1` | **the Madrid shopping list**: wooden 7 + 7 cm arm, 2× MG90S + MG946R, Uno R3, 5 V relay, 24 V XP20/15 magnet, white pad, 3 clear cups | sort screws / nuts / washers (+ brass distractor) |
| `taras_conveyor` | taras_v1 + homemade belt on a DS04-NFC continuous servo | belt advances, stops, sort the pick zone, repeat |
| `taras_kitting` | taras_v1 | one screw + one nut + one washer per cup |
| `taras_grading` | taras_v1 | screws by length; rusty ones rejected |

Two **brains**: `--brain agent` (default) lets GPT-6 act one tool call at a time — `take_photo` (with zoom),
`move_to`/`nudge` in cm, `magnet`, `pick_at`, `place_in`, `home`, `belt_advance`, `done` — with a fresh
photo after every motion and a mandatory final-photo check before `done` is accepted. `--brain program`
is the older one-batch-per-photo planner. One or two oblique phone cameras (`--cameras A` or `A,B`,
≤ 50 cm from the scene, never on top of the arm) are ArUco-calibrated; the photo carries a metric cm grid.
`--viewer` opens the MuJoCo window so you watch the run live (needs `mjpython`).

```bash
cd ~/robotics/magnet_sorter
../.venv/bin/mjpython run_demo.py --build taras_v1 --brain agent --cameras A,B --phone mujoco --viewer   # live, fast photos
../.venv/bin/python   run_demo.py --build taras_v1 --brain agent --cameras A,B --phone cycles          # Cycles photos for GPT-6
../.venv/bin/mjpython run_demo.py --build taras_v1 --brain program --planner oracle --viewer            # mechanics only, perfect vision
```

---

## hobby_v1 (first realistic build)

A hobby build, simulated faithfully and rendered with a path tracer:

```
 phone camera ──photo──▶ GPT-6 (gpt-6-astra) ──JSON program──▶ Python controller (IK)
                                  ▲                                     │ "S 104 77 74" / "M 1"  (USB serial, 115200)
                                  │                                     ▼
       "piece is gone from the table" / "still there"          Arduino Uno firmware ──▶ 3× MG996R servos
                (camera differencing, no ground truth)                                └▶ IRF520 MOSFET ──▶ 5 V electromagnet
```

- **Physics**: MuJoCo. Servos ramp at 300°/s with 1° pulse quantisation and a 1 N·m torque limit, so
  the arm sags and overshoots like the real thing. The electromagnet is a distance-falloff force
  field (25 N at contact, ~1 cm effective range) that only acts on carbon-steel parts; switching it
  off drops whatever it holds. Non-ferrous parts never stick, and nearby steel parts can get dragged.
- **Render**: Blender 5.2 Cycles on the M5 Max GPU, PBR metals (zinc iridescence, brushed stainless,
  matte aluminium, brass, copper), FDM layer lines on the PLA, MDF fibre bump, HDRI + desk lamp.
  MuJoCo body poses are pushed to Blender every frame, so physics and picture always agree.
- **Perception is honest**: GPT-6 gets a phone photo (1280×960, mild blur/noise/vignette/JPEG).
  Zinc steel vs stainless vs aluminium is genuinely ambiguous. The only truth it ever receives is
  the arm's feedback: after each pick the camera looks at the spot and reports whether the piece left.
- **Calibration is honest**: pixel→table is a homography fitted to the four ArUco markers in the
  photo (0.75 mm reprojection error). The same code runs on a real iPhone frame.
- **Software is the real stack**: `controller.py` only talks the serial protocol. `firmware/magnet_arm/`
  is the actual Arduino sketch; pass `--port /dev/tty.usbmodem…` to drive a real Uno.

## Run

```bash
cd ~/robotics/magnet_sorter
../.venv/bin/python run_demo.py                          # GPT-6 + Cycles phone photos (default)
../.venv/bin/python run_demo.py --seed 11                # reproduce the recorded run
../.venv/bin/python run_demo.py --planner mock           # no API: naive grey-blob planner, still learns from feedback
../.venv/bin/python run_demo.py --phone mujoco           # rasterised photos, no Blender, ~5 s per run
../.venv/bin/python make_video.py runs/<timestamp> --samples 24 --every 2      # photoreal dashboard video (≈1 h GPU)
../.venv/bin/python make_video.py runs/<timestamp> --renderer mujoco           # fast preview video
```

Each run writes `runs/<timestamp>/`: `roundN_photo.png` (what GPT-6 saw), `roundN_plan.png/json`
(its answer), `phone/*.png` (raw Cycles frames), `serial.log`, `trajectory.npz` + `events.json`.
The OpenAI key is read from `OPENAI_API_KEY`, `OPEN_AI_KEY`, or `~/Documents/leads-gpt/api/.env`.

## Recorded run (seed 11): 4/4 ferrous parts in the tray, 4 rounds, $0.29

| round | GPT-6 said | what happened |
| --- | --- | --- |
| 1 | 11 pieces listed; hedged "zinc_steel (ferrous?)" on 5; picked black nut, big "zinc" nut, zinc bolt | black nut ✓, "zinc" nut ✗ (it was M10 stainless), bolt ✓ |
| 2 | relabelled the M10 nut stainless; tried "zinc" bolt, M8 nut, M8 washer | bolt ✗ (M6 stainless), nut ✗ and washer ✗ (magnet missed, close together near the inner reach limit) |
| 3 | retried the M8 nut and washer, tried the tiny M5 nut | nut ✓, washer ✓, M5 ✗ (stainless) |
| 4 | "the seven remaining pieces appear non-ferrous based on pickup feedback and appearance" | done, tray contains exactly the four carbon-steel parts |

Final material labels were 11/11 correct, all learned from feedback where appearance was ambiguous.

## Bill of materials (the real build this simulates)

| part | notes |
| --- | --- |
| Arduino Uno R3 | USB serial to the laptop |
| 3× MG996R servo | base yaw, shoulder, elbow; ~1 N·m stall at 6 V |
| 3D-printed arm (PLA) | links 150 mm + 160 mm, shoulder 78 mm above the board, passive wrist hinge |
| 5 V electromagnet, 25 N ("2.5 kg") | 20 mm Ø, hangs 45 mm below the wrist pivot |
| IRF520 MOSFET module | switches the magnet from pin D7 |
| 5 V 3 A supply + breadboard | servos and magnet must not run off the Uno's 5 V pin |
| MDF board 500×400×12 mm | 4 printed ArUco markers (DICT_4X4_50, ids 0–3, 35 mm) at known positions |
| phone on a boom | overhead at 0.42 m, Continuity Camera or any webcam |
| blue PLA tray | the ferrous bin |

Wiring: D9/D10/D11 → servo signals, D7 → MOSFET SIG, MOSFET V+/GND → magnet, supply GND tied to
Arduino GND. Full pinout in the sketch header.

## Files

| file | role |
| --- | --- |
| `scene_def.py` | single source of truth: dimensions, materials, every geom, piece list |
| `mujoco_model.py` | MuJoCo XML from scene_def (physics + preview look) |
| `hardware.py` | `SimArduino` (firmware emulation, servo ramps, magnet force field) and `SerialArduino` (pyserial) |
| `controller.py` | 3-DOF inverse kinematics, pick/place ops, camera-diff pickup verification |
| `camera.py` | ArUco homography calibration, phone-look post-processing, MuJoCo/real cameras |
| `brain.py` | GPT-6 prompt + strict JSON schema (Responses API); `MockPlanner` for offline runs |
| `run_demo.py` | the loop; records trajectory + events for the video |
| `blender/scene_builder.py` | builds the PBR Blender scene from scene_def |
| `blender/render_server.py` · `blender_client.py` | Blender kept alive as a render server (Cycles, Metal) |
| `make_video.py` | dashboard video: Cycles 3D view + GPT-6 input/output + serial + feedback |
| `firmware/magnet_arm/magnet_arm.ino` | the real Arduino sketch (same protocol) |
| `build_assets.py` | hex mesh, MDF/PCB/breadboard textures, ArUco marker PNGs |

## Using a real iPhone camera

`camera.RealCamera(index)` opens Continuity Camera through AVFoundation. macOS asks for camera
permission the first time; run from Terminal.app so the prompt appears, with the iPhone unlocked
and on the same Apple ID. Print the four markers (`assets/aruco_*.png`, 35 mm) and glue them at
the positions in `scene_def.MARKERS`; calibration then needs no other setup. Real-camera mode is
wired but has not been exercised in this session (no camera permission for the agent shell).
