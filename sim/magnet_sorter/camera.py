"""Cameras and ArUco table calibration.

The pixel→table mapping is a homography fitted to the four ArUco markers glued to the MDF
board. It is computed from the image alone, so the same code works for the simulated phone
camera (Blender or MuJoCo render) and for a real iPhone via Continuity Camera.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np

import scene_def as sd

ARUCO_DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)


class TableCalibration:
    def __init__(self) -> None:
        self.H: np.ndarray | None = None  # pixel -> table (homogeneous)
        self.H_inv: np.ndarray | None = None
        self.found: dict[int, np.ndarray] = {}

    def fit(self, frame_bgr: np.ndarray) -> bool:
        detector = cv2.aruco.ArucoDetector(ARUCO_DICT, cv2.aruco.DetectorParameters())
        corners, ids, _ = detector.detectMarkers(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY))
        if ids is None:
            return False
        img_pts, tbl_pts = [], []
        half = sd.MARKER_SIZE / 2
        self.found = {}
        for c, mid in zip(corners, ids.flatten()):
            mid = int(mid)
            if mid not in sd.MARKERS:
                continue
            mx, my = sd.MARKERS[mid]
            px = c.reshape(4, 2)
            self.found[mid] = px
            # corner order from the detector is clockwise starting top-left in marker space.
            # In our texture placement marker-space x = table +x, marker-space y = table -y.
            tbl = np.array([[mx - half, my + half], [mx + half, my + half], [mx + half, my - half], [mx - half, my - half]])
            img_pts.append(px)
            tbl_pts.append(tbl)
        if len(img_pts) < 2:
            return False
        img = np.vstack(img_pts).astype(np.float64)
        tbl = np.vstack(tbl_pts).astype(np.float64)
        self.H, _ = cv2.findHomography(img, tbl, method=0)
        self.H_inv = np.linalg.inv(self.H)
        return self.H is not None

    def pixel_to_table(self, u: float, v: float) -> tuple[float, float]:
        if self.H is None:
            raise RuntimeError("camera not calibrated (no ArUco markers found)")
        p = self.H @ np.array([u, v, 1.0])
        return float(p[0] / p[2]), float(p[1] / p[2])

    def table_to_pixel(self, x: float, y: float) -> tuple[int, int]:
        p = self.H_inv @ np.array([x, y, 1.0])
        return int(round(p[0] / p[2])), int(round(p[1] / p[2]))

    def reprojection_error_mm(self) -> float:
        if not self.found:
            return float("nan")
        errs = []
        for mid, px in self.found.items():
            mx, my = sd.MARKERS[mid]
            cx, cy = px.mean(axis=0)
            tx, ty = self.pixel_to_table(cx, cy)
            errs.append(np.hypot(tx - mx, ty - my))
        return float(np.mean(errs) * 1000)


def phone_look(frame_bgr: np.ndarray, rng: np.random.Generator | None = None) -> np.ndarray:
    """Make a clean render look like a phone photo: mild blur, sensor noise, vignette, JPEG."""
    rng = rng or np.random.default_rng()
    img = frame_bgr.astype(np.float32)
    img = cv2.GaussianBlur(img, (0, 0), 0.7)
    h, w = img.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2)
    img *= (1 - 0.18 * r**2)[:, :, None]
    img += rng.normal(0, 2.2, img.shape).astype(np.float32)
    img = np.clip(img, 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR) if ok else img


class MujocoPhoneCamera:
    """Fast fallback: MuJoCo's own rasteriser for a phone view (camera name 'phone' = legacy top-down, or 'A'/'B')."""

    def __init__(self, model, data, name: str = "phone"):
        import mujoco

        self.model, self.data = model, data
        cam = sd.CAMERAS.get(name, sd.PHONE_CAM)
        self.cam_name = f"cam_{name}" if name in sd.CAMERAS else "phone"
        self.renderer = mujoco.Renderer(model, height=cam["height"], width=cam["width"])
        self.rng = np.random.default_rng(0)
        self.last_render_s = 0.0

    def grab(self) -> np.ndarray:
        self.renderer.update_scene(self.data, camera=self.cam_name)
        return phone_look(cv2.cvtColor(self.renderer.render(), cv2.COLOR_RGB2BGR), self.rng)


