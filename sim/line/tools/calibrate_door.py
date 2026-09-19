"""Camera-verified calibration of the continuous-servo door (pin 6 timed pulses). Owner: integrator.

    cd ~/robotics && .venv/bin/python -m line.tools.calibrate_door --speeds 25,40,60,80,100 [--box 420,120,720,330]

For each speed: from CLOSED, nudge towards OPEN in small steps until the camera sees the paddle stop moving (it is
against the wall), then the same from OPEN towards CLOSED. Pulse = converged time + margin, so both positions end on
their stop. Then 3 full cycles are run and the frames of equal states compared: the residual must stay under `thr`.
Config is written through the panel API after each speed that passes; the panel must be running on :8800 and the sorting
loop stopped. Nothing here runs faster than the given speeds and every spin is followed by a stop.
"""

from __future__ import annotations

import argparse
import os
import json
import time
import urllib.request

import cv2
import numpy as np

PANEL = "http://127.0.0.1:8800"
DEBUG = "/private/tmp/claude-501/-Users-jaumepuig-Documents-leads-gpt/5bfec454-67e6-4f65-934f-94fe28c87f85/scratchpad/cal"
UNO = "http://127.0.0.1:8765"


def api(path: str, body: dict | None = None):
    req = urllib.request.Request(PANEL + path, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def uno(line: str) -> None:
    urllib.request.urlopen(urllib.request.Request(f"{UNO}/cmd?line={urllib.parse.quote(line)}", method="POST"), timeout=5).read()


import urllib.parse  # noqa: E402


def full_frame():
    b = urllib.request.urlopen(PANEL + "/snapshot.jpg", timeout=5).read()
    return cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_COLOR)


