# THEKER robot sorter — simulation learnings (living document)

Updated automatically with the code. Newest at the top of each section.

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
  - Cost/step ≈ $0.06–0.08 with two 1280×960 images per step at medium effort.

## Cameras
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
- Oracle (ground-truth) planner scores: taras_v1 6/6, 5/6, 5/6; theker_v1 7/9 (one part carried along,
  one nudged off the card). Parts closer than ~3 cm come up together: separation matters (sheet agrees).

## Hardware facts baked into the sim (from the shopping sheet)
- MG90S (0.18 N·m, 550°/s), MG946R (1.2 N·m), links 70 mm pivot-to-pivot from 100 mm 10×10 wood strips,
  lazy-susan base, magnet Ø20 mm ~25 g on a passive hanger, relay-switched, flyback diode.
- Parts: M3×20 and M4×20 with nuts and washers (+ whatever comes from home); one brass distractor.
- Elbow servo budget: 63 g at 70 mm with 3× margin → keep the magnet ≤ Ø20 mm.

## Open issues
- Verifier still confuses look-alike neighbours occasionally (two cameras disagree → we say so to GPT-6).
- Photoreal Cycles video renders at 13–30 s/frame while the GPU is shared with live sims; render when idle.
- Real iPhone camera path (`camera.RealCamera`) is wired but untested (needs camera permission in Terminal).
