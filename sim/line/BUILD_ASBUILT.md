# BUILD_ASBUILT — the cartón pluma chute (owner: build)

**Status: DESIGN VALUES. The chute is not built yet (19 Sep 2026, 13:40).** Everything below is what the
cutting plan specifies; the "as built" section at the bottom is a template to fill with measurements once the
chute exists. Until then, use these numbers in `line/config.json` → `timing` and `gate` (they already match).

Plans (private artifacts, Jaume's account):
- Cutting and assembly plan (foam board 2.5 mm, exact cm): https://claude.ai/artifact/9kBLdCVTmpmwAENr3DgzGA
- Design sheet (side/plan views, timing budget, software loop): https://claude.ai/artifact/Wem19pZ5f6UuVJ8Um2ssTd
- Three.js simulation (mechanisms, slope selector, manual door): https://claude.ai/artifact/1XEu7jny7uvc9n5J6RrENr

## Geometry (design)

| item | value | notes |
| --- | --- | --- |
| chute length along the slant | 40.0 cm | top end = 0, exit = 40 |
| slope | **15° default**; 12 / 18 / 21 with tilt blocks under one end of the base tray | `timing.slope_deg` may take any of these |
| channel inner width | 2.5 cm | walls 0.25 cm foam board, outer 3.0 |
| wall height above the paper | 3.0 cm | measured square to the floor |
| floor top above the base plate | 9.0 cm at the exit, 19.35 cm at the top end | base tray 2.25 cm tall incl. skirt |
| camera zone | **9.0 – 15.0 cm** from the top, centre 12.0 | pencil ticks on both wall tops at 9.0 and 15.0 → 6 cm apart for `camera.px_per_mm` |
| camera | iPhone, lens 20 cm above the paper at the zone centre, square to the paper, aligned with the slope | tripod |
| door | right wall, **30.0 – 36.0 cm** from the top, 6.0 × 3.0 cm | "right" = right hand when standing at the top looking towards the exit |
| hinge | at the **downstream (36.0) end**, axis square to the floor | servo shaft above, toothpick pivot below |
| door swing | ≈ 25° into the channel; free end touches the left wall | mechanical stop = the far wall |
| review cup | on a riser against the right panel under the door, rim 0.5 cm below the floor (10.3 cm above the base) | |
| bowl | under the exit, ≤ 8.5 cm tall | |

## Servo (design)

| item | value |
| --- | --- |
| servo | SG90, on **D9** (firmware "base" channel), 5V + GND from the Uno |
| mounting | hangs shaft-down through a foam-board bracket box glued to the outside of the right panel at the hinge; ears trapped by two keepers + tape |
| horn | single-arm horn glued to a T-tab on the door's top edge **with the servo commanded to 90** |
| FLUSH | `S 90 90 75` (door in line with the wall) |
| OPEN | `S 65 90 75` nominal (25° in). If the horn ends up mirrored, `S 115 90 75`. Calibrate so the free end just touches the left wall |
| swing time | **with the stock firmware ramp (700 °/s², 200 °/s cap) a 25° move takes ≈ 0.35 s** (arduino agent, FakeArduino 0.32 s, analytic 0.38 s) → `gate.settle_ms ≈ 400`, `timing.door_lead_s ≈ 0.40`. With the proposed per-channel speed override (door capped at ~400 °/s) ≈ 0.10–0.12 s → `settle_ms ≈ 120`, `door_lead_s ≈ 0.12` |
| soft stop | 1.0 × 3.0 cm foam-board pad on the left wall inner face at ≈ 29.8–30.8 cm; OPEN is calibrated to touch the pad |
| dwell | 0.5 s after the bean was due at the door, then FLUSH |

## Expected bean kinematics (model, not measured)

Empirical rolling model used in the simulation: net drive 64 cm/s² at 15° with a linear drag giving a terminal
speed of 40 cm/s (`v' = a − 1.6 v`). Bean released from rest at the top.

| slope | a (cm/s²) | t top → zone centre (12 cm) | t zone centre → door start (30 cm) | speed at the zone |
| --- | --- | --- | --- | --- |
| 12° | ≈ 48 | ≈ 0.85 s | ≈ 0.70 s | ≈ 22 cm/s |
| **15°** | 64 | ≈ 0.72 s | **≈ 0.55 s** | ≈ 27 cm/s |
| 18° | ≈ 80 | ≈ 0.64 s | ≈ 0.47 s | ≈ 31 cm/s |
| 21° | ≈ 96 | ≈ 0.58 s | ≈ 0.42 s | ≈ 34 cm/s |

Photo → door-command budget at 15°: ≈ 0.45 s with the fast-door firmware option (0.55 − 0.10), only ≈ 0.15 s with the stock ramp (0.55 − 0.38). At 18° with the stock ramp the budget is ≈ 0.09 s, i.e. not workable; the fast-door option or 12° is required. The chute is hand-fed one bean
every ~2 s; never two beans in the zone at once.

## Bench fact 19 Sep 14:33 (from the integrator, Jaume watching)

Cause found 14:38 (arduino): the servo's signal wire is on **D6** because this morning's wiring guide put it there when the servo
was the conveyor; nothing is wired to D9–D11, so `S` moves nothing. The board is fine. Workaround landed in `line/gate.py`: the
door/tray servo stays on **D6** and is driven as `gate.channel = "belt"`: `sp = round((deg − 90) / 0.9)`, clamped −100..100, **never `C 0`** (the firmware detaches
the servo at 0 and it goes limp; use 1). All angles in these sheets stay angles; only the wire and the command change:
LEVEL/FLUSH 90 → `C 1`, GOOD 45 → `C -50`, REJECT 135 → `C 50`, OPEN (door) 65 → `C -28`. There is **no ramp** on the belt channel: the SG90 moves at its own speed (≈ 0.1 s for 25°, ≈ 0.15 s for 45°), so the swing-door
timing problem disappears on D6 for free, but the door lands hard, so the soft-stop pad matters more. To get the ramped `S` path
back later: move the orange wire three holes towards the USB end (~6 → ~9) and set `gate.channel = "base"`. Bench caveat:
`conveyor_button.py` STOP and its exit handler send `C 0`, which detaches the door servo (goes limp) — do not press STOP there
while the door/tray is in use.

## Bench state 19 Sep 14:55 (seen on the panel snapshot, not measured by hand)

- A white foam-board **U-chute is built**: long channel, low walls (looks ≈ 2 cm, not the 3 cm of the sheet), inner width read by
  the camera agent as ≈ 2.5 cm (105–110 px). It rests on a cardboard box at the top end; the slope is therefore whatever that
  box gives and is not yet measured.
- The **SG90 carries a flat white paddle** glued to a single horn arm (≈ 4–5 cm long from the shaft). It is lying loose beside
  the chute, wired (orange/red/brown) to the Uno on **D6**. This is the "1 servo + barrera" of the original concept: the paddle is
  the door and the servo shaft is the hinge, so no wall cut-out, no toothpick.
- Camera: phone ≈ 28 cm above the paper (camera agent, from the scale), `camera.px_per_mm = 4.3`, proposed
  `camera.zone = [992, 395, 1274, 505]` (floor only, walls and shadows excluded). Paper median gray 152, threshold adapts.
- Servo commands go through D6 (`gate.channel = "belt"`, `C` mapping above); a config re-save from the panel can flip this back.

Open as-built numbers (need Jaume's tape measure): chute length along the slant, wall height, box height under the top end
(→ slope), paddle length, where the servo will be fixed (outside the right wall near the exit is the intended place), zone
position along the chute, and 10 stopwatch transit times.

### Paddle-gate mounting (recommended for what is on the bench)
- Fix the servo body to the OUTSIDE of the right wall with its shaft vertical (square to the floor), shaft 0.3 cm past the
  exit edge, horn at wall-top height, so the paddle sweeps across the exit opening and never needs a slot. Glue a foam-board
  block (2.5 × 1.5 × 2.3 cm) between servo and wall for the standoff; strap with tape around block and servo.
- CLOSED = paddle across the channel at the exit (bean stops against it, photo, decide); GOOD = paddle swings 90° downstream,
  bean rolls into the bowl; SUSPECT = paddle stays closed and swings 90° UPSTREAM-in? — no: with one paddle at the exit only a
  stop-and-release is possible, so the second bin needs either the tilting tray or the side kicker. If Jaume wants two bins with
  this paddle alone, mount it as the swing DOOR instead: shaft at 36 cm from the top on the right wall, paddle 6 cm long lying
  flush along the inside of the wall (there is no wall material to remove if the paddle sits in a 6 cm gap of the wall), swing
  25° in for a suspect while the bean rolls (timing per the table; D6 is unramped so settle 150 ms).

## Bench fact 19 Sep 15:00 (integrator): the paddle servo on D6 is CONTINUOUS-ROTATION

It spun half a turn in 0.4 s at `C 30` and creeps at `C 1`. It has no position control, so every angle in these sheets
(FLUSH/OPEN, LEVEL/45°) is meaningless for it. Two ways forward, in order of preference:

1. **Put a positional servo on the door** (SG90 / MG90S: the shopping sheet lists 2× MG90S). Then everything above applies
   unchanged (`S` on D9, or `C`-mapping on D6 if it stays there). The continuous servo goes back to being the conveyor.
2. **Keep the continuous servo and add two mechanical end stops** (paddle door only, not the tray): a foam-board block on
   each side of the paddle's swing so it stalls against "CLOSED" and "OPEN". Drive it with timed pulses a little longer
   than the swing needs (e.g. `C 40` for 250 ms, then `C 0`); the stops, not the timing, define the two positions, so it is
   repeatable. A stalled micro servo at 5 V draws ~0.6–0.8 A for the extra ~100 ms, which the USB 5 V rail tolerates for
   short pulses; the paddle must be light (foam board) and the stops padded (a second foam layer). This is the integrator's
   current `panel.DoorOnD6` timed-pulse approach plus stops; without stops the door position will drift by a few degrees per
   cycle and eventually miss the channel.
   Stop geometry for the exit paddle: CLOSED stop = a 1.0 × 3.0 block glued on the floor outside the far wall line so the
   paddle tip rests on it square across the channel; OPEN stop = a block on the outside of the right wall 90° downstream.
   The tilting tray cannot be done with a continuous servo (no way to hold LEVEL).

## As built (fill in when the chute exists)

```
date / who:
slope block used:                      (none / W1 exit / W1 top / W2 top)   → measured slope: ____ °
zone ticks measured from the top:      ____ cm and ____ cm
door opening measured from the top:    ____ cm to ____ cm ; hinge side: right / left
camera lens height over the paper:     ____ cm ; ROI px (x0,y0,x1,y1): ____ ; px_per_mm: ____
FLUSH angle that is truly flush:       S ___ 90 75
OPEN angle, free end touching:         S ___ 90 75
transit top→zone (10 stopwatch runs):  ____ ____ ____ ____ ____ ____ ____ ____ ____ ____  → mean ____ s
transit zone→door (10 runs):           ____ ____ ____ ____ ____ ____ ____ ____ ____ ____  → mean ____ s
beans that stalled on the paper:       __ / 10 at ___° ; beans that jumped the door: __ / 10
```
