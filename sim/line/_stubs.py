"""Stand-ins for every subsystem so the panel and the closed loop run before (or without) the real modules.

Owner: integrator. Each stub honours the contract in `contracts.py` exactly; the real implementations replace them
one at a time through `panel.build_modules()`. They are also the fixtures for pipeline/panel tests.
"""

from __future__ import annotations

import math
import threading
import time

import cv2
import numpy as np

from line.config import LineConfig
from line.contracts import ArduinoState, Blob, Check, Frame, ROI, Verdict, now


# ------------------------------------------------------------------ arduino
class StubArduino:
    """magnet_arm firmware protocol in Python: S/M/C/H/? with a 200°/s ramp so `B 1` shows while moving."""

    name = "stub-arduino"
    SPEED_DEG_S = 200.0

    def __init__(self, home=(150, 125, 75)):
        self.lock = threading.Lock()
        self.target = list(home)
        self.current = [float(h) for h in home]
        self.magnet = False
        self.belt = 0
        self.port = "fake"
        self.log: list[tuple[float, str, str]] = []
        self._t = now()

    def _advance(self) -> None:
        t = now()
        dt, self._t = t - self._t, t
        for i in range(3):
            d = self.target[i] - self.current[i]
            step = math.copysign(min(abs(d), self.SPEED_DEG_S * dt), d) if d else 0.0
            self.current[i] += step

    def cmd(self, line: str) -> str:
        with self.lock:
            self._advance()
            p = line.strip().split()
            if not p:
                reply = "err"
            elif p[0] == "S" and len(p) == 4:
                self.target = [max(0, min(180, int(float(v)))) for v in p[1:]]
                reply = "ok"
            elif p[0] == "M" and len(p) == 2:
                self.magnet = p[1] == "1"
                reply = "ok"
            elif p[0] == "C" and len(p) == 2:
                self.belt = max(-100, min(100, int(float(p[1]))))
                reply = "ok"
            elif p[0] == "H":
                self.target = [150, 125, 75]
                reply = "ok"
            elif p[0] == "?":
                moving = int(any(abs(t - c) > 0.01 for t, c in zip(self.target, self.current)))
                reply = f"P {round(self.current[0])} {round(self.current[1])} {round(self.current[2])} M {int(self.magnet)} B {moving}"
            else:
                reply = "err"
            self.log.append((now(), line.strip(), reply))
            return reply

    def query(self) -> ArduinoState:
        r = self.cmd("?").split()
        return ArduinoState(pos=(int(r[1]), int(r[2]), int(r[3])), magnet=r[5] == "1", moving=r[7] == "1", belt=self.belt, raw=" ".join(r), port=self.port)

    def status(self) -> dict:
        s = self.query()
        return {"name": self.name, "port": s.port, "pos": list(s.pos), "magnet": s.magnet, "moving": s.moving, "belt": s.belt, "commands": len(self.log), "ok": True}

    def selftest(self) -> list[Check]:
        t0 = now()
        r = self.cmd("?")
        return [Check("arduino.query", r.startswith("P "), r, (now() - t0) * 1000), Check("arduino.belt_stop", self.cmd("C 0") == "ok", "C 0 → ok")]

    def close(self) -> None:
        self.cmd("C 0")


