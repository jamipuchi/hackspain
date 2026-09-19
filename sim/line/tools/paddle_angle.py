"""Measure the door paddle's actual angle from the camera, frame by frame. Owner: camera agent.

Jaume's servo brief asks for *measured* shaft position, not commanded pulses. The paddle is white on white
paper, so this tracks DARK MARKS on it (a dot of black tape / marker):

  two marks    put one mark at the paddle tip and one at its base near the horn: angle = the line between
               them. No pivot needed, immune to camera shifts. (recommended)
  one mark     tip mark only: angle = atan2(mark - pivot). Pivot from --pivot u,v, or found automatically as
               the centre of the circle the mark travels on once the paddle has moved through >= 3 positions.

Frames come from the panel (`/snapshot.jpg`, default; the panel keeps the phone) or directly from the
iPhone (--backend real, only when the panel is stopped). The ROI around the servo is given with --roi, or
found with --auto-roi from the blue servo body (bbox grown by --arm-mm along every side).

    cd ~/robotics && .venv/bin/python line/tools/paddle_angle.py --auto-roi --show
    .venv/bin/python line/tools/paddle_angle.py --roi 250,200,600,450 --once          # one reading, exit
    .venv/bin/python line/tools/paddle_angle.py --roi ... --seconds 30 --csv line/logs/paddle_test1.csv

Output: live `t angle` lines, a CSV (wall time, monotonic, angle, mark positions, n marks), and on exit a
plateau table: every stretch where the angle held still (|delta| < --tol for >= --hold s) with mean, std,
duration, and the settling time from the previous plateau. Compare plateau means across repeated commands
for repeatability; approach both directions for backlash. The numbers are positions of the paddle in the
image plane — with the phone looking straight down at the horn axis they equal shaft angles; a tilted view
compresses them (report the tilt if the phone is not overhead).
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from line.contracts import ROI, now  # noqa: E402

BLUE_LO, BLUE_HI = (95, 120, 60), (130, 255, 255)  # HSV range of the SG90 case


# ----------------------------------------------------------------------------- frame sources
class SnapshotSource:
    """Polls the panel's /snapshot.jpg (it carries the HUD overlay in the top-left; keep the ROI away from it)."""

    def __init__(self, url: str = "http://127.0.0.1:8800/snapshot.jpg", timeout_s: float = 2.0):
        self.url, self.timeout_s, self.seq = url, timeout_s, 0
        self._last: bytes | None = None

    def grab(self) -> tuple[np.ndarray, float]:
        # the panel re-serves the same JPEG until the camera delivers a new frame (30 fps): skip duplicates so
        # every reading is a distinct frame, otherwise the log shows ~140 identical rows per second
        for _ in range(200):
            with urllib.request.urlopen(self.url, timeout=self.timeout_s) as r:
                raw = r.read()
            if raw != self._last:
                break
            time.sleep(0.005)
        self._last = raw
        buf = np.frombuffer(raw, np.uint8)
        t = now()
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError("snapshot did not decode")
        self.seq += 1
        return img, t

    def close(self) -> None:
        pass


class RealSource:
    def __init__(self):
        from line.camera_source import RealCamera
        from line.config import load

        self.cam = RealCamera(load())
        if not self.cam.ok:
            sys.exit(f"camera: {self.cam.error}")

    def grab(self):
        f = self.cam.grab()
        return f.bgr, f.t

    def close(self):
        self.cam.close()


