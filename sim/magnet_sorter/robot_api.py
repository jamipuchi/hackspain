"""Generic robot interface the GPT-6 agent talks to.

Everything the agent may know or do goes through here, in table-frame centimetres (origin at
the arm base, x forward, y left, z up from the work surface). Nothing in this file is specific
to one arm, one camera rig or one task: it reads the active build in scene_def and wraps an
ArmController (real or simulated). A different arm only needs a BuildConfig and a controller
that implements move_face_to / magnet / home / belt_advance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

import scene_def as sd
from controller import ArmController, Unreachable, in_workspace


@dataclass
class ToolResult:
    ok: bool
    text: str
    photo: bool = True  # attach a fresh photo after this action
    extra: dict = field(default_factory=dict)


class RobotAPI:
    def __init__(self, ctrl: ArmController, get_face_pos, verifier=None):
        self.ctrl = ctrl
        self.get_face_pos = get_face_pos  # () -> np.ndarray world metres (magnet face)
        self.magnet_on = False
        self.actions = 0
        self.camera_names: list[str] = list(sd.CAMERAS)  # the agent narrows this to the cameras actually mounted
        self.budget_actions = 60

    # ------------------------------------------------------------ description for the prompt
    def describe(self) -> str:
        ws = sd.WORKSPACE
        r0, r1 = ws["r"][0] * 100, ws["r"][1] * 100
        y0, y1 = math.degrees(ws["yaw"][0]), math.degrees(ws["yaw"][1])
        lines = [
            f"Robot: {sd.CFG.title}.",
            f"Coordinates: centimetres in the table frame drawn on every photo: origin at the arm base, +x forward "
            f"(away from the base), +y to the left when looking from the base, z up from the work surface.",
            f"Reachable pick region: radius {r0:.0f}–{r1:.0f} cm from the base, direction {y0:.0f}° to {y1:.0f}° "
            f"(0° = +x axis, positive towards +y). The magnet face can be lowered to z = {sd.PICK_Z * 100:.1f} cm above the surface; "
            f"travel height is z = {sd.TRAVEL_Z * 100:.1f} cm. Small parts are 0.2–1.2 cm tall.",
            "Containers (name → centre in cm, drop height):",
        ]
        for name, t in sd.TARGETS.items():
            x, y = t["pos"]
            lines.append(f"  - {name}: ({x * 100:.1f}, {y * 100:.1f}) cm, drop from z = {t['drop_z'] * 100:.1f} cm — {t['label']}")
        if sd.CONVEYOR:
            c = sd.CONVEYOR
            lines.append(
                f"Conveyor: belt centred on x = {c['x'] * 100:.1f} cm running along +y at {c['speed'] * 100:.0f} cm/s when commanded; "
                f"parts are reachable only inside the pick zone y ∈ [{c['pick_zone_y'][0] * 100:.0f}, {c['pick_zone_y'][1] * 100:.0f}] cm; "
                f"the belt surface is {sd.SURFACE_Z * 100:.1f} cm above the board (z values are relative to the belt surface)."
            )
        cams = []
        for name, cam in sd.CAMERAS.items():
            if name not in self.camera_names:
                continue
            px, py, pz = cam["pos"]
            lx, ly, _ = cam["lookat"]
            d = math.hypot(px - lx, py - ly)
            elev = math.degrees(math.atan2(pz, d))
            side = "left (+y)" if py > 0.03 else "right (−y)" if py < -0.03 else "centre"
            cams.append(f"camera {name}: {math.hypot(d, pz) * 100:.0f} cm from the scene, {elev:.0f}° above the table, on the {side} side, "
                        f"{'in front of the scene looking back towards the base' if px > lx + 0.05 else 'beside the scene'}")
        lines.append(f"Cameras ({len(cams)}): " + "; ".join(cams) + ". Photos are calibrated from the four ArUco markers; the white grid drawn on them is the "
                     "table frame in 2 cm steps with labelled axes, so you can read positions in cm directly.")
        return "\n".join(lines)

    # ------------------------------------------------------------ state
    def state(self) -> dict:
        p = self.get_face_pos()
        return {"magnet_face_cm": [round(float(p[0]) * 100, 1), round(float(p[1]) * 100, 1), round((float(p[2]) - sd.SURFACE_Z) * 100, 1)], "magnet_on": self.magnet_on, "actions_so_far": self.actions}

    def _fmt_state(self) -> str:
        s = self.state()
        x, y, z = s["magnet_face_cm"]
        return f"magnet face now at ({x}, {y}, z={z}) cm, magnet {'ON' if s['magnet_on'] else 'off'}"

    # ------------------------------------------------------------ actions (all in cm)
    def move_to(self, x_cm: float, y_cm: float, z_cm: float) -> ToolResult:
        self.actions += 1
        x, y = x_cm / 100, y_cm / 100
        z = sd.SURFACE_Z + max(sd.PICK_Z, min(z_cm / 100, 0.12))
        if not in_workspace(x, y):
            return ToolResult(False, f"({x_cm:.1f}, {y_cm:.1f}) is outside the reachable region; {self._fmt_state()}", photo=False)
        try:
            self.ctrl.move_face_to(x, y, z)
        except Unreachable as exc:
            return ToolResult(False, f"unreachable: {exc}; {self._fmt_state()}", photo=False)
        return ToolResult(True, f"moved; {self._fmt_state()}")

    def nudge(self, dx_cm: float, dy_cm: float, dz_cm: float) -> ToolResult:
        p = self.get_face_pos()
        return self.move_to(p[0] * 100 + dx_cm, p[1] * 100 + dy_cm, (p[2] - sd.SURFACE_Z) * 100 + dz_cm)

    def magnet(self, on: bool) -> ToolResult:
        self.actions += 1
        self.ctrl.magnet(on)
        self.magnet_on = on
        return ToolResult(True, f"electromagnet {'energised' if on else 'released'}; {self._fmt_state()}")

    def pick_at(self, x_cm: float, y_cm: float) -> ToolResult:
        self.actions += 1
        res = self.ctrl.pick(x_cm / 100, y_cm / 100)
        self.magnet_on = bool(res.ok)
        return ToolResult(res.ok, f"{res.detail}; {self._fmt_state()}")

    def place_in(self, target: str) -> ToolResult:
        self.actions += 1
        res = self.ctrl.place(target)
        self.magnet_on = False
        return ToolResult(res.ok, f"{res.detail}; {self._fmt_state()}")

    def home(self) -> ToolResult:
        self.actions += 1
        self.ctrl.home()
        return ToolResult(True, f"parked; {self._fmt_state()}")

    def belt_advance(self, cm: float) -> ToolResult:
        self.actions += 1
        res = self.ctrl.belt_advance(max(1.0, min(cm, 20.0)))
        return ToolResult(res.ok, res.detail)
