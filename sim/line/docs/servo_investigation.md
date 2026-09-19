# Inconsistent SG90 movement — investigation report (arduino agent, 19 Sep 2026)

Brief from Jaume (verbatim in `INTEGRATOR.md`, 16:31 relay). Scope: the door servo on the coffee line. Everything below
separates **what was verified in software**, **what was observed on the bench**, and **what still needs a physical test**.

## 1. What we are actually controlling

| item | value | how verified |
| --- | --- | --- |
| board | Arduino Uno R3, ATmega328P, 16 MHz | `arduino-cli board list`, flashed today |
| library | Arduino `Servo` 1.3.0 (`~/Documents/Arduino/libraries/Servo`), Timer1, 20 ms refresh | `Servo.h`: `MIN_PULSE_WIDTH 544`, `MAX 2400`, `DEFAULT 1500`, `REFRESH_INTERVAL 20000` |
| signal pin | **D6** (`PIN_BELT`, the conveyor output). D9/D10/D11 carry the arm channels and have **nothing wired** — not a fault | wiring guide `docs/servo_wiring.html`; sketch `magnet_arm.ino` |
| power | Uno `5V` pin, i.e. USB bus power, shared with the MCU | wiring guide; see limitations |
| command path | `panel.DoorOnD6` → `HttpPanelLink` (HTTP) → `conveyor_button.py` (HTTP server + pyserial, one lock) → USB CDC → sketch | code read; one-hop `?` round trip measured n=40: p50 4.1 ms, p90 4.2 ms, **max 37.5 ms** |
| "ms" in our config | the **duration** the host kept `C <speed>` active before `C 0` — never a pulse width | `DoorOnD6._pulse` |
| ms/µs conversions | none wrong: pulse widths are computed inside `Servo.write()`; durations are `time.sleep(ms/1000)` on the host and `millis()` on the MCU | code read |

**Positional or continuous?** Not inferred from the label. Test 1 (17:00, paddle mounted, panel snapshots at 0 / 0.7 / 1.6 / 2.5 s /
after `C 0` / +2 s): the horn turned between 0 and 1.6 s, then **held the same angle at 1.6 s, 2.5 s and afterwards while `C 30`
(1750 µs on the old firmware) was still active**. A continuous-rotation servo would have kept turning; this is positional behaviour.
Caveat: the paddle ended near the servo lead, so a stall cannot be excluded from pictures alone. Test 2 was invalidated (the
mount was being rebuilt during it). **Decisive check for the bench (3 s, bare horn):** send `C 30` and watch — turns ~30° and
stops firmly = positional; keeps turning = continuous. The integrator has since switched its adapter to positional semantics.

## 2. Confirmed code defects (fixed in firmware on disk, compiled 9540 bytes / 29 %, **not yet flashed**)

1. **Wrong neutral.** `belt.write(90 + sp*90/100)`: `Servo.write(90)` sends **1472 µs**, not 1500. On a continuous servo that is a
   28 µs offset → the creep seen this morning at "90" and at `C 1`; `+sp` and `−sp` were asymmetric about the true neutral.
   *Fix:* D6 speed mode now uses `writeMicroseconds(neutral + sp·5)`; `neutral` defaults to 1500 and is set by the new
   `N <us>` command (a documented servo constant to calibrate, not an offset that hides drift). `servo_repeat calibrate-neutral`
   finds it.
2. **Detach on stop.** `C 0` detached the pin. A **positional** servo with no pulses goes limp; the next move starts from an unknown
   angle → "same command, different movement". *Fix:* new `D <deg>` command — attach if needed, ramp with the same trapezoidal
   profile as `S`, **hold, never detach**. Any `C`/`T` afterwards returns D6 to speed mode. `Gate` channel `d6` uses it.
3. **Host-timed motion.** Pulse duration = HTTP + USB + `time.sleep` on the laptop: up to ~40 ms jitter per pulse plus 20 ms
   servo-frame quantisation on start and stop. *Fix:* `T <speed> <ms>` — the MCU times the pulse with `millis()`, then stops.
   Stop is now **neutral for 2 frames (40 ms), then detach** — deterministic for a continuous servo instead of "coast when the
   signal vanishes".
4. **Integer truncation.** `sp*90/100` gave 1° steps and asymmetry near 0. Removed by the µs mapping (5 µs per %).
5. **No travel limits.** `S` clamped to 0–180 = 544–2400 µs, endpoints that bind many SG90s. *Fix:* `L <ch> <min> <max>`
   per-channel limits (ch 3 = the D door). Defaults 0–180 so nothing changes until configured; `Gate` sends `L 3 40 140`
   from `gate.door_limits_deg` for the door.
6. **Concurrent commands.** Two panels poll `?` once a second through the same serial lock; each poll can delay a door command by
   ~4 ms. Minor; the MCU-timed `T`/`D` paths make it irrelevant.

Protocol summary after the change (all additive; `S/M/H/?` unchanged, `C` now speed-only in µs):