# ------------------------------------------------------------------ gate
class StubGate:
    """Timer-based door driver on top of any ArduinoLink. Non-blocking pulse, coalesced, dry_run aware."""

    name = "stub-gate"

    def __init__(self, link, cfg: LineConfig, dry_run: bool = True):
        self.link, self.cfg, self.dry_run = link, cfg, dry_run
        self.state = "flush"
        self.events: list[tuple[float, str]] = []
        self._timers: list[threading.Timer] = []
        self._lock = threading.Lock()
        self._flush_due = 0.0

    def _send(self, deg: int, what: str) -> None:
        hold = self.cfg.gate.hold_deg
        line = f"S {deg} {hold[0]} {hold[1]}"
        self.events.append((now(), f"{what} {'(dry)' if self.dry_run else ''} {line}"))
        if not self.dry_run:
            self.link.cmd(line)

    def flush(self) -> None:
        with self._lock:
            self.state = "flush"
            self._send(self.cfg.gate.flush_deg, "flush")

    def open(self) -> None:
        with self._lock:
            self.state = "open"
            self._send(self.cfg.gate.open_deg, "open")

    def _flush_if_due(self, due: float) -> None:
        with self._lock:
            if due < self._flush_due - 1e-6:
                return  # a later pulse extended the dwell
        self.flush()

    def pulse(self, delay_s: float, dwell_s: float) -> None:
        delay_s = max(0.0, delay_s)
        with self._lock:
            self.state = "scheduled" if delay_s > 0 else self.state
            due = now() + delay_s + dwell_s
            self._flush_due = max(self._flush_due, due)
            t_open = threading.Timer(delay_s, self.open)
            t_close = threading.Timer(delay_s + dwell_s, self._flush_if_due, args=(due,))
            for t in (t_open, t_close):
                t.daemon = True
                t.start()
                self._timers.append(t)
            self._timers = [t for t in self._timers if t.is_alive()]

    def status(self) -> dict:
        return {"name": self.name, "state": self.state, "dry_run": self.dry_run, "flush_deg": self.cfg.gate.flush_deg, "open_deg": self.cfg.gate.open_deg, "pulses": sum(1 for _, e in self.events if e.startswith("open")), "last": self.events[-1][1] if self.events else ""}

    def selftest(self) -> list[Check]:
        saved, self.dry_run = self.dry_run, True
        n0 = len(self.events)
        t0 = now()
        self.pulse(0.05, 0.05)
        time.sleep(0.2)
        self.dry_run = saved
        ev = self.events[n0:]
        ok = len(ev) >= 2 and ev[0][1].startswith("open") and ev[-1][1].startswith("flush")
        t_open = (ev[0][0] - t0) if ev else 0
        return [Check("gate.pulse_timing", ok and abs(t_open - 0.05) < 0.03, f"open after {t_open * 1000:.0f} ms (want 50), {len(ev)} events, state={self.state}")]

    def close(self) -> None:
        for t in self._timers:
            t.cancel()
        self.flush()


