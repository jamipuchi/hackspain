"""Frame sources for the line: the real iPhone, a file replay and a synthetic bean chute.

Owner: camera agent. Implements `contracts.FrameSource`.

    RealCamera(cfg)          iPhone (or any camera) via AVFoundation, resolved BY NAME, background reader
    FileCamera(paths, fps)   replays a folder of images, a list of images or a video (loops)
    SyntheticCamera(cfg)     dark ellipse "beans" rolling across white paper along cfg.camera.flow_axis
    make_frame_source(cfg)   picks one from cfg.camera.backend ('real' | 'file' | 'synthetic')

Camera indices: OpenCV's AVFoundation backend numbers external / Continuity cameras first and the
built-in camera after, the reverse of `ffmpeg -list_devices` and `system_profiler`. On the MacBook the
iPhone is OpenCV index 0 and the Mac camera 1. Never hard-code an index: `list_cameras()` returns the
OpenCV order via the Swift helper in ../demos/avf_cameras.swift (compiled on first use).
"""
from __future__ import annotations

import glob
import subprocess
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from line.config import LineConfig, load as load_config
from line.contracts import Check, Frame, now

ROOT = Path(__file__).resolve().parents[1]
_HELPER_SRC = ROOT / "demos" / "avf_cameras.swift"
_HELPER_BIN = ROOT / "demos" / "avf_cameras"
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
_VIDEO_EXT = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}


# ----------------------------------------------------------------------------- device discovery
def list_cameras() -> list[tuple[int, str]]:
    """[(opencv_index, name)] in the order OpenCV indexes them (external first).

    Falls back to `system_profiler` order (built-in first) when swiftc is unavailable; that order is
    wrong for OpenCV on a MacBook with a phone attached, so the fallback is only for name matching.
    """
    try:
        if _HELPER_SRC.exists() and (not _HELPER_BIN.exists() or _HELPER_BIN.stat().st_mtime < _HELPER_SRC.stat().st_mtime):
            subprocess.run(["swiftc", "-O", str(_HELPER_SRC), "-o", str(_HELPER_BIN)], check=True, capture_output=True, timeout=180)
        out = subprocess.run([str(_HELPER_BIN)], capture_output=True, text=True, check=True, timeout=20).stdout
        return [(int(parts[0]), parts[1]) for parts in (l.split("\t") for l in out.splitlines()) if len(parts) >= 2]
    except Exception:  # noqa: BLE001 - fall through to the slow, differently ordered listing
        pass
    try:
        out = subprocess.run(["system_profiler", "SPCameraDataType"], capture_output=True, text=True, timeout=20).stdout
        names = [l.strip()[:-1] for l in out.splitlines() if l.strip().endswith(":") and "Camera" in l]
        return list(enumerate(names))
    except Exception:  # noqa: BLE001
        return []


def resolve_camera(match: str, devices: list[tuple[int, str]] | None = None) -> tuple[int, str]:
    """First camera whose name contains `match` (case-insensitive), skipping Desk View variants."""
    devices = list_cameras() if devices is None else devices
    m = match.lower()
    for idx, name in devices:
        n = name.lower()
        if m in n and "desk view" not in n:
            return idx, name
    listing = ", ".join(f"[{i}] {n}" for i, n in devices) or "none"
    raise LookupError(f"no camera matching {match!r} (phone unlocked and plugged in?). cameras: {listing}")


# ----------------------------------------------------------------------------- shared reader
class _LatestFrameReader(threading.Thread):
    """Pulls frames from a cv2.VideoCapture as fast as it delivers them and keeps only the newest."""

    def __init__(self, cap: cv2.VideoCapture, source: str, fps_hint: float):
        super().__init__(name=f"reader:{source}", daemon=True)
        self.cap, self.source = cap, source
        self.period = 1.0 / max(fps_hint, 1.0)
        self.cond = threading.Condition()
        self.latest: Frame | None = None
        self.consumed = True
        self.seq = 0
        self.dropped = 0
        self.read_errors = 0
        self.fps = 0.0
        self._last_t: float | None = None
        self._stop_evt = threading.Event()  # not `_stop`: that shadows Thread._stop()

    def run(self) -> None:
        while not self._stop_evt.is_set():
            ok, bgr = self.cap.read()
            t = now()
            if not ok or bgr is None:
                self.read_errors += 1
                time.sleep(0.005)
                continue
            with self.cond:
                if self.latest is not None and not self.consumed:
                    self.dropped += 1
                self.latest = Frame(bgr=bgr, t=t, seq=self.seq, source=self.source)
                self.seq += 1
                self.consumed = False
                if self._last_t is not None:
                    dt = max(t - self._last_t, 1e-4)
                    inst = 1.0 / dt
                    self.fps = inst if self.fps == 0.0 else 0.9 * self.fps + 0.1 * inst
                self._last_t = t
                self.cond.notify_all()

    def grab(self, wait_s: float | None = None) -> Frame:
        """Newest frame. Waits at most one frame period for a fresh one, then returns whatever is newest."""
        with self.cond:
            if self.consumed:
                self.cond.wait(timeout=self.period if wait_s is None else wait_s)
            if self.latest is None:
                raise RuntimeError(f"{self.source}: no frame captured yet")
            self.consumed = True
            return self.latest

    def wait_first(self, timeout_s: float) -> bool:
        with self.cond:
            if self.latest is None:
                self.cond.wait(timeout=timeout_s)
            return self.latest is not None

    def stop(self) -> None:
        self._stop_evt.set()