def find_servo(img) -> tuple[int, int] | None:
    """Centroid of the blue SG90 body: saturated blue, compact (not text strokes, not the teal PCB)."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (100, 120, 60), (125, 255, 255))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    n, lab, st, cen = cv2.connectedComponentsWithStats(m)
    best = None
    for i in range(1, n):
        x, y, w, h, area = st[i]
        if not (300 <= area <= 8000):
            continue
        fill = area / max(w * h, 1)
        aspect = max(w, h) / max(min(w, h), 1)
        if fill < 0.55 or aspect > 3.0:
            continue  # text, wires, PCB traces
        score = area * fill
        if best is None or score > best[0]:
            best = (score, int(cen[i][0]), int(cen[i][1]), (int(x), int(y), int(w), int(h), int(area), round(fill, 2)))
    if best is None:
        return None
    print(f"servo candidate bbox x,y,w,h,area,fill = {best[3]}")
    return best[1], best[2]


def auto_box(img, reach: int = 230) -> tuple[int, int, int, int]:
    hub = find_servo(img)
    if hub is None:
        raise SystemExit("blue servo body not found in the frame: cannot place the calibration box")
    h, w = img.shape[:2]
    return max(0, hub[0] - reach), max(40, hub[1] - reach), min(w, hub[0] + reach), min(h, hub[1] + reach)


def frame(box):
    img = full_frame()
    x0, y0, x1, y1 = box
    return cv2.GaussianBlur(img[y0:y1, x0:x1], (7, 7), 0).astype(np.int16)


def diff(a, b) -> float:
    """Number of pixels that changed by more than 20 levels: a thin white paddle moving is thousands, noise is tens."""
    return float((np.abs(a - b).max(axis=2) > 20).sum())


def spin(sp: int, ms: int) -> None:
    """One raw pulse with a guaranteed stop (bypasses the adapter so durations are exact)."""
    try:
        uno(f"C {sp}")
        time.sleep(ms / 1000)
    finally:
        uno("C 0")
        uno("C 0")


def settle(box, n=2, dt=0.35):
    time.sleep(dt)
    return frame(box)


def converge(sp: int, box, step_ms: int, cap_ms: int, thr: float, label: str) -> tuple[int, np.ndarray]:
    """Nudge with `step_ms` pulses until the frame stops changing. Returns (total_ms, final frame)."""
    prev = settle(box)
    total = 0
    still = 0
    moved = False
    while total < cap_ms:
        spin(sp, step_ms)
        total += step_ms
        cur = settle(box)
        d = diff(prev, cur)
        print(f"    {label}: +{step_ms} ms (total {total}) → change {d:.2f}")
        cv2.imwrite(f"{DEBUG}/{label.strip('→')}-{total:03d}ms.jpg", np.clip(cur, 0, 255).astype(np.uint8))
        prev = cur
        if d < thr:
            still += 1
            if moved and still >= 2:  # it moved earlier and now two nudges changed nothing: on the stop
                return total - 2 * step_ms, cur
            if not moved and still >= 3:
                raise SystemExit(f"{label}: no motion after {total} ms of nudges — pulses too short or servo not powered; aborting without saving")
        else:
            moved = True
            still = 0
    print(f"    {label}: cap {cap_ms} ms reached without a clear stop")
    return total, prev


def main() -> None:
    os.makedirs(DEBUG, exist_ok=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--speeds", default="25,40,60,80,100")
    ap.add_argument("--box", default="auto", help="auto = around the blue servo body; or x0,y0,x1,y1")
    ap.add_argument("--hub", default=None, help="override the servo position as x,y pixels")
    ap.add_argument("--margin", type=float, default=0.30, help="extra fraction of the converged time so the door always reaches its stop")
    ap.add_argument("--cycles", type=int, default=3)
    a = ap.parse_args()
    st = api("/api/state")
    img0 = full_frame()
    box = auto_box(img0) if a.box == "auto" else tuple(int(v) for v in a.box.split(","))
    hub = tuple(int(v) for v in a.hub.split(',')) if a.hub else find_servo(img0)
    if a.hub:
        box = (max(0, hub[0] - 230), max(40, hub[1] - 230), min(img0.shape[1], hub[0] + 230), min(img0.shape[0], hub[1] + 230))
    print(f"servo body at {hub}, calibration box {box}")
    cv2.imwrite(f"{DEBUG}/box.jpg", cv2.rectangle(img0.copy(), box[:2], box[2:], (0, 0, 255), 3))
    cfg = st["cfg"]
    assert not st["line"]["enabled"], "stop the sorting loop first (Line tab → stop)"
    d6dir = 1 if int(cfg["door_d6_dir"]) >= 0 else -1
    sp_open, sp_close = -1 * d6dir, +1 * d6dir  # adapter: OPEN = +1*dir... see panel.DoorOnD6._pulse (direction +1 for OPEN)
    sp_open, sp_close = +1 * d6dir, -1 * d6dir
    # noise floor
    f0 = frame(box)
    time.sleep(0.4)
    noise = diff(f0, frame(box))
    thr = max(150.0, noise * 4 + 100)
    print(f"camera noise in box {box}: {noise:.2f} → motion threshold {thr:.2f}")
    results = {}
    door_pos = api("/api/state")["modules"]["arduino"].get("door_pos")
    print(f"door reported {door_pos}; making sure it is CLOSED first (at 25%)")
    if door_pos != "CLOSED":
        spin(25 * sp_close, int(cfg["door_d6_close_ms"]))
    for sp in [int(s) for s in a.speeds.split(",")]:
        print(f"\n=== speed {sp}% ===")
        scale = 25 / sp
        step = max(60, int(round(70 * scale)))  # the servo needs ≥3 PWM periods (~60 ms) to start moving at all
        cap = int(400 * scale)
        t_open, f_open = converge(sp * sp_open, box, step, cap, thr, "→OPEN")
        t_close, f_close = converge(sp * sp_close, box, step, cap, thr, "→CLOSED")
        ms_open, ms_close = int(t_open * (1 + a.margin)) + 10, int(t_close * (1 + a.margin)) + 10
        print(f"  converged: open {t_open} ms, close {t_close} ms → pulses open {ms_open} ms, close {ms_close} ms")
        # verification cycles
        worst_open = worst_close = 0.0
        ref_o, ref_c = None, f_close
        for k in range(a.cycles):
            spin(sp * sp_open, ms_open)
            fo = settle(box)
            spin(sp * sp_close, ms_close)
            fc = settle(box)
            if ref_o is None:
                ref_o = fo
            do, dc = diff(ref_o, fo), diff(ref_c, fc)
            worst_open, worst_close = max(worst_open, do), max(worst_close, dc)
            print(f"  cycle {k + 1}: OPEN residual {do:.2f}  CLOSED residual {dc:.2f}  (threshold {thr:.2f})")
        ok = worst_open < thr and worst_close < thr
        results[sp] = dict(open_ms=ms_open, close_ms=ms_close, worst_open=round(worst_open, 2), worst_close=round(worst_close, 2), ok=ok)
        if ok:
            for path, val in (("door_d6_speed", sp), ("door_d6_open_ms", ms_open), ("door_d6_close_ms", ms_close)):
                api("/api/config", {"path": path, "value": val})
            api("/api/door", {"action": "assume", "pos": "closed"})
            print(f"  ✓ speed {sp}% repeatable; saved to config")
        else:
            print(f"  ✗ speed {sp}% NOT repeatable (open {worst_open:.2f}, close {worst_close:.2f} vs {thr:.2f}); keeping the last good speed")
            break
    print("\nsummary:", json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
