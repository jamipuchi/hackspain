# THEKER robot sorter — simulation learnings (living document)

Updated automatically with the code. Newest at the top of each section.

## Track 2 — coffee bean optical sorter (`sim/coffee_sorter`)
Hand-sorting green coffee is still done by people; the industrial answer is a belt/chute optical sorter with
air ejectors at thousands of beans per second, not an arm. We simulate that plant in MuJoCo end to end.
- **Conveyor idiom.** A static belt geom makes beans roll like wheels (friction + forward push = torque) at
  ~1000 rad/s and hop. Correct: belt = body on a slide joint, `qpos` reset to 0 and `qvel` set to the belt
  speed every step. Friction alone then transports the beans; no fake forces.
- **Tiny ellipsoids are bad colliders.** Ellipsoid–ellipsoid goes through the generic convex collider and
  catapults 0.2 g beans. Use an analytic capsule collision geom (same length, same resting height) and keep
  the ellipsoid visual-only. Boxes/capsules for stones/sticks are analytic already.
- **Light bodies + `implicitfast` = gyroscopic blow-up.** A bean landing on a corner picks up hundreds of
  rad/s in one 2 ms step and the explicit ω×Iω term diverges (NaN → MuJoCo silently resets the whole state,
  which looks like "all beans accepted"). Fix: per-body rotational damping `I/τ` (τ = 1.5 ms) and armature
  `2I` set at spawn; `implicit` integrator also works but is 10× slower with 1200 bodies.
- **Runtime mass/size edits** need `dof_invweight0`/`body_invweight0` updated too, or contact impedance is
  scaled for the compiled mass.
- **Rendering cost is per geom, not per pixel**: 46 ms/frame with 1200 bodies at any resolution. Move
  off-strip geoms to a hidden geom group before each inspection render → 5.6 ms at 2080×192.
- **The renderer is one step behind `qpos`**: it draws the kinematics of the last completed step. Timestamp
  frames as `t − dt` and label training blobs from `xpos`, not `qpos` (6 mm error at 3 m/s otherwise).
- Spawn beans in an overlap-free spot (a vibratory feeder meters one layer); overlapping spawns at 2000/s
  were the other source of catapulted beans.
- Vectorise per-blob statistics over foreground pixels only; `np.bincount` over the full 400k-pixel strip
  is ~45 ms/frame and made classifier data collection take >10 min (still to do).

## What we are building
A 3-servo wooden arm with an electromagnet that sorts screws, nuts and washers into three lids, controlled
by GPT-6 (`gpt-6-astra`) from one phone camera. Everything in `sim/magnet_sorter` is the same software that
will run on the real Arduino: the Python controller only speaks the serial protocol implemented by
`firmware/magnet_arm/magnet_arm.ino`.

Builds (`run_demo.py --build …`): `theker_v1` = the final shopping sheet (v4, 19 Sep); `taras_v1` = the
docx proposal; `hobby_v1` = first realistic build; `taras_conveyor`, `taras_kitting`, `taras_grading` =
extra use cases for the demo.

## GPT-6 as the brain — what works
- **Tool-calling agent, not batch programs.** One tool per step, a fresh photo after each motion,
  a `note_parts` inventory echoed back every step, mandatory final-photo check before `done`. Batch
  programs (one plan per photo) hid failures until the end.
- **Metric grid on the photo.** Pixel→table homography from four ArUco markers (0.5 mm error) lets the
  model read positions in cm directly and command `pick_at(x_cm, y_cm)`; it does not need intrinsics.
- **Zoom mosaic.** Small parts (M3/M4) are unidentifiable in a 1280×960 frame; a 3× crop of each noted
  part from every camera returned after `note_parts` is what made type identification work.
- **Guardrails are needed.** Without them the model loops on `take_photo`/`home` (25 photos for one
  pick). Rule: max 3 photos in a row, max 5 non-productive actions, then it must pick/place/done.
- **Sliding-window context** (last 40 log lines + inventory + latest photos) instead of chaining
  `previous_response_id`: cost stays ~4k tokens/step (≈$0.04). Chaining grew to $2.5 for 16 steps.