# ----------------------------------------------------------------------------- real camera
class RealCamera:
    """iPhone via Continuity Camera (or any AVFoundation camera), picked by name, background reader.

    `RealCamera(cfg)` reads cfg.camera (match, width, height, fps); keyword overrides win.
    """

    def __init__(self, cfg: LineConfig | None = None, *, match: str | None = None, width: int | None = None,
                 height: int | None = None, fps: float | None = None, open_timeout_s: float = 4.0):
        cam = (cfg or load_config()).camera
        self.match = match or cam.match
        self.req_w, self.req_h, self.req_fps = width or cam.width, height or cam.height, fps or cam.fps
        self.name = f"real:{self.match}"
        self.device_index: int | None = None
        self.device_name = ""
        self.error = ""
        self._reader: _LatestFrameReader | None = None
        self._cap: cv2.VideoCapture | None = None
        self._grab_latency_ms = 0.0
        self._grabs = 0
        self._lock = threading.Lock()
        self._open_timeout_s = open_timeout_s
        self._open()

    # -- lifecycle
    def _open(self) -> None:
        try:
            self.device_index, self.device_name = resolve_camera(self.match)
        except LookupError as exc:
            self.error = str(exc)
            return
        cap = cv2.VideoCapture(self.device_index, cv2.CAP_AVFOUNDATION)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.req_w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.req_h)
        cap.set(cv2.CAP_PROP_FPS, self.req_fps)
        if not cap.isOpened():
            self.error = f"could not open {self.device_name} (index {self.device_index}); camera permission for the terminal?"
            cap.release()
            return
        self._cap = cap
        self.name = self.device_name
        self._reader = _LatestFrameReader(cap, self.device_name, self.req_fps)
        self._reader.start()
        if not self._reader.wait_first(self._open_timeout_s):
            self.error = f"{self.device_name} opened but delivered no frame in {self._open_timeout_s:.0f} s"

    @property
    def ok(self) -> bool:
        return self._reader is not None and self._reader.latest is not None and not self.error

    # -- FrameSource
    def grab(self) -> Frame:
        if self._reader is None:
            raise RuntimeError(self.error or "camera not open")
        f = self._reader.grab()
        with self._lock:
            self._grab_latency_ms = 0.8 * self._grab_latency_ms + 0.2 * (now() - f.t) * 1000.0 if self._grabs else (now() - f.t) * 1000.0
            self._grabs += 1
        return f

    def resolution(self) -> tuple[int, int]:
        r = self._reader
        if r is not None and r.latest is not None:
            h, w = r.latest.bgr.shape[:2]
            return w, h
        if self._cap is not None:
            return int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        return 0, 0

    # -- Module
    def status(self) -> dict:
        r = self._reader
        w, h = self.resolution()
        return {
            "name": self.name, "backend": "real", "match": self.match, "device_index": self.device_index,
            "resolution": [w, h], "requested": [self.req_w, self.req_h, self.req_fps],
            "fps_measured": round(r.fps, 1) if r else 0.0,
            "grab_latency_ms": round(self._grab_latency_ms, 1),
            "frames": r.seq if r else 0, "dropped": r.dropped if r else 0, "read_errors": r.read_errors if r else 0,
            "grabs": self._grabs, "running": bool(r and r.is_alive()), "ok": self.ok, "error": self.error,
        }

    def selftest(self) -> list[Check]:
        checks: list[Check] = []
        t0 = now()
        try:
            idx, name = resolve_camera(self.match)
            checks.append(Check("device found by name", True, f"[{idx}] {name}", (now() - t0) * 1000))
        except LookupError as exc:
            checks.append(Check("device found by name", False, str(exc), (now() - t0) * 1000))
            return checks
        if not self.ok:
            checks.append(Check("camera open", False, self.error or "no frames"))
            return checks
        checks.append(Check("camera open", True, f"index {self.device_index}"))
        t0 = now()
        seqs = []
        try:
            last = -1
            while len(seqs) < 10 and now() - t0 < 2.0:
                f = self.grab()
                if f.seq != last:
                    seqs.append(f.seq)
                    last = f.seq
            ms = (now() - t0) * 1000
            checks.append(Check("10 frames in < 1 s", len(seqs) >= 10 and ms < 1000, f"{len(seqs)} frames in {ms:.0f} ms, {self._reader.fps:.1f} fps", ms))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("10 frames in < 1 s", False, repr(exc), (now() - t0) * 1000))
        w, h = self.resolution()
        checks.append(Check("resolution", w > 0 and h > 0, f"{w}x{h} (requested {self.req_w}x{self.req_h}@{self.req_fps})"))
        checks.append(Check("grab latency", self._grab_latency_ms < 100, f"{self._grab_latency_ms:.1f} ms"))
        return checks

    def close(self) -> None:
        if self._reader is not None:
            self._reader.stop()
            self._reader.join(timeout=2.0)
            self._reader = None
        if self._cap is not None:
            self._cap.release()
            self._cap = None


