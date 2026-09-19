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
| swing time | SG90 ≈ 0.1 s for 25° incl. the firmware's trapezoidal ramp (`gate.settle_ms = 120` is fine) |
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

Photo → door-command budget at 15° ≈ 0.45 s (0.55 s minus the servo swing). The chute is hand-fed one bean
every ~2 s; never two beans in the zone at once.

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