# ----------------------------------------------------------------------------- detection
def find_servo_body(bgr: np.ndarray) -> tuple[int, int, int, int] | None:
    """bbox (x, y, w, h) of the largest blue blob = the SG90 case, or None."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    blue = cv2.inRange(hsv, BLUE_LO, BLUE_HI)
    n, _lab, st, _ce = cv2.connectedComponentsWithStats(blue)
    if n <= 1:
        return None
    i = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    if st[i, cv2.CC_STAT_AREA] < 200:
        return None
    return tuple(int(v) for v in st[i, :4])


def auto_roi(bgr: np.ndarray, arm_px: int) -> ROI | None:
    b = find_servo_body(bgr)
    if b is None:
        return None
    x, y, w, h = b
    H, W = bgr.shape[:2]
    return ROI(max(x - arm_px, 0), max(y - arm_px, 0), min(x + w + arm_px, W), min(y + h + arm_px, H))


def find_marks(bgr_roi: np.ndarray, gray_max: int = 70, min_area: int = 12, max_area: int = 2500, k: int = 2,
               exclude: tuple[int, int, int, int] | None = None) -> list[tuple[float, float, float]]:
    """Up to k dark marks in the ROI as (u, v, area), largest first.

    Blue (servo case) and saturated colours are excluded, and so is the `exclude` bbox (x, y, w, h in ROI px, grown by
    6 px): the case carries black print and a dark connector that would otherwise pass as marks.
    """
    gray = cv2.cvtColor(bgr_roi, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(bgr_roi, cv2.COLOR_BGR2HSV)
    dark = (gray < gray_max) & (hsv[..., 1] < 120)  # dark AND unsaturated: black tape, not the blue case or orange wire
    if exclude is not None:
        x, y, w, h = exclude
        dark[max(y - 6, 0): y + h + 6, max(x - 6, 0): x + w + 6] = False
    dark = cv2.morphologyEx(dark.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, _lab, st, ce = cv2.connectedComponentsWithStats(dark, connectivity=8)
    marks = []
    for i in range(1, n):
        a = st[i, cv2.CC_STAT_AREA]
        if min_area <= a <= max_area:
            marks.append((float(ce[i, 0]), float(ce[i, 1]), float(a)))
    marks.sort(key=lambda m: -m[2])
    return marks[:k]


def angle_two_marks(m1, m2) -> float:
    """Angle in degrees of the line from the mark nearer the servo (m2) to the tip (m1); caller orders them."""
    return math.degrees(math.atan2(m1[1] - m2[1], m1[0] - m2[0]))


def angle_about_pivot(mark, pivot) -> float:
    return math.degrees(math.atan2(mark[1] - pivot[1], mark[0] - pivot[0]))


def fit_circle(points: np.ndarray) -> tuple[float, float, float] | None:
    """Least-squares circle (cx, cy, r) through >= 3 distinct points (Kasa fit)."""
    if len(points) < 3:
        return None
    x, y = points[:, 0], points[:, 1]
    A = np.column_stack([x, y, np.ones_like(x)])
    b = x * x + y * y
    try:
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    except np.linalg.LinAlgError:
        return None
    cx, cy = sol[0] / 2, sol[1] / 2
    r2 = sol[2] + cx * cx + cy * cy
    if r2 <= 0:
        return None
    return float(cx), float(cy), float(math.sqrt(r2))


def unwrap_deg(prev: float | None, cur: float) -> float:
    if prev is None:
        return cur
    while cur - prev > 180:
        cur -= 360
    while cur - prev < -180:
        cur += 360
    return cur


# ----------------------------------------------------------------------------- plateaus
@dataclass
class Plateau:
    t0: float
    t1: float
    mean: float
    std: float
    n: int
    settle_s: float | None  # time from the end of the previous plateau to the start of this one


def plateaus(ts: list[float], angles: list[float], tol_deg: float = 1.5, hold_s: float = 0.4) -> list[Plateau]:
    """Stretches where consecutive angles stay within tol_deg of the running plateau mean for >= hold_s."""
    out: list[Plateau] = []
    i, n = 0, len(ts)
    last_end: float | None = None
    while i < n:
        j = i
        vals = [angles[i]]
        while j + 1 < n and abs(angles[j + 1] - float(np.mean(vals))) <= tol_deg:
            j += 1
            vals.append(angles[j])
        if ts[j] - ts[i] >= hold_s and len(vals) >= 3:
            out.append(Plateau(ts[i], ts[j], float(np.mean(vals)), float(np.std(vals)), len(vals),
                               None if last_end is None else ts[i] - last_end))
            last_end = ts[j]
        i = j + 1
    return out


def format_plateaus(pl: list[Plateau], t_ref: float) -> str:
    lines = ["plateau   start_s   dur_s   angle_deg   std   n   settle_s"]
    for k, p in enumerate(pl):
        lines.append(f"{k:>7}  {p.t0 - t_ref:8.2f}  {p.t1 - p.t0:6.2f}  {p.mean:9.2f}  {p.std:4.2f}  {p.n:>3}   "
                     + ("   -" if p.settle_s is None else f"{p.settle_s:5.2f}"))
    if len(pl) >= 2:
        means = [p.mean for p in pl]
        lines.append(f"spread of plateau angles: {max(means) - min(means):.2f} deg over {len(pl)} plateaus")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- main loop
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", choices=["snapshot", "real"], default="snapshot")
    ap.add_argument("--url", default="http://127.0.0.1:8800/snapshot.jpg")
    ap.add_argument("--roi", help="x0,y0,x1,y1 around the servo + paddle")
    ap.add_argument("--auto-roi", action="store_true", help="ROI = blue servo body grown by --arm-mm on every side")
    ap.add_argument("--arm-mm", type=float, default=70.0, help="paddle length for --auto-roi")
    ap.add_argument("--px-per-mm", type=float, default=4.3)
    ap.add_argument("--mode", choices=["auto", "two", "one"], default="auto", help="two marks, one mark + pivot, or auto (two if seen)")
    ap.add_argument("--pivot", help="u,v of the horn axis in full-frame px (one-mark mode); default: circle fit from motion")
    ap.add_argument("--gray-max", type=int, default=70, help="marks are darker than this")
    ap.add_argument("--seconds", type=float, default=0.0, help="stop after this long (0 = until q / Ctrl-C)")
    ap.add_argument("--once", action="store_true", help="print one reading and exit")
    ap.add_argument("--tol", type=float, default=1.5, help="plateau tolerance, deg")
    ap.add_argument("--hold", type=float, default=0.4, help="plateau minimum duration, s")
    ap.add_argument("--csv", help="log file (default line/logs/paddle_angle_<ts>.csv)")
    ap.add_argument("--show", action="store_true", help="window with ROI, marks and angle")
    args = ap.parse_args()

    src = RealSource() if args.backend == "real" else SnapshotSource(args.url)
    img, _ = src.grab()
    if args.roi:
        x0, y0, x1, y1 = (int(v) for v in args.roi.split(","))
        roi = ROI(x0, y0, x1, y1)
    elif args.auto_roi:
        roi = auto_roi(img, int(args.arm_mm * args.px_per_mm))
        if roi is None:
            sys.exit("auto-roi: no blue servo body found; pass --roi")
        print(f"auto ROI from servo body: {roi.x0},{roi.y0},{roi.x1},{roi.y1}")
    else:
        roi = ROI(0, 0, img.shape[1], img.shape[0])
    pivot = tuple(float(v) for v in args.pivot.split(",")) if args.pivot else None
    body = find_servo_body(img)
    if body and pivot is None:
        print(f"servo body at x,y,w,h={body} (pivot will be fitted from the tip's motion; pass --pivot to fix it)")

    csv_path = Path(args.csv) if args.csv else ROOT / "line" / "logs" / f"paddle_angle_{time.strftime('%Y%m%d-%H%M%S')}.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fh = csv_path.open("w", newline="")
    wr = csv.writer(fh)
    wr.writerow(["wall_time", "t_mono", "angle_deg", "mode", "tip_u", "tip_v", "base_u", "base_v", "n_marks", "pivot_u", "pivot_v"])

    ts: list[float] = []
    angles: list[float] = []
    tips: list[tuple[float, float]] = []
    prev_angle: float | None = None
    t_start = now()
    print("t_s      angle_deg  mode  marks   (q or Ctrl-C to stop)")
    try:
        while True:
            img, t = src.grab()
            crop = roi.crop(img)
            body_roi = (body[0] - roi.x0, body[1] - roi.y0, body[2], body[3]) if body else None
            marks = find_marks(crop, gray_max=args.gray_max, exclude=body_roi)
            marks_full = [(m[0] + roi.x0, m[1] + roi.y0, m[2]) for m in marks]
            angle: float | None = None
            mode = "-"
            tip = base = None
            if len(marks_full) >= 2 and args.mode in ("auto", "two"):
                # tip = the mark farther from the servo body (or from the pivot); base = the other
                ref = pivot or ((body[0] + body[2] / 2, body[1] + body[3] / 2) if body else None)
                if ref is not None:
                    m_sorted = sorted(marks_full[:2], key=lambda m: -math.hypot(m[0] - ref[0], m[1] - ref[1]))
                else:
                    m_sorted = marks_full[:2]
                tip, base = m_sorted[0], m_sorted[1]
                angle, mode = angle_two_marks(tip, base), "two"
            elif len(marks_full) >= 1 and args.mode in ("auto", "one"):
                tip = marks_full[0]
                tips.append((tip[0], tip[1]))
                if pivot is None and len(tips) >= 3:
                    pts = np.array(tips[-400:])
                    if np.ptp(pts, axis=0).max() > 8:  # only fit once the tip actually moved
                        c = fit_circle(pts)
                        if c and c[2] > 5:
                            pivot = (c[0], c[1])
                if pivot is not None:
                    angle, mode = angle_about_pivot(tip, pivot), "one"
                else:
                    mode = "one(no pivot yet)"
            if angle is not None:
                angle = unwrap_deg(prev_angle, angle)
                prev_angle = angle
                ts.append(t)
                angles.append(angle)
            f1 = lambda v: "" if v == "" or v is None else f"{v:.1f}"  # noqa: E731
            wr.writerow([time.strftime("%H:%M:%S") + f".{int((time.time() % 1) * 1000):03d}", f"{t:.3f}",
                         "" if angle is None else f"{angle:.2f}", mode,
                         *(f1(v) for v in (tip[:2] if tip else ("", ""))), *(f1(v) for v in (base[:2] if base else ("", ""))),
                         len(marks_full), *(f1(v) for v in (pivot or ("", "")))])
            print(f"{t - t_start:7.2f}  {'  --   ' if angle is None else f'{angle:8.2f}'}  {mode:<5} {len(marks_full)}", flush=True)
            if args.show:
                view = img.copy()
                cv2.rectangle(view, (roi.x0, roi.y0), (roi.x1 - 1, roi.y1 - 1), (0, 200, 255), 2)
                for m in marks_full:
                    cv2.circle(view, (int(m[0]), int(m[1])), 6, (0, 0, 255), 2)
                if pivot:
                    cv2.drawMarker(view, (int(pivot[0]), int(pivot[1])), (255, 0, 0), cv2.MARKER_CROSS, 20, 2)
                if tip and (base or pivot):
                    o = base or pivot
                    cv2.line(view, (int(o[0]), int(o[1])), (int(tip[0]), int(tip[1])), (0, 255, 0), 2)
                cv2.putText(view, "--" if angle is None else f"{angle:.1f} deg", (roi.x0, max(roi.y0 - 8, 16)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
                cv2.imshow("paddle_angle", cv2.resize(view, None, fx=0.6, fy=0.6))
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
            if args.once or (args.seconds and t - t_start >= args.seconds):
                break
    except KeyboardInterrupt:
        pass
    finally:
        fh.close()
        src.close()
        if args.show:
            cv2.destroyAllWindows()
    print(f"\n{len(angles)} readings in {(ts[-1] - ts[0]) if len(ts) > 1 else 0:.1f} s -> {csv_path}")
    if pivot:
        print(f"pivot used: {pivot[0]:.1f},{pivot[1]:.1f}")
    if len(angles) >= 3:
        print(format_plateaus(plateaus(ts, angles, args.tol, args.hold), ts[0]))
    elif not angles:
        print("no angle measured: no dark marks in the ROI. Put a black tape dot on the paddle tip (and one at its base),"
              " check --roi covers the paddle, or raise --gray-max.")


if __name__ == "__main__":
    main()