# ----------------------------------------------------------------------------- file replay
class FileCamera:
    """Replays images (folder, glob, list) or a video at `fps`. Loops by default.

    realtime=False (tests): each grab() returns the next frame immediately with t advancing by 1/fps.
    realtime=True (panel): grab() paces itself to `fps` on the monotonic clock.
    """

    def __init__(self, paths, fps: float = 30.0, *, loop: bool = True, realtime: bool = False, name: str = "file"):
        self.fps, self.loop, self.realtime, self.name = float(fps), loop, realtime, name
        self.period = 1.0 / max(self.fps, 1e-3)
        self._images: list[Path] = []
        self._video: cv2.VideoCapture | None = None
        self._video_path: Path | None = None
        self._pos = 0
        self.seq = 0
        self.loops = 0
        self.error = ""
        self._t = now()
        self._last_t: float | None = None
        self._resolution = (0, 0)
        self._grab_latency_ms = 0.0
        if isinstance(paths, (str, Path)):
            paths = [paths]
        for p in paths:
            p = Path(p).expanduser()
            if p.is_dir():
                self._images += sorted(q for q in p.iterdir() if q.suffix.lower() in _IMAGE_EXT)
            elif p.suffix.lower() in _VIDEO_EXT:
                self._video_path = p
            elif any(ch in str(p) for ch in "*?["):
                self._images += sorted(Path(q) for q in glob.glob(str(p)) if Path(q).suffix.lower() in _IMAGE_EXT)
            elif p.suffix.lower() in _IMAGE_EXT:
                self._images.append(p)
        if self._video_path is not None:
            self._video = cv2.VideoCapture(str(self._video_path))
            if not self._video.isOpened():
                self.error = f"cannot open video {self._video_path}"
        elif not self._images:
            self.error = f"no images found in {list(map(str, paths))}"

    def __len__(self) -> int:
        if self._video is not None:
            return int(self._video.get(cv2.CAP_PROP_FRAME_COUNT))
        return len(self._images)

    def _next_bgr(self) -> np.ndarray:
        if self._video is not None:
            ok, bgr = self._video.read()
            if not ok:
                if not self.loop:
                    raise StopIteration
                self._video.set(cv2.CAP_PROP_POS_FRAMES, 0)
                self.loops += 1
                ok, bgr = self._video.read()
                if not ok:
                    raise RuntimeError(f"video {self._video_path} has no frames")
            return bgr
        if not self._images:
            raise RuntimeError(self.error)
        if self._pos >= len(self._images):
            if not self.loop:
                raise StopIteration
            self._pos = 0
            self.loops += 1
        bgr = cv2.imread(str(self._images[self._pos]), cv2.IMREAD_COLOR)
        self._pos += 1
        if bgr is None:
            raise RuntimeError(f"unreadable image {self._images[self._pos - 1]}")
        return bgr

    def grab(self) -> Frame:
        if self.realtime:
            if self._last_t is not None:
                due = self._last_t + self.period
                dt = due - now()
                if dt > 0:
                    time.sleep(dt)
            t = now()
            self._last_t = t
        else:
            t = self._t
            self._t += self.period
        bgr = self._next_bgr()
        self._resolution = (bgr.shape[1], bgr.shape[0])
        f = Frame(bgr=bgr, t=t, seq=self.seq, source=self.name)
        self.seq += 1
        return f

    def status(self) -> dict:
        return {"name": self.name, "backend": "file", "resolution": list(self._resolution), "fps_measured": self.fps,
                "grab_latency_ms": 0.0, "frames": self.seq, "dropped": 0, "n_files": len(self), "loops": self.loops,
                "position": self._pos, "ok": not self.error, "error": self.error}

    def selftest(self) -> list[Check]:
        if self.error:
            return [Check("source readable", False, self.error)]
        t0 = now()
        try:
            f = self.grab()
            return [Check("source readable", True, f"{len(self)} frames, first {f.bgr.shape[1]}x{f.bgr.shape[0]}", (now() - t0) * 1000)]
        except Exception as exc:  # noqa: BLE001
            return [Check("source readable", False, repr(exc), (now() - t0) * 1000)]

    def close(self) -> None:
        if self._video is not None:
            self._video.release()
            self._video = None


