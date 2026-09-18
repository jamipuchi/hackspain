"""Simulated overhead camera: renders frames from MuJoCo and maps pixels back to the table."""

from __future__ import annotations

import math

import cv2
import mujoco
import numpy as np

from arm import Arm

PIECE_CENTER_Z = 0.008


class SimCamera:
    def __init__(self, arm: Arm, camera: str = "overhead", width: int = 960, height: int = 720):
        self.arm = arm
        self.name = camera
        self.width, self.height = width, height
        self.renderer = mujoco.Renderer(arm.model, height=height, width=width)
        self.cam_id = arm.model.camera(camera).id
        fovy = math.radians(float(arm.model.cam_fovy[self.cam_id]))
        self.focal = (height / 2) / math.tan(fovy / 2)

    def grab(self) -> np.ndarray:
        """BGR frame, as OpenCV expects."""
        self.renderer.update_scene(self.arm.data, camera=self.name)
        rgb = self.renderer.render()
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    def pixel_to_table(self, u: float, v: float, z_plane: float = PIECE_CENTER_Z) -> tuple[float, float]:
        """Cast a ray through pixel (u, v) and intersect it with the horizontal plane z_plane."""
        cam_pos = self.arm.data.cam_xpos[self.cam_id]
        cam_rot = self.arm.data.cam_xmat[self.cam_id].reshape(3, 3)
        d_cam = np.array([(u - self.width / 2) / self.focal, -(v - self.height / 2) / self.focal, -1.0])
        d_world = cam_rot @ d_cam
        if abs(d_world[2]) < 1e-9:
            raise ValueError("ray parallel to the table")
        t = (z_plane - cam_pos[2]) / d_world[2]
        p = cam_pos + t * d_world
        return float(p[0]), float(p[1])

    def table_to_pixel(self, x: float, y: float, z: float = PIECE_CENTER_Z) -> tuple[int, int]:
        cam_pos = self.arm.data.cam_xpos[self.cam_id]
        cam_rot = self.arm.data.cam_xmat[self.cam_id].reshape(3, 3)
        p_cam = cam_rot.T @ (np.array([x, y, z]) - cam_pos)
        u = self.width / 2 + self.focal * p_cam[0] / -p_cam[2]
        v = self.height / 2 - self.focal * p_cam[1] / -p_cam[2]
        return int(round(u)), int(round(v))


def draw_grid(frame: np.ndarray, step: int = 100) -> np.ndarray:
    """Light pixel grid with labelled ticks so the vision model can quote coordinates."""
    out = frame.copy()
    h, w = out.shape[:2]
    for x in range(0, w, step):
        cv2.line(out, (x, 0), (x, h), (120, 120, 120), 1, cv2.LINE_AA)
        cv2.putText(out, str(x), (x + 2, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (40, 40, 40), 1, cv2.LINE_AA)
    for y in range(0, h, step):
        cv2.line(out, (0, y), (w, y), (120, 120, 120), 1, cv2.LINE_AA)
        cv2.putText(out, str(y), (2, y - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (40, 40, 40), 1, cv2.LINE_AA)
    return out


def annotate(frame: np.ndarray, plan: dict) -> np.ndarray:
    out = frame.copy()
    colors = {"iron": (60, 60, 200), "aluminum": (200, 200, 200), "brass": (40, 170, 220), "plastic": (200, 80, 200), "unknown": (0, 0, 0)}
    for piece in plan.get("pieces", []):
        u, v = int(piece["u"]), int(piece["v"])
        color = colors.get(piece.get("material", "unknown"), (0, 0, 0))
        cv2.circle(out, (u, v), 20, color, 3, cv2.LINE_AA)
        label = f"{piece['id']} {piece.get('material')}"
        cv2.putText(out, label, (u + 24, v + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(out, label, (u + 24, v + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)
    picks = [op for op in plan.get("program", []) if op.get("op") == "pick" and op.get("u") is not None]
    for i, op in enumerate(picks, 1):
        cv2.circle(out, (int(op["u"]) - 24, int(op["v"]) - 24), 15, (0, 140, 0), -1, cv2.LINE_AA)
        cv2.putText(out, str(i), (int(op["u"]) - 31, int(op["v"]) - 17), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA)
    if plan.get("message"):
        cv2.rectangle(out, (0, out.shape[0] - 34), (out.shape[1], out.shape[0]), (20, 20, 20), -1)
        cv2.putText(out, plan["message"][:95], (10, out.shape[0] - 11), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (240, 240, 240), 1, cv2.LINE_AA)
    return out
