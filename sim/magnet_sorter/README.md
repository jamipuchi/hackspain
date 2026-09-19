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

## Jev tool decisions

The [experiment handoff](experiments/2026-09-19-jev/HANDOFF.md) contains measured results, reproducible commands, and the routing continuation scope.

Use `--decision-model jev` to let Jev select one tool call per step.
Astra describes the camera photos as structured observations by default. Jev receives those observations and the robot's feedback as text.
Local code supplies the available tools and their arguments. Jev selects a candidate with a typed Choice response.

From this repository's root, use the existing simulation environment:

```bash
python3 -m venv contracts/.venv  # only if this environment does not exist
contracts/.venv/bin/python -m pip install -r contracts/learning/requirements.txt openai
contracts/.venv/bin/python sim/magnet_sorter/run_demo.py \
  --build theker_v1 --brain agent --decision-model jev \
  --cameras A,B --phone mujoco --seed 4 --max-steps 60 --budget 4
```

For a visible simulation on macOS, use `mjpython` and add `--viewer`:

```bash
contracts/.venv/bin/mjpython sim/magnet_sorter/run_demo.py \
  --build theker_v1 --brain agent --decision-model jev \
  --cameras A,B --phone mujoco --viewer --seed 4 --max-steps 60 --budget 4
```

The viewer runs arm motion at approximately real time. Pauses include live API calls.
By default, the vision model describes each fresh photo before Jev selects the next tool.
The decision trace records each provider's latency separately.

Add `--reuse-pick-observation` to reuse the known part description immediately after a successful pickup.
Jev can then select the placement without another vision request.
The agent still captures photos and refreshes vision after placement, failed pickups, explicit photos, and final confirmation.

For the requested DeepSeek vision variant, set `OPENROUTER_API_KEY` and run:

```bash
contracts/.venv/bin/mjpython sim/magnet_sorter/run_demo.py \
  --build theker_v1 --brain agent --decision-model jev \
  --vision-provider openrouter --vision-model deepseek/deepseek-v4.1-flash \
  --reuse-pick-observation --cameras A,B --phone mujoco --viewer \
  --seed 4 --max-steps 60 --budget 4
```

OpenRouter uses the same structured observation schema through its Responses API. Jev still selects every tool call.
This variant requires no OpenAI key. The run budget includes OpenRouter's reported cost and Jev's estimated cost.

For the Gemini variant with lower reasoning, use:

```bash
contracts/.venv/bin/mjpython sim/magnet_sorter/run_demo.py \
  --build theker_v1 --brain agent --decision-model jev \
  --vision-provider openrouter --vision-model google/gemini-3.8-flash \
  --reuse-pick-observation --effort low --cameras A,B --phone mujoco --viewer \
  --seed 4 --max-steps 60 --budget 4
```

Set `OPENAI_API_KEY` when using Astra. Set `TYPESAFE_API_KEY` in the environment or the repository's `.env` file.
The existing `~/.config/typesafe/api_key` fallback also works.
`GPT6_MODEL` selects the vision model. `JEV_MODEL` selects the decision model and defaults to `jev-latest`.

This experiment supports `pick_at`, `place_in`, `home`, `take_photo`, `done`, and `belt_advance` for conveyor builds.
It does not expose free-form movement or inventory-editing tools to Jev.
Both brains use the existing robot controller, action limits, and final-photo confirmation.
The final observation includes visible container contents. Task-specific failure limits apply even when a part's visual ID changes.
The budget includes both providers. A request can exceed the remaining budget before its usage arrives.

Each run stores its available photos, traces, and metadata under `sim/magnet_sorter/runs/<timestamp>/`.
Completed runs save `result.json`. Failed runs save `error.json` and a partial result when the agent was initialized.
Missing OpenRouter cost stops the run and marks cost accounting incomplete.
The result records the simulator's score separately from the agent's completion claim.
The simulator's hidden part labels never enter the vision or decision requests.

Use `--decision-model astra` for the original agent. It remains the default.

## TypeSafe Jev program planner (`--planner jev`)

```bash
../.venv/bin/python run_demo.py --planner jev --phone mujoco          # any build; program brain, one plan per photo
```

[Jev](https://docs.typesafe.ai) is TypeSafe's "System One" model: text in, typed answers out (Choice / Score /
yes-no) with calibrated probabilities. It reads no images, writes no text and calls no tools, so it cannot
take GPT-6's seat one-for-one. `jev_brain.py` splits the job the way TypeSafe recommends (code in control,
the model answers narrow questions):