def draw_metric_grid(frame: np.ndarray, cal: "TableCalibration", step_cm: float = 2.0, label_every: int = 2) -> np.ndarray:
    """Project a table-frame grid (cm, origin at the arm base, x forward, y left) onto the photo."""
    base = frame.copy()
    out = frame.copy()  # grid is drawn here, then blended so lines never hide small parts
    h, w = out.shape[:2]
    bx, by = sd.BOARD["center"]
    hx, hy = sd.BOARD["size"][0] / 2, sd.BOARD["size"][1] / 2
    xs = np.arange(np.ceil((bx - hx) * 100 / step_cm) * step_cm, (bx + hx) * 100 + 0.01, step_cm)
    ys = np.arange(np.ceil((by - hy) * 100 / step_cm) * step_cm, (by + hy) * 100 + 0.01, step_cm)

    def px(x_cm, y_cm):
        u, v = cal.table_to_pixel(x_cm / 100.0, y_cm / 100.0)
        return int(u), int(v)

    def inside(p):
        return -50 <= p[0] < w + 50 and -50 <= p[1] < h + 50

    for x in xs:
        pts = [px(x, y) for y in np.linspace(ys[0], ys[-1], 24)]
        for a, b in zip(pts[:-1], pts[1:]):
            if inside(a) and inside(b):
                cv2.line(out, a, b, (255, 255, 255), 1, cv2.LINE_AA)
    for y in ys:
        pts = [px(x, y) for x in np.linspace(xs[0], xs[-1], 24)]
        for a, b in zip(pts[:-1], pts[1:]):
            if inside(a) and inside(b):
                cv2.line(out, a, b, (255, 255, 255), 1, cv2.LINE_AA)
    for x in xs[::label_every]:
        p = px(x, ys[-1])
        if inside(p):
            cv2.putText(out, f"x{x:+.0f}", (p[0] - 12, p[1] - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(out, f"x{x:+.0f}", (p[0] - 12, p[1] - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 0), 1, cv2.LINE_AA)
    for y in ys[::label_every]:
        p = px(xs[-1], y)
        if inside(p):
            cv2.putText(out, f"y{y:+.0f}", (p[0] + 4, p[1] + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(out, f"y{y:+.0f}", (p[0] + 4, p[1] + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 0), 1, cv2.LINE_AA)
    out = cv2.addWeighted(out, 0.45, base, 0.55, 0)  # semi-transparent grid
    o = px(0, 0)
    if inside(o):
        cv2.circle(out, o, 6, (0, 200, 255), 2, cv2.LINE_AA)
        cv2.putText(out, "arm base (0,0)", (o[0] + 8, o[1] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(out, "arm base (0,0)", (o[0] + 8, o[1] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1, cv2.LINE_AA)
    return out


class RealCamera:
    """iPhone via Continuity Camera (or any webcam) through AVFoundation.

    macOS asks for camera permission the first time; run from Terminal.app so the prompt
    appears and grant it to Terminal. The iPhone must be unlocked, on the same Apple ID and
    Wi-Fi/Bluetooth as the Mac; it shows up as an extra camera index (usually 1).
    """

    def __init__(self, index: int = 1, width: int = 1280, height: int = 960):
        self.cap = cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        if not self.cap.isOpened():
            raise RuntimeError(f"camera index {index} could not be opened (permission? phone unlocked?)")
        for _ in range(8):  # let exposure settle
            self.cap.read()

    def grab(self) -> np.ndarray:
        ok, frame = self.cap.read()
        if not ok:
            raise RuntimeError("camera read failed")
        return frame

    @staticmethod
    def list_devices() -> str:
        try:
            out = subprocess.run(["system_profiler", "SPCameraDataType"], capture_output=True, text=True, timeout=20).stdout
            return "\n".join(l.strip() for l in out.splitlines() if l.strip().endswith(":") and "Camera" in l)
        except Exception as exc:  # noqa: BLE001
            return f"(could not list cameras: {exc})"


def draw_grid(frame: np.ndarray, step: int = 100) -> np.ndarray:
    out = frame.copy()
    h, w = out.shape[:2]
    for x in range(0, w, step):
        cv2.line(out, (x, 0), (x, h), (255, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(out, str(x), (x + 3, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(out, str(x), (x + 3, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
    for y in range(0, h, step):
        cv2.line(out, (0, y), (w, y), (255, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(out, str(y), (3, y - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(out, str(y), (3, y - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
    return out


def annotate(frame: np.ndarray, plan: dict) -> np.ndarray:
    out = frame.copy()
    for piece in plan.get("pieces", []):
        u, v = int(piece["u"]), int(piece["v"])
        ferrous = bool(piece.get("ferrous"))
        color = (60, 60, 230) if ferrous else (200, 200, 200)
        cv2.circle(out, (u, v), 22, color, 3, cv2.LINE_AA)
        label = f"{piece['id']} {piece.get('material', '?')}{' (ferrous?)' if ferrous else ''}"
        cv2.putText(out, label, (u + 26, v + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(out, label, (u + 26, v + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)
    picks = [op for op in plan.get("program", []) if op.get("op") == "pick" and op.get("u") is not None]
    for i, op in enumerate(picks, 1):
        cv2.circle(out, (int(op["u"]) - 26, int(op["v"]) - 26), 15, (0, 150, 0), -1, cv2.LINE_AA)
        cv2.putText(out, str(i), (int(op["u"]) - 33, int(op["v"]) - 19), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA)
    if plan.get("message"):
        cv2.rectangle(out, (0, out.shape[0] - 36), (out.shape[1], out.shape[0]), (20, 20, 20), -1)
        cv2.putText(out, plan["message"][:120], (10, out.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (240, 240, 240), 1, cv2.LINE_AA)
    return out


def rectified_view(frames: dict[str, np.ndarray], cals: dict[str, "TableCalibration"], ppc: int = 24, step_cm: float = 2.0) -> np.ndarray | None:
    """Top-down (bird's-eye) view of the pick area, warped from each calibrated oblique photo with the same ArUco
    homography the robot uses. Straight cm grid, base at (0,0), container outlines. One panel per camera side by side.
    This is how a real installation would read positions from a phone on a gooseneck: rectify, then measure."""
    x0, x1 = -0.03, 0.17
    y0, y1 = -0.13, 0.13
    W, H = int((x1 - x0) * 100 * ppc), int((y1 - y0) * 100 * ppc)
    # output pixel (i, j) -> table (x, y): x grows to the right, y grows upwards
    T = np.array([[1.0 / (100 * ppc), 0, x0], [0, -1.0 / (100 * ppc), y1], [0, 0, 1.0]])
    panels = []
    for name, frame in frames.items():
        cal = cals.get(name)
        if cal is None or cal.H is None:
            continue
        M = cal.H_inv @ T  # output px -> source px
        top = cv2.warpPerspective(frame, M, (W, H), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderValue=(40, 40, 40))
        over = top.copy()

        def px(x, y):
            return int(round((x - x0) * 100 * ppc)), int(round((y1 - y) * 100 * ppc))

        for xc in np.arange(np.ceil(x0 * 100 / step_cm) * step_cm, x1 * 100 + 0.01, step_cm):
            major = abs(xc % 4) < 1e-6
            cv2.line(over, px(xc / 100, y0), px(xc / 100, y1), (255, 255, 255), 2 if major else 1)
            if major:
                cv2.putText(over, f"x={xc:.0f}", (px(xc / 100, y0)[0] + 3, H - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 3, cv2.LINE_AA)
                cv2.putText(over, f"x={xc:.0f}", (px(xc / 100, y0)[0] + 3, H - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 0), 1, cv2.LINE_AA)
        for yc in np.arange(np.ceil(y0 * 100 / step_cm) * step_cm, y1 * 100 + 0.01, step_cm):
            major = abs(yc % 4) < 1e-6
            cv2.line(over, px(x0, yc / 100), px(x1, yc / 100), (255, 255, 255), 2 if major else 1)
            if major:
                cv2.putText(over, f"y={yc:.0f}", (4, px(x0, yc / 100)[1] - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 3, cv2.LINE_AA)
                cv2.putText(over, f"y={yc:.0f}", (4, px(x0, yc / 100)[1] - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 0), 1, cv2.LINE_AA)
        for tname, t in sd.TARGETS.items():
            (tx, ty), (hx, hy) = t["pos"], (t["size"][0], t["size"][1])
            cv2.rectangle(over, px(tx - hx, ty + hy), px(tx + hx, ty - hy), (0, 200, 255), 2)
            cv2.putText(over, tname, (px(tx - hx, ty + hy)[0] + 3, px(tx - hx, ty + hy)[1] + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(over, tname, (px(tx - hx, ty + hy)[0] + 3, px(tx - hx, ty + hy)[1] + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1, cv2.LINE_AA)
        r0, r1 = sd.WORKSPACE["r"]
        a0, a1 = np.degrees(sd.WORKSPACE["yaw"])
        for rr in (r0, r1):
            cv2.ellipse(over, px(0, 0), (int(rr * 100 * ppc), int(rr * 100 * ppc)), 0, -a1, -a0, (0, 255, 120), 1)
        cv2.circle(over, px(0, 0), 6, (0, 220, 255), -1)
        cv2.putText(over, "arm base (0,0)", (px(0, 0)[0] + 8, px(0, 0)[1] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(over, "arm base (0,0)", (px(0, 0)[0] + 8, px(0, 0)[1] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 255), 1, cv2.LINE_AA)
        panel = cv2.addWeighted(over, 0.55, top, 0.45, 0)
        cv2.putText(panel, f"TOP-DOWN from camera {name} (tall objects smear: trust flat parts, read x,y at the part's centre)", (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(panel, f"TOP-DOWN from camera {name} (tall objects smear: trust flat parts, read x,y at the part's centre)", (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        panels.append(panel)
    if not panels:
        return None
    sep = np.full((H, 6, 3), 200, np.uint8)
    out = panels[0]
    for p in panels[1:]:
        out = np.hstack([out, sep, p])
    return out