- Reasoning effort `medium`; `low` dithered more.
- Results so far:
  - taras_v1, 2 cameras, seed 4: **4/6** correct in 22 steps, brass distractor correctly left, $1.27.
    Misses: a small M5 nut never noticed; an M6 washer read as a nut.
  - theker_v1 (final sheet: M3/M4 parts, lids, blue card), 1 top-down phone, seed 4: **4/9** correct in 38
    steps, $2.73. Dominant failure = **co-picks**: with parts ~3 cm apart the Ø20 magnet lifts the neighbour
    too (nut into the screws lid, washer + nut into unknown). GPT-6 noticed the mismatch in the final photo
    and reported the exceptions honestly instead of claiming success. Fixes queued: parts spaced ≥ 3.8 cm
    (the sheet's "gaps between parts"), magnet lateral reach radius + 2 mm.
  - theker_v1, **2 phones** (top-down A + side B), same seed: **7/9** correct in 36 steps, $2.91. Two cameras
    beat one clearly (7/9 vs 4/9): the side view disambiguates parts the top-down view hides under the arm or
    behind a lid rim. Remaining misses were both co-picks (a screw carried into the washers lid, a nut into the
    screws lid). GPT-6 tried to recover the mis-sorted nut from the lid but the lids sit outside the pick
    sector (r ≤ 10.8 cm), so it reported the exception for manual correction — correct behaviour.
  - taras_kitting (1 screw + 1 nut + 1 washer per cup, 2 cameras, seed 4): **1/3** kits complete in 50 steps,
    $4.08 (budget hit). Kit A correct; kit B ended with 2 nuts + 2 washers and no screw. Four of eight picks were
    reported by the verifier as "spot unchanged" — GPT-6 aimed 0.5–1 cm off on the dense 10-part card and the magnet
    lifted a neighbour instead (the neighbour then landed in the kit, hence the duplicates). GPT-6 did NOT claim
    success: its `done` summary listed exactly which kits were incomplete and that kit B's contents conflicted
    with its pickup log. Counting-by-feedback is the hard part of kitting: a wrong pick corrupts the count.
    Next: verify by looking INTO the cup after each place (second camera), and reject the drop if the cup shows
    a duplicate; spread parts ≥ 4 cm.
  - taras_grading (screws by length: short / long, rusty → reject; 2 cameras, seed 4, dense layout): **4/9** in 50
    steps, $3.63. Length grading itself worked (the two screws it lifted cleanly went to the right cup; a black M4×25
    correctly read as long; rusty correctly rejected). Same layout bug as kitting: parts 0.6–2 cm apart → 5 failed
    picks and a short screw carried into the long cup. GPT-6's final report again listed the exceptions instead of
    claiming success. Re-run pending with the fixed layout + waves.
  - **taras_kitting with the fixed layout + waves (2 cameras, seed 4): 3/3 kits, 10/10 picks clean, 46 steps.**
    Waves of 5 / 4 / 1 parts; GPT-6 counted what each cup already had across batches ("kit B needs only a screw"),
    asked for the next batch itself, and sent the leftover washer to the unknown tray. Same brain, same physics as
    the 1/3 run: the whole difference was part spacing. This is the demo-day recipe: a handful of parts at a time.
  - Cost/step ≈ $0.06–0.08 with two 1280×960 images per step at medium effort.

## Layout bug found 19 Sep 01:50 — the real cause of the co-picks
- The pick sector of a 7+7 cm arm is only ~57 cm² (r 5.5–10.8 cm, ±38°). Ten parts can never sit 3.8 cm apart
  there, and the old `scatter()` silently gave up: in the kitting/theker runs the closest pairs were **0.6–1.9 cm**,
  not 3.8. Every "co-pick" in the results above was this, not the magnet model. Worse, the cups' rims reach 1 cm
  into the sector, so 3 parts spawned inside kit B's footprint and were counted as already in the cup.
- Fix (in `run_demo.py`): guaranteed spacing (3.8 cm, relaxing to a 2.8 cm floor = magnet reach), keep-out circles
  around every container, and **waves**: `BuildConfig.wave_size` (6) parts on the card at once; when GPT-6's `done` is
  accepted, the operator puts the next batch on the card and the agent continues (the tool result says so, the
  inventory is reset). This is also how the physical demo will run — a human refills the card.
- Result of the fix (oracle planner, taras_kitting, seed 4): **3/3 kits** with waves of 3–4 parts vs 1/3 before,
  zero failed picks, zero co-picks. Tuned to a 2.6 cm floor and 1.2 cm keep-out margin → ~5 parts per wave.
- Lesson: always print the achieved layout (`layout: n parts, closest pair x cm`) and check it before blaming
  perception or physics. Mechanics ceilings above (7/9 etc.) need re-measuring with the new layout.

## Cameras
- **Real iPhone over USB-C works, zero phone-side setup (19 Sep 12:45).** macOS Continuity Camera exposes a
  plugged-in, unlocked iPhone as a normal AVFoundation device ("Jaume's iPhone Camera" plus a "Desk View"
  variant and the phone microphone). 640×480 … 1920×1440 at 30 or 60 fps. `ffmpeg -f avfoundation -i "1:none"`
  and `cv2.VideoCapture(i, cv2.CAP_AVFOUNDATION)` both pull frames; `sim/demos/iphone_camera.py` is the viewer.
- **OpenCV camera indices are not ffmpeg's.** OpenCV's AVFoundation backend lists external / Continuity
  cameras first, built-in after; ffmpeg and `system_profiler` list built-in first. On the MacBook the phone
  is OpenCV 0 / ffmpeg 1. The first live viewer opened index 1 and showed the Mac camera while a snapshot
  "looked plausible"; a thumbnail-per-index probe caught it. Rule: never hard-code a camera index, resolve
  by name (`sim/demos/avf_cameras.swift`, a 10-line Swift DiscoverySession helper, compiled on first use;
  `swift file.swift` interpreted takes 15 s, the compiled binary is instant) and keep a `--probe` that saves
  one thumbnail per index. `camera.RealCamera()` now does this; its old default `index=1` was the Mac camera.
- Asking OpenCV for 1280×960 on the phone returns 1920×1440 (nearest 4:3 mode); downscale yourself if the
  GPT-6 image budget matters.
- ffmpeg needs an explicit supported pixel format for the phone (`uyvy422`, `yuyv422`, `nv12`, `0rgb`,
  `bgr0`); the default `yuv420p` request is refused. `ffprobe -f avfoundation -i "1:none"` lists modes.
- Continuity Camera drops when the phone locks or sleeps; the device disappears from every listing. Wake it
  and reopen. Camera permission must be granted to the terminal app once.
- One or two oblique phones ≤ 50 cm work; two views help when the arm hides a spot. The final sheet
  puts one phone on a gooseneck 25–30 cm straight above the tray: `theker_v1` camera A.
- Tall cups between the camera and the tray hide parts: use shallow lids (the sheet agrees).
- Verification "did the part leave its spot" = exact-spot pixel change per camera. Template search
  was worse: a look-alike neighbour 1 cm away matched.

## Physics / mechanics (MuJoCo) — hard-won
- Hanging magnet: ≈2× critical damping (0.004 N·m·s/rad for 40 g on 4 cm). Over-damping made the magnet
  creep 1–2 cm off target for seconds; the arm then grabbed the neighbour.
- Magnet holding must be a weld on contact (25 N vs 3 g parts); force-only holding launched screws.
  The pull-in stays a force field capped at 20 m/s² with a terminal-speed fade. Magnet geoms must not
  collide (a colliding face crushes parts that stood up).
- Energise only after settling at pick height; lift slowly first.
- Travel height must clear container rims plus a 2 cm screw hanging under the magnet (the screws that
  "vanished" were clipping cup rims and stalling the base servo).
- Position-actuator velocity damping kv = 0.025·kp; 0.08·kp made the MG90S base lag 12° for 0.5 s.
- Wrist mass must sit on the magnet geom, or the hanger has no gravity torque.
- Wrist hinge range ±1.55 rad (±1.2 was hit at steep poses).
- Servo firmware: trapezoidal profile (200°/s cruise, 700°/s² accel) for both sim and `.ino`.
- The oracle planner now understands kitting (assigns each loose part to the first kit missing that part,
  counting what is already in each cup), so every use case has a mechanics ceiling to compare GPT-6 against.
- `mjpython` + viewer can segfault (exit 139) when Blender Cycles is rendering at the same time; run the
  live sims with `--phone mujoco` while a Cycles video renders, or wait for the render.
- Oracle (ground-truth) planner scores: taras_v1 6/6, 5/6, 5/6; theker_v1 7/9 (one part carried along,
  one nudged off the card). Parts closer than ~3 cm come up together: separation matters (sheet agrees).

## Hardware facts baked into the sim (from the shopping sheet)
- MG90S (0.18 N·m, 550°/s), MG946R (1.2 N·m), links 70 mm pivot-to-pivot from 100 mm 10×10 wood strips,
  lazy-susan base, magnet Ø20 mm ~25 g on a passive hanger, relay-switched, flyback diode.
- Parts: M3×20 and M4×20 with nuts and washers (+ whatever comes from home); one brass distractor.
- Elbow servo budget: 63 g at 70 mm with 3× margin → keep the magnet ≤ Ø20 mm.

## Real Arduino bring-up — 19 Sep 2026 (first contact with hardware)
Bench: Arduino Uno R3 on USB, one blue 9 g micro servo (SG90/FS90R form factor) on a 3-pin lead
(brown/red/orange) joined to three jumpers (black/red/orange). No breadboard, no external supply yet.

- **"The motor runs permanently on power".** The orange signal jumper was in `~10`, the shoulder pin. The
  sketch holds the shoulder at its home angle (125°) forever, so a continuous-rotation servo there never
  stops, and `C 0` (which talks to `~6`) did nothing. A photo of the bench settled in 10 s what 20 min of
  serial poking did not: **ask for a photo first.**
- **90 is not "stop" for a continuous servo.** Each DS04-NFC/FS90R has its own dead centre and a trim pot.
  Fix in firmware, not by trimming: at speed 0 we now `detach()` the belt servo and drive `D6` LOW (no
  pulses → the servo stands still). Also applied at boot, so nothing moves on power-up until a `C` command.
- **Servos plugged in with their 5 V rail off freeze the Uno.** Signal pins back-feed the servo electronics
  through the 328P I/O pins; the USB bridge still enumerates but the MCU stops answering and avrdude
  reports `not in sync: resp=0x00` on every attempt. Rule: **servo supply on first, off last**; unplug all
  servo leads before flashing if in doubt. Once everything was unplugged the board answered and flashed
  first try (`arduino-cli upload --fqbn arduino:avr:uno`, verify with `avrdude -Uflash:v:`).
- A single micro servo runs fine from the Uno's `5V` pin over USB. The 5 V 5 A supply is only needed once
  the MG946R/MG90S arm servos join.
- Opening the serial port resets the Uno (DTR). Anything that talks to it must hold the port open and wait
  ~2.5 s for `magnet_arm ready` before sending. Re-plugging USB while a program holds the port gives
  `Device not configured` (ENXIO) on the next write; reopen the port and retry once (done in
  `conveyor_button.py`).
- New tooling in `sim/magnet_sorter/`: `conveyor_button.py` (local RUN/STOP web panel on
  http://127.0.0.1:8765, holds the port, sends `C 0` on start and exit, auto-reconnects) and
  `docs/servo_wiring.html` (colour wiring guide: orange→`~6`, red→`5V`, black→`GND`, power-up order).
- Still to verify on the bench: does the micro servo stay still at power-up with the new firmware, and is
  it positional (RUN twitches to an angle) or continuous (RUN spins)? The conveyor needs a continuous one.

## Open issues
- Verifier still confuses look-alike neighbours occasionally (two cameras disagree → we say so to GPT-6).
- Photoreal Cycles video renders at 13–30 s/frame while the GPU is shared with live sims; render when idle.
- `camera.RealCamera` is tested standalone against the USB iPhone (frame grab OK) but not yet wired into
  `run_demo.py` in place of the rendered phone; the ArUco calibration on real frames is untested.

## Café chute — swing-gate inspector, design phase (build session, 19 Sep 2026 10:30–13:45)
Hardware track for coffee: one bean at a time down a chute, phone camera, one servo. Plans in `plans/cafe_chute/`.
- **Bring-up of the real Uno from this Mac.** Board was blank; `arduino-cli` (Homebrew) + `arduino:avr` core +
  the `Servo` library (not bundled with the core in arduino-cli) compile and flash `magnet_arm.ino` in one go.
  The genuine Uno enumerates as `/dev/cu.usbmodem2140x`; the suffix changes with the USB socket, so resolve it
  with `arduino-cli board list`, never hard-code it.
- **Wiring mistakes that look right in a photo.** A servo signal jumper in the RX/TX end of the header (pins 0/1)
  kills USB serial; a "5V" jumper one hole off lands on Vin or GND. Check against the header labels, not by position.
- **Rejection mechanisms ranked for a cardboard build, beans fed one at a time:** side kicker at a stop gate (98 %,
  two servos, nothing to time) > diverter flap under the exit (95 %) > swing gate in the wall (93 %, ONE servo,
  no stop, but a 0.45 s photo→command budget) > trapdoor (90 %, cardboard hinges stick) > air puff (80 %, needs
  pump + ms timing). Jaume chose the swing gate for simplicity; the timing budget is the price.
- **Swing-gate kinematics.** Door = the 6 cm wall cut-out hinged at its downstream end; 25° in reaches the far
  wall of a 2.5 cm channel (6·sin25° = 2.54). A rolling bean meets the angled door and slides out through the
  opening it left; no stop needed. Servo axis must be square to the floor (mount on the wall plane, not the table)
  or the door's free end lifts off the sloped floor.
- **Timing budget** (empirical rolling model `v' = a − 1.6 v`, a = 64 cm/s² at 15°, terminal 40 cm/s): released from
  rest, a bean reaches the zone centre (12 cm) at ~0.72 s and the door (30 cm) at ~1.3 s → ~0.55 s zone→door,
  0.45 s for capture + classify + serial after the servo swing. OpenCV rule fits; a cloud vision call does not.
  Slope 12/18/21° gives 0.70/0.47/0.42 s. Friction on paper varies, so the slope must be adjustable.
- **Foam board (2.5 mm) structure that is actually rigid**: make the side walls full-height trapezoid panels that
  reach a skirted base tray, glue the floor on rails (two 40 cm faces, not a 0.25 cm edge), close both ends with
  bulkheads → closed box girder. Adjust slope with a tilt block under one end of the tray rather than props under
  the chute. Servo in a glued box bracket with keepers around its ears, never taped to a 0.25 cm wall top. Door on
  two bearings (servo shaft + toothpick).
- **Simulation bugs worth remembering** (three.js chute sim): (1) a generic "gate open ⇒ leading bean released"
  rule released the *next* bean while the gate was still closing, so it was never inspected and the line stalled;
  release must be tied to the inspected bean. (2) a sequence step that hands the bean off inside `on()` can finish
  one frame before the hand-off condition; do the hand-off in `end()`. (3) `renderer.setSize(w,h,false)` with
  devicePixelRatio 2 shows only a quarter of the scene; let three set the CSS size.
- **Multi-session coordination.** Several Claude sessions share this Mac; port 8765 was taken by the bench
  session's conveyor panel while I tried to serve a preview there. Check `lsof -iTCP:<port>` before binding, and
  use `~/robotics/INTEGRATOR.md` (roles: arduino, camera, coffee-sim, build, integrator) for hand-offs.