# ----------------------------------------------------------------------------- synthetic chute
class SyntheticCamera:
    """Dark ellipse beans rolling across white paper along cfg.camera.flow_axis. Deterministic per seed.

    Beans spawn every `spawn_every_s` at the upstream edge with jittered size, shade and lateral offset and
    move at `speed_px_s`. Every `defect_every`-th bean is a "defect": darker with two black spots. `truth()`
    gives the beans currently in view for tests. Frames are the same size as cfg.camera.width x height.
    """

    def __init__(self, cfg: LineConfig | None = None, *, width: int | None = None, height: int | None = None,
                 fps: float | None = None, seed: int = 0, speed_px_s: float | None = None, spawn_every_s: float = 0.6,
                 defect_every: int = 3, bean_major_mm: float = 11.0, realtime: bool = False, name: str = "synthetic"):
        cfg = cfg or load_config()
        cam = cfg.camera
        self.w, self.h = width or cam.width, height or cam.height
        self.fps = float(fps or cam.fps)
        self.period = 1.0 / self.fps
        self.axis = cam.flow_axis if cam.flow_axis in ("x", "+x", "-x", "y", "+y", "-y") else "x"
        self.px_per_mm = cam.px_per_mm
        self.speed = speed_px_s if speed_px_s is not None else cfg.timing.bean_speed_cm_s * 10.0 * cam.px_per_mm
        self.spawn_every_s, self.defect_every = spawn_every_s, max(defect_every, 1)
        self.bean_major_px = bean_major_mm * cam.px_per_mm
        self.realtime, self.name, self.seed = realtime, name, seed
        self.rng = np.random.default_rng(seed)
        self.zone = list(cam.zone)
        self.seq = 0
        self._t0 = now()
        self._sim_t = 0.0
        self._last_real: float | None = None
        self._next_spawn = 0.1  # first bean early so short tests see one
        self._n_spawned = 0
        self._beans: list[dict] = []
        # static paper texture (seeded) so consecutive frames differ only by the beans
        paper = np.full((self.h, self.w, 3), (236, 238, 240), np.uint8)
        noise = self.rng.normal(0, 3, (self.h, self.w, 1)).astype(np.int16)
        self._paper = np.clip(paper.astype(np.int16) + noise, 0, 255).astype(np.uint8)

    # -- geometry helpers
    def _span(self) -> int:
        return self.w if self.axis.endswith("x") else self.h

    def _forward(self) -> int:
        return -1 if self.axis.startswith("-") else 1

    def _spawn(self) -> None:
        major = self.bean_major_px * self.rng.uniform(0.85, 1.15)
        minor = major / self.rng.uniform(1.35, 1.75)
        lateral_span = self.h if self.axis.endswith("x") else self.w
        z0, z1 = (self.zone[1], self.zone[3]) if self.axis.endswith("x") else (self.zone[0], self.zone[2])
        lateral = self.rng.uniform(z0 + major / 2, z1 - major / 2) if z1 - z0 > major else lateral_span / 2
        defect = (self._n_spawned % self.defect_every) == self.defect_every - 1
        gray = self.rng.uniform(28, 42) if defect else self.rng.uniform(70, 105)
        color = (int(gray * 0.75), int(gray * 0.9), int(gray * 1.25))  # BGR: brownish
        start = -major if self._forward() > 0 else self._span() + major
        self._beans.append({"id": self._n_spawned, "s": start, "lat": lateral, "major": major, "minor": minor,
                            "color": color, "defect": defect, "angle": self.rng.uniform(-12, 12), "spin": self.rng.uniform(-40, 40)})
        self._n_spawned += 1

    def _advance(self, dt: float) -> None:
        self._sim_t += dt
        while self._sim_t >= self._next_spawn:
            self._spawn()
            self._next_spawn += self.spawn_every_s
        for b in self._beans:
            b["s"] += self._forward() * self.speed * dt
            b["angle"] += b["spin"] * dt
        span = self._span()
        self._beans = [b for b in self._beans if -b["major"] * 1.5 < b["s"] < span + b["major"] * 1.5]

    def _center(self, b: dict) -> tuple[float, float]:
        return (b["s"], b["lat"]) if self.axis.endswith("x") else (b["lat"], b["s"])

    def render(self) -> np.ndarray:
        img = self._paper.copy()
        along_x = self.axis.endswith("x")
        for b in self._beans:
            cx, cy = self._center(b)
            axes = (int(b["major"] / 2), int(b["minor"] / 2))
            ang = b["angle"] + (0 if along_x else 90)
            cv2.ellipse(img, (int(round(cx)), int(round(cy))), axes, ang, 0, 360, b["color"], -1, cv2.LINE_AA)
            # a lighter centre crease like a real bean, then defect spots
            cv2.ellipse(img, (int(round(cx)), int(round(cy))), (max(axes[0] - 3, 1), max(axes[1] // 4, 1)), ang, 0, 360,
                        tuple(min(c + 25, 255) for c in b["color"]), -1, cv2.LINE_AA)
            if b["defect"]:
                r = max(int(b["minor"] / 6), 2)
                for k in (-0.4, 0.35):
                    dx, dy = k * b["major"] / 2, 0.15 * b["minor"] * (1 if k > 0 else -1)
                    if not along_x:
                        dx, dy = dy, dx
                    cv2.circle(img, (int(round(cx + dx)), int(round(cy + dy))), r, (12, 12, 14), -1, cv2.LINE_AA)
        return img

    def truth(self) -> list[dict]:
        out = []
        for b in self._beans:
            cx, cy = self._center(b)
            out.append({"id": b["id"], "u": cx, "v": cy, "major_px": b["major"], "minor_px": b["minor"], "defect": b["defect"]})
        return out

    # -- FrameSource
    def grab(self) -> Frame:
        if self.realtime:
            if self._last_real is not None:
                dt_due = self._last_real + self.period - now()
                if dt_due > 0:
                    time.sleep(dt_due)
            t = now()
            dt = self.period if self._last_real is None else t - self._last_real
            self._last_real = t
        else:
            dt = self.period
            t = self._t0 + self._sim_t + dt
        self._advance(dt)
        f = Frame(bgr=self.render(), t=t, seq=self.seq, source=self.name)
        self.seq += 1
        return f

    def status(self) -> dict:
        return {"name": self.name, "backend": "synthetic", "resolution": [self.w, self.h], "fps_measured": self.fps,
                "grab_latency_ms": 0.0, "frames": self.seq, "dropped": 0, "beans_in_view": len(self._beans),
                "beans_spawned": self._n_spawned, "speed_px_s": round(self.speed, 1), "flow_axis": self.axis, "seed": self.seed,
                "ok": True, "error": ""}

    def selftest(self) -> list[Check]:
        t0 = now()
        f = self.grab()
        ok = f.bgr.shape == (self.h, self.w, 3)
        return [Check("renders frame", ok, f"{self.w}x{self.h}, {len(self._beans)} beans in view", (now() - t0) * 1000)]

    def close(self) -> None:
        pass


# ----------------------------------------------------------------------------- factory
def make_frame_source(cfg: LineConfig | None = None, backend: str | None = None):
    cfg = cfg or load_config()
    backend = backend or cfg.camera.backend
    if backend == "real":
        return RealCamera(cfg)
    if backend == "file":
        return FileCamera(cfg.camera.file_paths, fps=cfg.camera.fps, realtime=True)
    if backend == "synthetic":
        return SyntheticCamera(cfg, realtime=True)
    raise ValueError(f"unknown camera backend {backend!r}")


if __name__ == "__main__":  # quick manual check: python -m line.camera_source [match]
    cam = RealCamera(match=sys.argv[1] if len(sys.argv) > 1 else None)
    for c in cam.selftest():
        print(("OK  " if c.ok else "FAIL"), c.name, "-", c.detail)
    print(cam.status())
    cam.close()
