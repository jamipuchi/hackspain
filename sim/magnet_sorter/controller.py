"""Arm controller: inverse kinematics for the 3-servo arm and the pick/place program executor.

Talks to the Arduino (real or simulated) only through the serial protocol, exactly like the
physical build would.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np

import scene_def as sd
from hardware import joint_to_servo


class Unreachable(ValueError):
    pass


def ik(x: float, y: float, z_face: float) -> tuple[float, float, float]:
    """Joint angles (base yaw, shoulder from horizontal, elbow relative) for a magnet-face target.

    The magnet hangs vertically from the wrist pivot, so the wrist pivot is HANG above the face.
    """
    # the wrist plane may sit beside the base axis (servo-horn side): solve the offset triangle
    e = getattr(sd, "WRIST_Y", 0.0)
    rho = math.hypot(x, y)
    if rho <= abs(e) + 1e-6:
        raise Unreachable(f"target r={rho:.3f} too close to the base axis")
    yaw = math.atan2(y, x) - math.asin(e / rho)
    r = math.sqrt(rho * rho - e * e)
    zw = z_face + sd.HANG
    dx, dz = r, zw - sd.SHOULDER_Z
    dist2 = dx * dx + dz * dz
    c2 = (dist2 - sd.L1 * sd.L1 - sd.L2 * sd.L2) / (2 * sd.L1 * sd.L2)
    if c2 > 1.0 or c2 < -1.0:
        raise Unreachable(f"target r={r:.3f} z={z_face:.3f} out of reach")
    q2 = -math.acos(c2)  # elbow-up: forearm bends down
    q1 = math.atan2(dz, dx) - math.atan2(sd.L2 * math.sin(q2), sd.L1 + sd.L2 * math.cos(q2))
    for name, q in (("base", yaw), ("shoulder", q1), ("elbow", q2)):
        lo, hi = sd.JOINTS[name]["range"]
        if not (lo - 1e-6 <= q <= hi + 1e-6):
            raise Unreachable(f"{name} joint {math.degrees(q):.1f}° outside [{math.degrees(lo):.0f}, {math.degrees(hi):.0f}]")
    return yaw, q1, q2


def fk(yaw: float, q1: float, q2: float) -> np.ndarray:
    e = getattr(sd, "WRIST_Y", 0.0)
    r = sd.L1 * math.cos(q1) + sd.L2 * math.cos(q1 + q2)
    z = sd.SHOULDER_Z + sd.L1 * math.sin(q1) + sd.L2 * math.sin(q1 + q2) - sd.HANG
    return np.array([r * math.cos(yaw) - e * math.sin(yaw), r * math.sin(yaw) + e * math.cos(yaw), z])


def in_workspace(x: float, y: float) -> bool:
    r, yaw = math.hypot(x, y), math.atan2(y, x)
    return sd.WORKSPACE["r"][0] - 0.01 <= r <= sd.WORKSPACE["r"][1] + 0.01 and sd.WORKSPACE["yaw"][0] - 0.05 <= yaw <= sd.WORKSPACE["yaw"][1] + 0.05


@dataclass
class OpResult:
    op: dict
    ok: bool
    detail: str


class ArmController:
    def __init__(self, arduino, step: Callable[[int], None], settle_time: float = 0.6, frame_grab: Callable[[], np.ndarray] | None = None, frame_grabs: dict[str, Callable[[], np.ndarray]] | None = None, calibrations: dict | None = None):
        """arduino: SimArduino/SerialArduino; step(n): advance the world n sim steps (real: sleep).

        frame_grabs/calibrations: one entry per camera. With two cameras the pick verification is
        cross-checked from both viewpoints, which is robust to the arm occluding one of them.
        """
        self.ard = arduino
        self.step = step
        self.settle_time = settle_time
        self.frame_grab = frame_grab
        self.frame_grabs = frame_grabs or ({"A": frame_grab} if frame_grab else {})
        self.calibrations = calibrations or {}
        self.pixel_to_table: Callable[[float, float], tuple[float, float]] | None = None
        self.last_pick: dict | None = None  # {u, v, x, y, patch}
        self.on_event: Callable[[str], None] | None = None

    # ------------------------------------------------------------ low level
    def _wait(self, extra: float = 0.0, timeout: float = 4.0) -> None:
        t = 0.0
        while self.ard.busy() and t < timeout:
            self.step(25)
            t += 25 * 0.002
        self.step(int((self.settle_time + extra) / 0.002))

    def servo(self, yaw: float, q1: float, q2: float) -> None:
        s = (joint_to_servo("base", yaw), joint_to_servo("shoulder", q1), joint_to_servo("elbow", q2))
        self.ard.write(f"S {s[0]} {s[1]} {s[2]}")
        self._wait()

    def move_face_to(self, x: float, y: float, z: float) -> None:
        self.servo(*ik(x, y, z))

    def magnet(self, on: bool) -> None:
        self.ard.write(f"M {1 if on else 0}")
        self.step(int(0.25 / 0.002))

    def home(self) -> None:
        self.ard.write("H")
        self._wait(extra=0.2)

    # ------------------------------------------------------------ ops
    def pick(self, x: float, y: float, u: float | None = None, v: float | None = None) -> OpResult:
        op = {"op": "pick", "x": x, "y": y}
        if not in_workspace(x, y):
            return OpResult(op, False, f"pick ({x:.3f},{y:.3f}) is outside the arm workspace")
        travel, pick_z = sd.SURFACE_Z + sd.TRAVEL_Z, sd.SURFACE_Z + sd.PICK_Z
        try:
            patch = self._patches(x, y, u, v) if self.frame_grabs else None
            self.move_face_to(x, y, travel)
            self.move_face_to(x, y, pick_z + 0.010)
            self.move_face_to(x, y, pick_z)  # settle fully at pick height BEFORE energising: never press a part that has stood up
            self.step(int(0.3 / 0.002))
            self.magnet(True)
            self.step(int(0.6 / 0.002))
            self.move_face_to(x, y, pick_z + 0.015)  # lift slowly first, then to travel height
            self.move_face_to(x, y, travel)
        except Unreachable as exc:
            return OpResult(op, False, f"pick ({x:.3f},{y:.3f}): {exc}")
        self.last_pick = {"u": u, "v": v, "x": x, "y": y, "patch": patch}
        return OpResult(op, True, f"pick at ({x:.3f},{y:.3f}) done, magnet on, arm lifted")

    def place(self, target: str | None = None) -> OpResult:
        target = target or next(iter(sd.TARGETS))
        op = {"op": "place", "target": target}
        if target not in sd.TARGETS:
            return OpResult(op, False, f"place: unknown target {target!r}; valid targets: {', '.join(sd.TARGETS)}")
        t = sd.TARGETS[target]
        bx, by = t["pos"]
        travel = max(sd.SURFACE_Z + sd.TRAVEL_Z, t["drop_z"])
        try:
            self.home()  # park out of the camera's view before checking the pick spot
            ok, detail = self._verify_pick()
            self.move_face_to(bx, by, travel)
            self.move_face_to(bx, by, t["drop_z"])
            self.magnet(False)
            self.step(int(0.4 / 0.002))
            self.move_face_to(bx, by, travel)
        except Unreachable as exc:
            return OpResult(op, False, f"place: {exc}")
        return OpResult(op, ok, detail.replace("released in the bin", f"released in {target}"))

    def run(self, program: list[dict]) -> list[OpResult]:
        out = []
        for op in program:
            name = op.get("op")
            if self.on_event:
                self.on_event(f"op {name}")
            if name == "pick":
                if op.get("u") is not None and self.pixel_to_table:
                    x, y = self.pixel_to_table(float(op["u"]), float(op["v"]))
                    out.append(self.pick(x, y, op["u"], op["v"]))
                elif op.get("x") is not None:
                    out.append(self.pick(float(op["x"]), float(op["y"])))
                else:
                    out.append(OpResult(op, False, "pick needs (u,v) pixel coordinates"))
            elif name == "place":
                out.append(self.place(op.get("target") or None))
            elif name == "home":
                self.home()
                out.append(OpResult(op, True, "home"))
            elif name == "belt":
                out.append(self.belt_advance(float(op.get("cm") or 8.0)))
            elif name == "wait":
                self.step(int(float(op.get("seconds") or 0.5) / 0.002))
                out.append(OpResult(op, True, f"wait {op.get('seconds')}"))
            elif name == "magnet_on":
                self.magnet(True)
                out.append(OpResult(op, True, "magnet on"))
            elif name == "magnet_off":
                self.magnet(False)
                out.append(OpResult(op, True, "magnet off"))
            elif name == "move_xy":
                try:
                    if op.get("u") is not None and self.pixel_to_table:
                        x, y = self.pixel_to_table(float(op["u"]), float(op["v"]))
                    else:
                        x, y = float(op["x"]), float(op["y"])
                    self.move_face_to(x, y, sd.SURFACE_Z + sd.TRAVEL_Z)
                    out.append(OpResult(op, True, f"moved above ({x:.3f},{y:.3f})"))
                except (Unreachable, KeyError, TypeError) as exc:
                    out.append(OpResult(op, False, f"move_xy failed: {exc}"))
            else:
                out.append(OpResult(op, False, f"unknown op {name!r}"))
        return out

    def belt_advance(self, cm: float) -> OpResult:
        op = {"op": "belt", "cm": cm}
        c = sd.CONVEYOR
        if c is None:
            return OpResult(op, False, "belt: this build has no conveyor")
        self.home()
        seconds = (cm / 100.0) / c["speed"]
        self.ard.write("C 100")
        self.step(int(seconds / 0.002))
        self.ard.write("C 0")
        self.step(int(0.6 / 0.002))
        return OpResult(op, True, f"belt advanced {cm:.0f} cm and stopped")

    # ------------------------------------------------------------ pickup verification (camera)
    PATCH_R = 26  # px around the pick pixel that must contain the part
    SEARCH_R = 48  # px neighbourhood searched for a nudged part (small: look-alike neighbours confuse the match)

    def _patch(self, u, v, r: int | None = None, grab=None) -> np.ndarray | None:
        r = r or self.PATCH_R
        frame = (grab or self.frame_grab)()
        h, w = frame.shape[:2]
        u, v = int(u), int(v)
        return frame[max(0, v - r) : min(h, v + r), max(0, u - r) : min(w, u + r)].copy()

    def _pixel_in(self, cam: str, x: float, y: float, u, v):
        """Pixel of table point (x, y) in camera cam; falls back to the given (u, v) for the primary camera."""
        cal = self.calibrations.get(cam)
        if cal is not None and cal.H is not None:
            return cal.table_to_pixel(x, y)
        return (int(u), int(v)) if u is not None else None

    def _patches(self, x, y, u, v) -> dict:
        out = {}
        for cam, grab in self.frame_grabs.items():
            px = self._pixel_in(cam, x, y, u, v)
            if px is None:
                continue
            out[cam] = {"uv": px, "patch": self._patch(px[0], px[1], grab=grab)}
        return out

    def _verify_pick(self) -> tuple[bool, str]:
        """With the arm parked over the bin, look at the pick spot: is the piece gone?"""
        lp = self.last_pick
        if lp is None:
            return False, "place: nothing was picked before this place"
        if lp["patch"] is None or not self.frame_grab:
            return True, "place: released over the bin (no camera to verify the pick)"
        patches = lp["patch"]
        x, y = lp["x"], lp["y"]
        self.last_pick = None
        if not patches:
            return True, "place: released over the bin (no camera to verify the pick)"
        # Per camera: compare the exact pick spot before/after (the arm is parked out of the way). A part that
        # left changes those pixels a lot; an untouched part leaves them unchanged. Template search was dropped:
        # a look-alike neighbour a centimetre away fools it. Nudges show up in the photo GPT-6 gets anyway.
        verdicts = []
        for cam, info in patches.items():
            before = info["patch"]
            u, v = info["uv"]
            grab = self.frame_grabs[cam]
            if before is None or before.size == 0:
                continue
            after = self._patch(u, v, grab=grab)
            if after is None or after.shape != before.shape:
                continue
            mad = float(np.abs(before.astype(np.int16) - after.astype(np.int16)).mean())
            contrast = float(before.astype(np.int16).mean(axis=2).std())
            thr_same, thr_changed = (4.0, 8.0) if contrast < 8 else (6.0, 12.0)
            verdicts.append(("still" if mad < thr_same else "gone" if mad > thr_changed else "unsure", cam, round(mad, 1)))
        where = f"({x * 100:.1f}, {y * 100:.1f}) cm"
        if not verdicts:
            return True, "place: released over the container (could not compare frames)"
        gone = [v[1] for v in verdicts if v[0] == "gone"]
        still = [v[1] for v in verdicts if v[0] == "still"]
        if gone and not still:
            return True, f"pick at {where} succeeded: the spot changed in camera {','.join(gone)} (part left it); released in the container"
        if still and not gone:
            return False, f"pick at {where} FAILED: the spot looks unchanged in camera {','.join(still)} (part still there). If it looks like steel your x,y were probably off by more than 1 cm: re-read its centre on the top-down view and try once more; if it is brass/aluminium/plastic leave it. Released over the container anyway"
        if still and gone:
            return False, f"pick at {where}: cameras disagree (camera {','.join(gone)} sees the spot changed, camera {','.join(still)} unchanged); released over the container anyway, check the photo"
        return True, f"pick at {where}: unclear from the cameras whether the part left; released over the container, check the photo"
