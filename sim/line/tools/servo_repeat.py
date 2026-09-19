#!/usr/bin/env python
"""
servo_repeat.py — repeatability test for the door servo (owner: arduino agent). Logs COMMANDS, not measured position.

    cd ~/robotics
    .venv/bin/python -m line.tools.servo_repeat positional --a 65 --b 90 --cycles 10          # door on D9 (S) or D6 (D)
    .venv/bin/python -m line.tools.servo_repeat positional --channel d6 --a 65 --b 90
    .venv/bin/python -m line.tools.servo_repeat continuous --speed 12 --ms 300 --cycles 10   # T pulses, both directions
    .venv/bin/python -m line.tools.servo_repeat calibrate-neutral --lo 1440 --hi 1560          # step N and watch for "still"
    add --fake to run against FakeArduino (no hardware), --dry to print without sending, --csv out.csv to save the log.

Positional mode (same-direction approach, then opposite-direction approach to expose backlash):
    phase 1:  A → B → A → B …        every arrival at B comes from A (same side)
    phase 2:  A → C → B → A → C → B  every arrival at B comes from C (the other side); C = B + (B−A)·overshoot
    per move we log: requested deg, pulse width the Servo library will send (544 + deg·1856/180 us), approach direction,
    the ramp time predicted from the firmware profile, the settle interval we wait, and the `?`/status reply.
Continuous mode:
    T +speed ms, wait, T −speed ms, wait, … logging speed, ms, pulse width (neutral ± speed·5 us) and the reply.

WHAT THESE LOGS DO NOT SHOW: the actual shaft angle, and whether the pulse really had that width on the pin. To verify those:
    * shaft angle  → the camera agent's line/tools/paddle_angle.py (paddle principal axis per frame, ±1°), or a 240-fps phone
                     video of a mark on the horn against a printed protractor;
    * pulse width  → a logic analyser / oscilloscope on D6 or D9 (expect 20.0 ms period, width as logged, ±2 us on AVR Timer1),
                     or a second Arduino's pulseIn() on the signal line.
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
import time

from line.arduino_link import (ACCEL_DEG_S2, BELT_US_PER_PCT, MAX_DEG_PER_S, FakeArduino, HttpPanelLink, servo_write_us)
from line.config import load


def ramp_time_s(delta_deg: float, vmax: float = MAX_DEG_PER_S, amax: float = ACCEL_DEG_S2) -> float:
    """Time the firmware's trapezoidal profile needs for a move of delta_deg (triangular if cruise is never reached)."""
    d = abs(delta_deg)
    if d < 1e-9:
        return 0.0
    d_cruise = vmax * vmax / amax  # distance needed to accelerate and decelerate through vmax
    if d <= d_cruise:
        return 2 * math.sqrt(d / amax)
    return 2 * vmax / amax + (d - d_cruise) / vmax


class Runner:
    def __init__(self, link, dry: bool, sleep, out: list[dict], quiet: bool):
        self.link, self.dry, self.sleep, self.out, self.quiet = link, dry, sleep, out, quiet
        self.t0 = time.monotonic()

    def send(self, line: str, **fields) -> str:
        t = time.monotonic() - self.t0
        reply = "(dry)" if self.dry else self.link.cmd(line)
        row = {"t_s": round(t, 3), "line": line, "reply": reply, **fields}
        self.out.append(row)
        if not self.quiet:
            print("  ".join(f"{k}={v}" for k, v in row.items()))
        return reply

    def wait(self, s: float, why: str):
        self.out.append({"t_s": round(time.monotonic() - self.t0, 3), "line": "", "reply": "", "wait_s": round(s, 3), "why": why})
        if not self.dry:
            self.sleep(s)


def positional(r: Runner, ch: str, a: int, b: int, cycles: int, settle: float, overshoot: float, hold: tuple[int, int],
               vmax: float, amax: float):
    def cmd(deg: int) -> str:
        return f"D {deg}" if ch == "d6" else f"S {deg} {hold[0]} {hold[1]}"

    def move(deg: int, frm: int, tag: str):
        direction = "+" if deg > frm else "-" if deg < frm else "0"
        rt = ramp_time_s(deg - frm, vmax, amax)
        r.send(cmd(deg), phase=tag, requested_deg=deg, pulse_us=servo_write_us(deg), approach=direction,
               from_deg=frm, ramp_s=round(rt, 3), settle_s=settle)
        r.wait(rt + settle, f"ramp {rt:.3f}s + settle {settle:.3f}s")
        return deg

    print(f"# positional repeatability on channel {ch}: A={a}° B={b}°, {cycles} cycles, settle {settle}s")
    print(f"# pulse widths (Servo library): A={servo_write_us(a)} us  B={servo_write_us(b)} us; ramp A→B {ramp_time_s(b - a, vmax, amax):.3f}s")
    pos = move(a, b, "park")
    for i in range(cycles):  # phase 1: arrive at B always from A
        pos = move(b, pos, f"same-side #{i + 1}")
        pos = move(a, pos, f"same-side return #{i + 1}")
    c = int(round(b + (b - a) * overshoot))
    c = max(0, min(180, c))
    print(f"# phase 2: arrive at B from the other side via C={c}°")
    for i in range(cycles):
        pos = move(c, pos, f"other-side via C #{i + 1}")
        pos = move(b, pos, f"other-side #{i + 1}")
        pos = move(a, pos, f"other-side return #{i + 1}")
    print("# done. Compare the shaft angle at every 'same-side' vs 'other-side' arrival at B: the gap is backlash + deadband.")


