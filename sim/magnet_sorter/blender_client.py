"""Client for blender/render_server.py: start Blender once, submit render requests, wait."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import cv2
import mujoco
import numpy as np

import scene_def as sd

BLENDER = os.environ.get("BLENDER", "/Applications/Blender.app/Contents/MacOS/Blender")
SERVER = sd.ROOT / "blender" / "render_server.py"


def body_poses(model: mujoco.MjModel, data: mujoco.MjData) -> dict[str, list[float]]:
    out = {}
    for i in range(1, model.nbody):
        name = model.body(i).name
        out[name] = [*data.xpos[i].tolist(), *data.xquat[i].tolist()]
    return out


def pose_array(model: mujoco.MjModel, data: mujoco.MjData, names: list[str]) -> np.ndarray:
    arr = np.zeros((len(names), 7))
    for k, name in enumerate(names):
        bid = model.body(name).id
        arr[k, :3] = data.xpos[bid]
        arr[k, 3:] = data.xquat[bid]
    return arr


class BlenderRenderer:
    def __init__(self, samples: int = 96, engine: str = "CYCLES", queue_dir: Path | None = None, log_path: Path | None = None):
        self.queue = Path(queue_dir or tempfile.mkdtemp(prefix="magnet_render_"))
        self.queue.mkdir(parents=True, exist_ok=True)
        for p in self.queue.glob("*"):
            p.unlink()
        self.log = open(log_path or (self.queue / "blender.log"), "w")
        self.proc = subprocess.Popen(
            [BLENDER, "-b", "--python", str(SERVER), "--", str(self.queue), "--samples", str(samples), "--engine", engine],
            stdout=self.log,
            stderr=subprocess.STDOUT,
        )
        t0 = time.time()
        while not (self.queue / "READY").exists():
            if self.proc.poll() is not None:
                raise RuntimeError(f"Blender exited during scene build; see {self.log.name}")
            time.sleep(0.2)
        self.build_s = time.time() - t0

    def _submit(self, req: dict, timeout: float) -> dict:
        rid = f"{time.time():.3f}_{uuid.uuid4().hex[:6]}"
        path = self.queue / f"{rid}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(req))
        tmp.rename(path)
        t0 = time.time()
        while True:
            if path.with_suffix(".done").exists():
                res = json.loads(path.with_suffix(".done").read_text() or "{}")
                for p in (path, path.with_suffix(".done"), path.with_suffix(".progress")):
                    p.unlink(missing_ok=True)
                return res
            if path.with_suffix(".err").exists():
                raise RuntimeError("Blender render failed:\n" + path.with_suffix(".err").read_text())
            if self.proc.poll() is not None:
                raise RuntimeError(f"Blender died; see {self.log.name}")
            if time.time() - t0 > timeout:
                raise TimeoutError(f"render request {rid} timed out after {timeout}s")
            time.sleep(0.1)

    def render_frame(self, camera: str, poses: dict, out: Path, width: int, height: int, samples: int | None = None, timeout: float = 600) -> np.ndarray:
        self._submit({"type": "frame", "camera": camera, "poses": poses, "out": str(out), "width": width, "height": height, "samples": samples}, timeout)
        return cv2.imread(str(out), cv2.IMREAD_COLOR)

    def render_batch(self, camera: str, npz: Path, out_dir: Path, width: int, height: int, samples: int | None = None, start: int = 0, stop: int | None = None, every: int = 1, timeout: float = 6 * 3600) -> dict:
        return self._submit({"type": "batch", "camera": camera, "npz": str(npz), "out_dir": str(out_dir), "width": width, "height": height, "samples": samples, "start": start, "stop": stop, "every": every}, timeout)

    def progress(self) -> dict | None:
        ps = sorted(self.queue.glob("*.progress"))
        if not ps:
            return None
        try:
            return json.loads(ps[-1].read_text())
        except json.JSONDecodeError:
            return None

    def close(self) -> None:
        try:
            self._submit({"type": "quit"}, timeout=30)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.log.close()


class BlenderPhoneCamera:
    """Phone camera whose frames come from Cycles. Grab renders the current MuJoCo state."""

    def __init__(self, renderer: BlenderRenderer, model, data, out_dir: Path, samples: int = 64, name: str = "A"):
        from camera import phone_look

        self.r, self.model, self.data = renderer, model, data
        self.name = name
        self.cam = sd.CAMERAS.get(name, sd.PHONE_CAM)
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.samples = samples
        self.n = 0
        self.phone_look = phone_look
        self.rng = np.random.default_rng(0)
        self.last_render_s = 0.0

    def grab(self) -> np.ndarray:
        self.n += 1
        out = self.out_dir / f"cam{self.name}_{self.n:03d}.png"
        t0 = time.time()
        img = self.r.render_frame(self.name if self.name in sd.CAMERAS else "phone", body_poses(self.model, self.data), out, self.cam["width"], self.cam["height"], self.samples)
        self.last_render_s = time.time() - t0
        return self.phone_look(img, self.rng)