| stage | who | what |
| --- | --- | --- |
| perception | code | background-difference blobs inside the arm's workspace, footprint measured on the table plane through the ArUco homography (mm, elongation, hole, colour cast vs the board, brightness, shine) → a short English record per part |
| judgement | Jev | one request per part, four questions in parallel: kind, material, *will the magnet lift it?*, which container. The arm's feedback on that part (`stayed` / `lifted`) is part of the state and overrides appearance |
| control | code | tries every part Jev does not rule out (P(ferrous) ≥ 0.3), best-supported first, three per photo, at most two attempts per part; a low-confidence container falls back to the build's `unknown` tray when it has one |

Appearance alone cannot separate zinc from stainless (Jev says so: P ≈ 0.5), so like GPT-6 it learns from the
magnet. A round costs about 0.05 ¢ and 2–4 s for eleven parts (jev-latest: $0.042 per million input tokens,
output free, 64k tokens per request, 429/529 → backoff). The key is read from `TYPESAFE_API_KEY`, else
`~/.config/typesafe/api_key`. Plain HTTP (`urllib`), no SDK needed; `pip install typesafe-sdk` exists if you
want the typed client.

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
| `jev_brain.py` | TypeSafe Jev planner: model-free part detector + per-part Choice/Noul questions + confidence-gated program |
| `run_demo.py` | the loop; records trajectory + events for the video |
| `blender/scene_builder.py` | builds the PBR Blender scene from scene_def |
| `blender/render_server.py` · `blender_client.py` | Blender kept alive as a render server (Cycles, Metal) |
| `make_video.py` | dashboard video: Cycles 3D view + GPT-6 input/output + serial + feedback |
| `firmware/magnet_arm/magnet_arm.ino` | the real Arduino sketch (same protocol) |
| `conveyor_button.py` | local RUN/STOP web panel for the conveyor servo (holds the serial port, auto-reconnects) |
| `docs/servo_wiring.html` | colour wiring guide for the bench: orange→`~6`, red→`5V`, black→`GND`, power-up order |
| `build_assets.py` | hex mesh, MDF/PCB/breadboard textures, ArUco marker PNGs |

## Real Uno on the bench (19 Sep 2026)

```bash
arduino-cli compile --fqbn arduino:avr:uno firmware/magnet_arm && arduino-cli upload -p /dev/cu.usbmodem* --fqbn arduino:avr:uno firmware/magnet_arm
../.venv/bin/python conveyor_button.py      # RUN / STOP page at http://127.0.0.1:8765
open docs/servo_wiring.html                 # which wire goes where
```

Rules learned the hard way (details in the hackspain `LEARNINGS.md`): servo supply on first and off last,
or the Uno freezes and refuses to flash; unplug all servo leads before flashing if in doubt; at speed 0 the
firmware detaches the belt servo instead of writing 90, because a continuous servo's dead centre is never
exactly 90; a lone micro servo can run from the Uno's `5V` pin, the arm servos cannot.

## Using a real iPhone camera

`camera.RealCamera()` opens the iPhone through Continuity Camera / AVFoundation and picks it **by
name**, not by index. Plug the phone in over USB-C (or same Apple ID + Wi-Fi), unlocked; nothing to
install on the phone. Verified 19 Sep 2026: 1920×1080 frames at 30/60 fps, also 1920×1440.

Gotcha: OpenCV's AVFoundation backend lists external / Continuity cameras first and the built-in
camera after. That is the reverse of `ffmpeg -f avfoundation -list_devices true -i ""` and of
`system_profiler`. On this MacBook the phone is OpenCV index 0 and ffmpeg index 1; hard-coding
`index=1` in OpenCV silently gives you the Mac's own camera. `../demos/avf_cameras.swift` (compiled
on first use) prints cameras in OpenCV order; `../demos/iphone_camera.py --list | --probe` shows the
mapping and saves a thumbnail per index.

macOS asks for camera permission the first time; run from Terminal.app so the prompt appears.
Print the four markers (`assets/aruco_*.png`, 35 mm) and glue them at the positions in
`scene_def.MARKERS`; calibration then needs no other setup. Real-camera mode is not yet wired into
`run_demo.py` (it still renders phone frames); `RealCamera.grab()` is the drop-in for `phone.grab`.

## Layout and waves

The pick sector of the 7+7 cm arm is ~57 cm², so only ~5 parts fit with the 2.6 cm gaps a Ø20 magnet needs.
`run_demo.py` places `BuildConfig.wave_size` parts at a time (guaranteed spacing, keep-out zones around the
containers, prints `layout: n parts, closest pair x cm`) and parks the rest. When GPT-6's `done` is accepted and
parts remain, the operator "puts the next batch on the card": the agent gets a tool result saying so, resets its
inventory and continues. Conveyor builds feed by belt instead. Never trust a layout you did not print.