```
S <b> <s> <e>        arm angles (ramped)            D <deg>            positional door on D6 (ramped, held)
C <speed>            D6 speed, neutral ± speed·5 µs; 0 = neutral 40 ms then detach
T <speed> <ms>       MCU-timed spin pulse           N <us>             D6 neutral (1300–1700, default 1500)
R <ch> <vmax> <acc>  ramp per channel (3 = door)    L <ch> <min> <max> travel limits (3 = door)
```

**Breaking note:** a positional servo driven through `C` (the integrator's current adapter, `deg = 90 + 0.9·speed`) will land on
different angles after the flash because `C` no longer means degrees. Switch the adapter to `D <deg>` in the same step.

## 3. Repeatability test — `line/tools/servo_repeat.py`

```
.venv/bin/python -m line.tools.servo_repeat positional --channel d6 --a 65 --b 90 --cycles 10 --csv line/logs/servo_before.csv
.venv/bin/python -m line.tools.servo_repeat continuous --speed 12 --ms 300 --cycles 10
.venv/bin/python -m line.tools.servo_repeat calibrate-neutral --lo 1440 --hi 1560
```

Positional: phase 1 arrives at B always from A (same side); phase 2 arrives at B from C = B + 0.5·(B−A) (other side) — the
difference between the two families of arrivals is **backlash + deadband**. Each row logs requested angle, the pulse width the
library will send, approach direction, the ramp time predicted from the firmware profile (25°: 0.378 s stock, 0.113 s with
`R 3 400 8000`), the settle interval, and the reply. `--fake` runs the whole plan against `FakeArduino` (this is what the unit
tests do); `--dry` prints the plan.

**These logs are commands, not measurements.** To verify the two things they cannot show:
* shaft angle → the camera agent's `line/tools/paddle_angle.py` (two dark marks on the paddle, ±1°, plateau table gives
  repeatability and settling), or a 240 fps phone video of a mark against a printed protractor;
* electrical pulse width → logic analyser / oscilloscope on D6 (expect 20.0 ms period; width as logged ±2 µs on Timer1), or a
  second Arduino running `pulseIn()` on the signal line.

## 4. Before / after procedure

1. **Before (old firmware, today):** start `paddle_angle.py --seconds 60 --csv line/logs/paddle_before.csv`; from the panel do
   CLOSED, OPEN, CLOSED, OPEN, CLOSED (≥ 3 s each), then approach CLOSED once from the other side. Note the plateau spread.
2. **Flash (Jaume):** servo leads out, servo supply off, USB only → `arduino-cli upload -p /dev/cu.usbmodem* --fqbn arduino:avr:uno
   magnet_sorter/firmware/magnet_arm`, verify with `avrdude -Uflash:v:`, reconnect, `?` from the panel.
3. **Configure:** `gate.channel = d6` (positional) — `Gate` then sends `N 1500`, `L 3 40 140` and, if `ramp_override`, `R 3 400 8000`
   at start; adapter switched to `D`. For a continuous servo instead: `gate.channel = belt_spin`, calibrate `N`, tune
   `spin_speed/spin_open_ms`.
4. **After:** repeat step 1 → `paddle_after.csv`; run `servo_repeat positional --channel d6` while it records.
   Success = plateau spread ≤ 2° same-side and the other-side offset stable (that offset *is* the backlash; do not hide it with
   an angle correction).

## 5. Remaining limitations (not software, or not fixable in software)

* **Power.** The servo runs from the Uno's USB 5 V. A stall against a stop or a fast start draws 0.5–0.7 A; the rail dips and the
  servo's speed/torque varies with it, and a deep dip resets the Uno (look for a spurious `magnet_arm ready` in the panel log).
  Remedy: the separate 5 V supply with common GND (wiring guide), or at least 470–1000 µF across the servo's power.
* **Mechanical load / stops.** Pressing the paddle into a foam stop is a stall by design; position then depends on the stop, not
  the command. Fine for a two-position door if the stops are rigid.
* **Backlash and deadband.** SG90-class gear trains show 1–3° of backlash; the controller's deadband is ~5 µs (≈ 0.5°). Both show
  up as the same-side vs other-side difference in the test. Approach the important position from one side.
* **Continuous servo (if that is what it is).** Equal pulses give equal travel only while load, supply and neutral are constant;
  without position feedback (encoder or the camera) precise angles are not guaranteed — the end stops are what defines them.
* **Possible defective unit.** If the same-side spread on the *after* test exceeds ~3° with a steady supply and no load, swap the servo.

## What was verified where

| | verified |
| --- | --- |
| software | firmware compiles (9540 B); `FakeArduino` emulates `C/T/D/N/L/R` incl. neutral-then-detach and the door ramp; `Gate` channels `d6` / `belt_spin` / `belt` / `base`; 53 tests green (`test_arduino_link`, `test_gate`, `test_servo_repeat`, `test_config_merge`, `test_contracts`); one-hop latency measured |
| bench | positional behaviour observed once (test 1, with the stall caveat); nothing flashed; no pulse widths or shaft angles measured yet |
| still needed | the 3-s bare-horn check; the flash; `paddle_before/after.csv`; a scope or `pulseIn()` reading of D6 if the after-test is still inconsistent |