def continuous(r: Runner, speed: int, ms: int, cycles: int, pause: float, neutral: int):
    print(f"# continuous timed-motion repeatability: T ±{speed} {ms}ms × {cycles}; pulse {neutral + speed * BELT_US_PER_PCT}/"
          f"{neutral - speed * BELT_US_PER_PCT} us about neutral {neutral} us")
    print("# NOTE: a continuous servo has no position feedback; equal pulses give equal travel only if load, supply and neutral are constant.")
    r.send(f"N {neutral}", phase="neutral")
    for i in range(cycles):
        r.send(f"T {speed} {ms}", phase=f"fwd #{i + 1}", speed=speed, ms=ms, pulse_us=neutral + speed * BELT_US_PER_PCT, approach="+")
        r.wait(ms / 1000 + 0.04 + pause, "pulse + 2-frame neutral hold + pause")
        r.send(f"T {-speed} {ms}", phase=f"rev #{i + 1}", speed=-speed, ms=ms, pulse_us=neutral - speed * BELT_US_PER_PCT, approach="-")
        r.wait(ms / 1000 + 0.04 + pause, "pulse + 2-frame neutral hold + pause")
    print("# done. Mark the horn before the run: it should be back at the mark after every fwd+rev pair if travel is symmetric.")


def calibrate_neutral(r: Runner, lo: int, hi: int, step: int, dwell: float):
    print(f"# neutral calibration: N {lo}..{hi} step {step}, C 1 held {dwell}s each. Watch the horn: note the widest range where it is STILL;")
    print("# the middle of that range is the neutral to put in config (gate.neutral_us) and send with N at start-up.")
    for us in range(lo, hi + 1, step):
        r.send(f"N {us}", phase="neutral", pulse_us=us)
        r.send("C 1", phase="probe", pulse_us=us + BELT_US_PER_PCT)  # +5 us: as close to neutral as speed mode gets
        r.wait(dwell, "observe")
        r.send("C 0", phase="stop")
        r.wait(0.3, "settle")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["positional", "continuous", "calibrate-neutral"])
    ap.add_argument("--channel", default=None, help="positional: base|shoulder|elbow (S on D9/10/11) or d6 (D on D6); default cfg.gate.channel")
    ap.add_argument("--a", type=int, default=None, help="positional: angle A (default cfg.gate.open_deg)")
    ap.add_argument("--b", type=int, default=None, help="positional: angle B (default cfg.gate.flush_deg)")
    ap.add_argument("--overshoot", type=float, default=0.5, help="positional: C = B + (B-A)*overshoot for the other-side approach")
    ap.add_argument("--settle", type=float, default=0.4, help="positional: extra settle after the predicted ramp time")
    ap.add_argument("--speed", type=int, default=12)
    ap.add_argument("--ms", type=int, default=300)
    ap.add_argument("--pause", type=float, default=0.5)
    ap.add_argument("--neutral", type=int, default=1500)
    ap.add_argument("--lo", type=int, default=1440)
    ap.add_argument("--hi", type=int, default=1560)
    ap.add_argument("--step", type=int, default=10)
    ap.add_argument("--dwell", type=float, default=1.5)
    ap.add_argument("--cycles", type=int, default=10)
    ap.add_argument("--url", default=None, help="panel URL (default cfg.arduino.http_url)")
    ap.add_argument("--fake", action="store_true", help="run against FakeArduino, no hardware")
    ap.add_argument("--dry", action="store_true", help="print the plan, send nothing")
    ap.add_argument("--csv", default=None)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--fast", action="store_true", help="(tests) do not sleep")
    a = ap.parse_args(argv)

    cfg = load()
    sleep = (lambda s: None) if a.fast else time.sleep
    if a.fake:
        link = FakeArduino()
    elif a.dry:
        link = None
    else:
        link = HttpPanelLink(a.url or cfg.arduino.http_url)
        st = link.query()
        if not st.ok:
            sys.exit(f"Uno not reachable through the panel: {st.error}")
        print(f"# Uno via {link.url}: {st.raw}")
    out: list[dict] = []
    r = Runner(link, a.dry, sleep, out, a.quiet)
    ch = a.channel or cfg.gate.channel
    if a.mode == "positional":
        if ch not in ("base", "shoulder", "elbow", "d6"):
            sys.exit(f"positional needs channel base|shoulder|elbow|d6, got {ch!r} (a continuous servo cannot be positioned)")
        positional(r, ch, a.a if a.a is not None else cfg.gate.open_deg, a.b if a.b is not None else cfg.gate.flush_deg,
                   a.cycles, a.settle, a.overshoot, tuple(cfg.gate.hold_deg),
                   float(getattr(cfg.gate, "door_vmax_deg_s", MAX_DEG_PER_S)) if getattr(cfg.gate, "ramp_override", False) else MAX_DEG_PER_S,
                   float(getattr(cfg.gate, "door_accel_deg_s2", ACCEL_DEG_S2)) if getattr(cfg.gate, "ramp_override", False) else ACCEL_DEG_S2)
    elif a.mode == "continuous":
        continuous(r, max(1, min(100, abs(a.speed))), max(1, min(2000, a.ms)), a.cycles, a.pause, a.neutral)
    else:
        calibrate_neutral(r, a.lo, a.hi, a.step, a.dwell)
    if not a.dry and link is not None:
        try:
            print("# final status:", link.query().raw)
        except Exception as e:  # noqa: BLE001
            print("# final status unavailable:", e)
    if a.csv:
        keys: list[str] = []
        for row in out:
            for k in row:
                if k not in keys:
                    keys.append(k)
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(out)
        print(f"# log written: {a.csv} ({len(out)} rows)")
    return out


if __name__ == "__main__":
    main()
