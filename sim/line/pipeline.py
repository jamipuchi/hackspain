"""The closed loop: frames → blobs → one verdict per bean → gate pulse. Owner: integrator.

`SortingLine` runs a background thread at camera rate. It always produces an annotated preview frame for the
panel; it only acts (classify + gate) while `enabled` is True, and it only *sends* gate commands while
`gate.dry_run` is False. `step(frame)` processes one frame synchronously so tests need no threads.

State per bean: armed → tracking (blob seen, still partial) → decided (verdict issued, gate scheduled if suspect)
→ armed again when the blob leaves the ROI (or after `lost_after_s` without a blob → 'bean_lost').
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np

from line.config import LineConfig
from line.contracts import Blob, Frame, LineEvent, ROI, Verdict, now

ROOT = Path(__file__).resolve().parent
DATASETS = ROOT / "datasets" / "beans"


def flow_fraction(blob: Blob, roi: ROI, flow_axis: str) -> float:
    """0 at the ROI edge where beans enter, 1 where they leave, along cfg.camera.flow_axis."""
    if flow_axis.endswith("x"):
        f = (blob.u - roi.x0) / max(roi.w, 1)
    else:
        f = (blob.v - roi.y0) / max(roi.h, 1)
    if flow_axis.startswith("-"):
        f = 1.0 - f
    return float(min(1.0, max(0.0, f)))


def fallback_gate_schedule(frac: float, cfg: LineConfig) -> tuple[float, float]:
    """(delay_s, dwell_s) until the door must be open for a bean now at `frac` of the zone. Replaced by line.timing.gate_schedule when present."""
    t = cfg.timing
    z0, z1 = t.zone_from_top_cm
    pos_cm = z0 + frac * (z1 - z0)
    delay = (t.door_from_top_cm[0] - pos_cm) / max(t.bean_speed_cm_s, 1e-6) - t.door_lead_s
    return max(0.0, delay), cfg.gate.default_dwell_s


def gate_schedule(frac: float, cfg: LineConfig) -> tuple[float, float]:
    if getattr(cfg.timing, "mode", "fixed") == "fixed":
        return max(0.0, float(cfg.timing.fixed_delay_s)), float(cfg.gate.default_dwell_s)
    try:
        from line import timing  # coffee-sim agent's module, optional

        if hasattr(timing, "gate_schedule"):
            return timing.gate_schedule(frac, cfg)
    except Exception:  # noqa: BLE001 - a broken optional module must not stop the line
        pass
    return fallback_gate_schedule(frac, cfg)


class SortingLine:
    name = "line"

    def __init__(self, cfg: LineConfig, camera, detector, classifier, gate, arduino, log=None):
        self.cfg = cfg
        self.camera, self.detector, self.classifier, self.gate, self.arduino = camera, detector, classifier, gate, arduino
        self.log = log or (lambda s: None)
        self.enabled = False  # act on verdicts (classify + schedule gate)
        self.state = "armed"
        self.counters = {"beans": 0, "good": 0, "suspect": 0, "gate_pulses": 0, "lost": 0, "errors": 0, "frames": 0}
        self.events: deque[LineEvent] = deque(maxlen=600)
        self.last_frame: Frame | None = None
        self.last_blobs: list[Blob] = []
        self.last_verdict: Verdict | None = None
        self.last_blob: Blob | None = None
        self.track_started = 0.0
        self.track_frac = 0.0  # where along the zone the tracked bean was last seen (0 enter … 1 leave)
        self.last_seen = 0.0
        self.lost_after_s = 0.4
        self.fps = 0.0
        self.loop_ms = 0.0
        self._jpeg: bytes | None = None
        self._jpeg_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ------------------------------------------------------------ events
    def event(self, kind: str, **data) -> None:
        self.events.append(LineEvent(now(), kind, data))
        self.log(f"[line] {kind} {json.dumps(data, default=str)[:200]}")

    @property
    def roi(self) -> ROI:
        return ROI(*[int(v) for v in self.cfg.camera.zone])

    # ------------------------------------------------------------ one frame
    def step(self, frame: Frame) -> Verdict | None:
        t0 = now()
        roi = self.roi
        try:
            blobs = self.detector.detect(frame, roi)
        except Exception as exc:  # noqa: BLE001
            self.counters["errors"] += 1
            self.event("error", where="detector", error=str(exc))
            blobs = []
        self.last_frame, self.last_blobs = frame, blobs
        self.counters["frames"] += 1
        verdict: Verdict | None = None
        best = self._pick(blobs, roi)
        if best is not None:
            self.last_seen = frame.t
            self.track_frac = flow_fraction(best, roi, self.cfg.camera.flow_axis)
            if self.state == "armed":
                self.state = "tracking"
                self.track_started = frame.t
                self.counters["beans"] += 1
                self.event("bean_seen", u=round(best.u), v=round(best.v), partial=best.partial)
            if self.state == "tracking" and not best.partial and self.enabled and self.track_frac >= float(getattr(self.cfg.camera, "trigger_frac", 0.0)):
                verdict = self._decide(frame, best, roi)
        elif self.state != "armed" and frame.t - self.last_seen > float(getattr(self.cfg, "lost_after_s", self.lost_after_s)):
            if self.state == "tracking":
                self.counters["lost"] += 1
                self.event("bean_lost", after_s=round(frame.t - self.track_started, 2))
            self.state = "armed"
        self.loop_ms = (now() - t0) * 1000
        self._render(frame, blobs, best)
        return verdict

    def _pick(self, blobs: list[Blob], roi: ROI) -> Blob | None:
        """Which blob is *the* bean this frame. Beans only move forward along the zone, so while tracking one we follow
        the blob nearest its last position (never one far behind it). When it has left and another blob is behind,
        that is a new bean: re-arm so it gets its own verdict."""
        if not blobs:
            return None
        axis = self.cfg.camera.flow_axis
        fr = {id(b): flow_fraction(b, roi, axis) for b in blobs}
        if self.state == "armed":
            whole = [b for b in blobs if not b.partial] or blobs  # never adopt a bean that is already half out of the zone
            return max(whole, key=lambda b: (fr[id(b)], b.area_px))  # the front-most bean first
        ahead = [b for b in blobs if fr[id(b)] >= self.track_frac - 0.15]
        if ahead:
            return min(ahead, key=lambda b: abs(fr[id(b)] - self.track_frac))
        # tracked bean is gone (left the zone between frames); the remaining blob(s) are newer beans
        if self.state == "tracking":
            self.counters["lost"] += 1
            self.event("bean_lost", after_s=0.0, note="left the zone before a full view")
        self.state = "armed"
        return max(blobs, key=lambda b: (fr[id(b)], b.area_px))

    def _decide(self, frame: Frame, blob: Blob, roi: ROI) -> Verdict:
        try:
            verdict = self.classifier.classify(frame, blob)
        except Exception as exc:  # noqa: BLE001
            self.counters["errors"] += 1
            self.event("error", where="classifier", error=str(exc))
            verdict = Verdict("unknown", 1.0, True, f"classifier error: {exc}", 0.0, "none")
        self.last_verdict, self.last_blob = verdict, blob
        self.state = "decided"
        frac = flow_fraction(blob, roi, self.cfg.camera.flow_axis)
        self.event("verdict", label=verdict.label, p=round(verdict.p_defect, 2), suspect=verdict.suspect, reason=verdict.reason, ms=round(verdict.ms, 1), frac=round(frac, 2))
        if verdict.suspect:
            self.counters["suspect"] += 1
        else:
            self.counters["good"] += 1
        if verdict.suspect or getattr(self.cfg, "act_on", "suspect") == "all":
            delay, dwell = gate_schedule(frac, self.cfg)
            self.gate.pulse(delay, dwell)
            self.counters["gate_pulses"] += 1
            self.event("gate_open", in_s=round(delay, 3), dwell_s=round(dwell, 2), dry_run=getattr(self.gate, "dry_run", True), because="every bean" if not verdict.suspect else "suspect")
        return verdict

    # ------------------------------------------------------------ preview
    def _render(self, frame: Frame, blobs: list[Blob], best: Blob | None) -> None:
        img = frame.bgr.copy()
        r = self.roi
        cv2.rectangle(img, (r.x0, r.y0), (r.x1, r.y1), (0, 200, 255), 2)
        for b in blobs:
            x, y, w, h = b.bbox
            col = (120, 120, 120) if b.partial else ((60, 60, 230) if (b is best and self.last_verdict and self.state == "decided" and self.last_verdict.suspect) else (60, 200, 60))
            cv2.rectangle(img, (x, y), (x + w, y + h), col, 2)
            cv2.putText(img, f"{b.features.get('major_mm', 0):.1f}mm", (x, max(12, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1, cv2.LINE_AA)
        tag = f"{self.state} · {'SORTING' if self.enabled else 'preview'} · {'DRY RUN' if getattr(self.gate, 'dry_run', True) else 'LIVE GATE'} · {self.fps:.0f} fps · {self.loop_ms:.0f} ms"
        cv2.putText(img, tag, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(img, tag, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
        if self.last_verdict is not None and self.state == "decided":
            v = self.last_verdict
            txt = f"{v.label} p={v.p_defect:.2f} — {v.reason[:70]}"
            cv2.putText(img, txt, (10, img.shape[0] - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(img, txt, (10, img.shape[0] - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (80, 80, 255) if v.suspect else (80, 220, 80), 1, cv2.LINE_AA)
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
        if ok:
            with self._jpeg_lock:
                self._jpeg = buf.tobytes()

    def latest_jpeg(self) -> bytes | None:
        with self._jpeg_lock:
            return self._jpeg

    # ------------------------------------------------------------ manual tools for the panel
    def classify_now(self) -> list[dict]:
        """Detect + classify every full blob in the current frame without touching the gate."""
        frame = self.camera.grab()
        out = []
        for b in self.detector.detect(frame, self.roi):
            v = self.classifier.classify(frame, b)
            out.append({"blob": {"u": round(b.u), "v": round(b.v), "partial": b.partial, "features": {k: round(x, 3) for k, x in b.features.items()}}, "verdict": asdict(v)})
        self.last_frame = frame
        return out

    def save_sample(self, label: str) -> dict:
        """Crop the current best blob and store it with its features under datasets/beans/<label>/."""
        frame = self.last_frame or self.camera.grab()
        blobs = self.detector.detect(frame, self.roi)
        if not blobs:
            raise RuntimeError("no bean in the zone")
        b = max(blobs, key=lambda x: x.area_px)
        x, y, w, h = b.bbox
        pad = 12
        crop = frame.bgr[max(0, y - pad) : y + h + pad, max(0, x - pad) : x + w + pad]
        d = DATASETS / label
        d.mkdir(parents=True, exist_ok=True)
        stem = time.strftime("%Y%m%d-%H%M%S") + f"-{int((now() * 1000) % 1000):03d}"
        cv2.imwrite(str(d / f"{stem}.png"), crop)
        meta = {"label": label, "features": b.features, "bbox": list(b.bbox), "source": frame.source, "t": time.time(), "verdict": asdict(self.last_verdict) if self.last_verdict else None}
        (d / f"{stem}.json").write_text(json.dumps(meta, indent=1))
        self.event("note", text=f"saved sample {label}/{stem}")
        return {"path": str(d / f"{stem}.png"), "features": b.features}

    def dataset_counts(self) -> dict:
        return {p.name: len(list(p.glob("*.json"))) for p in DATASETS.glob("*") if p.is_dir()} if DATASETS.exists() else {}

    # ------------------------------------------------------------ thread
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="line", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        times: deque[float] = deque(maxlen=60)
        while not self._stop.is_set():
            try:
                frame = self.camera.grab()
                self.step(frame)
                times.append(now())
                if len(times) > 2:
                    self.fps = (len(times) - 1) / max(times[-1] - times[0], 1e-6)
            except Exception as exc:  # noqa: BLE001
                self.counters["errors"] += 1
                self.event("error", where="loop", error=str(exc))
                time.sleep(0.2)
            time.sleep(0.001)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def reset(self) -> None:
        for k in self.counters:
            self.counters[k] = 0
        self.events.clear()
        self.state = "armed"
        self.last_verdict = None

    def status(self) -> dict:
        return {"name": self.name, "state": self.state, "enabled": self.enabled, "dry_run": getattr(self.gate, "dry_run", True), "fps": round(self.fps, 1), "loop_ms": round(self.loop_ms, 1),
                "counters": dict(self.counters), "zone": list(self.cfg.camera.zone), "last_verdict": asdict(self.last_verdict) if self.last_verdict else None, "datasets": self.dataset_counts(), "ok": self._thread.is_alive() if self._thread else False}