# ------------------------------------------------------------------ camera
class StubCamera:
    """Synthetic beans rolling across white paper along cfg.camera.flow_axis. Deterministic; `defect_every` makes every n-th bean dark."""

    name = "stub-camera"

    def __init__(self, cfg: LineConfig, speed_px_s: float = 400.0, gap_s: float = 2.0, defect_every: int = 3, seed: int = 0):
        self.cfg = cfg
        self.w, self.h = cfg.camera.width, cfg.camera.height
        self.speed, self.gap, self.defect_every = speed_px_s, gap_s, defect_every
        self.rng = np.random.default_rng(seed)
        self.t0 = now()
        self.seq = 0
        self.frames_t: list[float] = []

    def bean_at(self, t: float) -> tuple[float, float, bool] | None:
        """(u, v, is_defect) of the bean on screen at time t, or None between beans."""
        period = self.w / self.speed + self.gap
        k = int(t // period)
        tau = t - k * period
        travel = self.w / self.speed
        if tau > travel:
            return None
        u = tau * self.speed if self.cfg.camera.flow_axis.endswith("x") else self.w / 2
        v = self.h / 2 + 30 * math.sin(k) if self.cfg.camera.flow_axis.endswith("x") else tau * self.speed
        if self.cfg.camera.flow_axis.startswith("-"):
            u, v = (self.w - u, v) if self.cfg.camera.flow_axis.endswith("x") else (u, self.h - v)
        return u, v, (k % self.defect_every == self.defect_every - 1)

    def grab(self) -> Frame:
        t = now()
        img = np.full((self.h, self.w, 3), 235, np.uint8)
        img += self.rng.integers(-6, 6, img.shape, dtype=np.int16).astype(np.uint8)
        b = self.bean_at(t - self.t0)
        if b is not None:
            u, v, defect = b
            color = (35, 30, 30) if defect else (70, 95, 140)  # BGR: near-black burnt bean vs brown roasted bean
            cv2.ellipse(img, (int(u), int(v)), (int(6 * self.cfg.camera.px_per_mm), int(4 * self.cfg.camera.px_per_mm)), 15, 0, 360, color, -1, cv2.LINE_AA)
            cv2.ellipse(img, (int(u), int(v)), (int(5 * self.cfg.camera.px_per_mm), int(0.6 * self.cfg.camera.px_per_mm)), 15, 0, 360, (200, 200, 200), -1, cv2.LINE_AA)  # crease
        self.seq += 1
        self.frames_t.append(t)
        self.frames_t = self.frames_t[-120:]
        return Frame(img, t, self.seq, self.name)

    def status(self) -> dict:
        ft = self.frames_t
        fps = (len(ft) - 1) / (ft[-1] - ft[0]) if len(ft) > 2 and ft[-1] > ft[0] else 0.0
        return {"name": self.name, "resolution": [self.w, self.h], "fps_measured": round(fps, 1), "grab_latency_ms": 0.0, "frames": self.seq, "dropped": 0, "ok": True}

    def selftest(self) -> list[Check]:
        t0 = now()
        f = self.grab()
        return [Check("camera.grab", f.bgr.shape == (self.h, self.w, 3), f"{f.bgr.shape[1]}x{f.bgr.shape[0]} in {(now() - t0) * 1000:.1f} ms")]

    def close(self) -> None:
        return None


# ------------------------------------------------------------------ detector
FEATURES = ["area_mm2", "major_mm", "minor_mm", "aspect", "fill", "mean_r", "mean_g", "mean_b", "mean_gray", "std_gray", "dark_frac", "n_dark_spots"]


class StubDetector:
    """Dark blobs on bright paper: threshold + connected components. Good enough to drive the loop; the camera agent's PaperBeanDetector replaces it."""

    name = "stub-detector"

    def __init__(self, cfg: LineConfig):
        self.cfg = cfg
        self.n_frames = 0
        self.last_ms = 0.0

    def detect(self, frame: Frame, roi: ROI | None = None) -> list[Blob]:
        t0 = now()
        v = self.cfg.vision
        img = roi.crop(frame.bgr) if roi else frame.bgr
        ox, oy = (roi.x0, roi.y0) if roi else (0, 0)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        mask = (gray < v.bean_gray_max).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        n, labels, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
        ppm = self.cfg.camera.px_per_mm
        out: list[Blob] = []
        H, W = gray.shape
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            if area < v.min_area_px or area > v.max_area_px:
                continue
            m = labels == i
            pts = np.column_stack(np.nonzero(m)).astype(np.float64)  # (row, col)
            cov = np.cov(pts.T) if len(pts) > 5 else np.eye(2)
            ev = np.sort(np.linalg.eigvalsh(cov))[::-1]
            major, minor = 4 * math.sqrt(max(ev[0], 1e-9)), 4 * math.sqrt(max(ev[1], 1e-9))
            px = img[m]
            g = gray[m].astype(np.float32)
            feats = {
                "area_mm2": float(area) / ppm**2, "major_mm": major / ppm, "minor_mm": minor / ppm, "aspect": major / max(minor, 1e-6),
                "fill": float(area) / max(w * h, 1), "mean_r": float(px[:, 2].mean()), "mean_g": float(px[:, 1].mean()), "mean_b": float(px[:, 0].mean()),
                "mean_gray": float(g.mean()), "std_gray": float(g.std()), "dark_frac": float((g < v.dark_spot_gray).mean()),
                "n_dark_spots": float(cv2.connectedComponents(((gray < v.dark_spot_gray) & m).astype(np.uint8))[0] - 1),
            }
            partial = x <= 0 or y <= 0 or x + w >= W or y + h >= H
            out.append(Blob(u=float(cents[i][0] + ox), v=float(cents[i][1] + oy), bbox=(int(x + ox), int(y + oy), int(w), int(h)), area_px=float(area), partial=partial, features=feats))
        self.n_frames += 1
        self.last_ms = (now() - t0) * 1000
        return out

    def status(self) -> dict:
        return {"name": self.name, "frames": self.n_frames, "last_ms": round(self.last_ms, 1), "features": FEATURES, "ok": True}

    def selftest(self) -> list[Check]:
        img = np.full((200, 300, 3), 235, np.uint8)
        cv2.ellipse(img, (150, 100), (36, 24), 0, 0, 360, (60, 80, 120), -1)
        blobs = self.detect(Frame(img, now(), 0, "test"))
        ok = len(blobs) == 1 and abs(blobs[0].u - 150) < 2 and not blobs[0].partial
        return [Check("detector.synthetic_bean", ok, f"{len(blobs)} blob(s), {self.last_ms:.1f} ms" + (f", centre ({blobs[0].u:.0f},{blobs[0].v:.0f})" if blobs else ""))]

    def close(self) -> None:
        return None


# ------------------------------------------------------------------ classifier
class StubClassifier:
    """Rules from cfg.classifier.rules over the detector's features. The coffee-sim agent's RuleClassifier supersedes this."""

    name = "stub-rule-classifier"

    def __init__(self, cfg: LineConfig):
        self.cfg = cfg
        self.n = 0
        self.ms_total = 0.0

    def classify(self, frame: Frame, blob: Blob) -> Verdict:
        t0 = now()
        r = self.cfg.classifier.rules
        f = blob.features
        reasons = []
        if f.get("major_mm", 0) < r["min_major_mm"]:
            reasons.append(f"major {f['major_mm']:.1f} mm < {r['min_major_mm']}: fragment")
        if f.get("major_mm", 0) > r["max_major_mm"]:
            reasons.append(f"major {f['major_mm']:.1f} mm > {r['max_major_mm']}: foreign/double")
        if not (r["min_aspect"] <= f.get("aspect", 1.5) <= r["max_aspect"]):
            reasons.append(f"aspect {f.get('aspect', 0):.2f} outside [{r['min_aspect']}, {r['max_aspect']}]")
        if f.get("dark_frac", 0) > r["max_dark_frac"]:
            reasons.append(f"dark fraction {f['dark_frac']:.2f} > {r['max_dark_frac']}: black/burnt")
        if f.get("mean_gray", 90) < r["min_mean_gray"]:
            reasons.append(f"mean grey {f['mean_gray']:.0f} < {r['min_mean_gray']}: burnt")
        if f.get("mean_gray", 90) > r["max_mean_gray"]:
            reasons.append(f"mean grey {f['mean_gray']:.0f} > {r['max_mean_gray']}: quaker/shell")
        if f.get("n_dark_spots", 0) > r["max_n_dark_spots"]:
            reasons.append(f"{int(f['n_dark_spots'])} dark spots > {r['max_n_dark_spots']}: insect/mould")
        p = min(1.0, 0.45 * len(reasons)) if reasons else 0.05
        suspect = p >= self.cfg.classifier.suspect_threshold
        ms = (now() - t0) * 1000
        self.n += 1
        self.ms_total += ms
        return Verdict(label="defect" if suspect else "good", p_defect=p, suspect=suspect, reason="; ".join(reasons) or "within all rules", ms=ms, classifier=self.name)

    def status(self) -> dict:
        return {"name": self.name, "classes": ["good", "defect"], "rules": self.cfg.classifier.rules, "threshold": self.cfg.classifier.suspect_threshold, "n_classified": self.n, "mean_ms": round(self.ms_total / self.n, 2) if self.n else 0.0, "ok": True}

    def selftest(self) -> list[Check]:
        good = Blob(0, 0, (0, 0, 1, 1), 1, False, {"major_mm": 11, "minor_mm": 7, "aspect": 1.5, "dark_frac": 0.05, "mean_gray": 90, "n_dark_spots": 0})
        burnt = Blob(0, 0, (0, 0, 1, 1), 1, False, {"major_mm": 11, "minor_mm": 7, "aspect": 1.5, "dark_frac": 0.8, "mean_gray": 25, "n_dark_spots": 0})
        f = Frame(np.zeros((2, 2, 3), np.uint8), now(), 0, "t")
        vg, vb = self.classify(f, good), self.classify(f, burnt)
        return [Check("classifier.good_passes", not vg.suspect, vg.reason), Check("classifier.burnt_rejected", vb.suspect, vb.reason)]

    def close(self) -> None:
        return None
