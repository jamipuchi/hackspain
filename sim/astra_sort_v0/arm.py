"""3-axis SCARA arm with an electromagnet, simulated in MuJoCo.

Axes: j1 base yaw, j2 elbow yaw, j3 vertical slide. The electromagnet is a set of weld
constraints (one per ferrous piece) that get switched on when the magnet face is close to
a ferrous piece and switched off on release. Non-ferrous pieces never stick.

The executor runs a "program": a list of primitive ops (move_xy, move_z, magnet_on,
magnet_off, pick, place, home, wait). This is the output side of the Astra loop.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import mujoco
import numpy as np

SCENE = Path(__file__).resolve().parent / "scene.xml"

L1 = 0.25
L2 = 0.25
TIP_Z_AT_ZERO = 0.15  # magnet face height when j3 = 0
TRAVEL_Z = 0.10  # safe height for horizontal moves
PIECE_TOP_Z = 0.016  # pieces are 16 mm tall
PICK_Z = PIECE_TOP_Z + 0.001
MAGNET_RANGE = 0.012  # magnet face must be within this distance of the piece top
MAGNET_RADIUS = 0.022  # ...and horizontally within this radius
WORKSPACE = {"x": (0.12, 0.46), "y": (-0.18, 0.18)}
TARGETS = {"iron_bin": (0.12, 0.26, 0.06)}
HOME_XY = (0.02, -0.32)  # parked along the bottom edge of the camera view

FERROUS_PREFIX = "piece_iron_"


class UnreachableError(ValueError):
    pass


@dataclass
class StepResult:
    op: dict
    ok: bool
    detail: str
    attached: str | None = None


@dataclass
class Arm:
    model: mujoco.MjModel
    data: mujoco.MjData
    on_step: Callable[[], None] | None = None
    magnet_on: bool = False
    attached: str | None = None
    log: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, on_step: Callable[[], None] | None = None) -> "Arm":
        model = mujoco.MjModel.from_xml_path(str(SCENE))
        data = mujoco.MjData(model)
        arm = cls(model, data, on_step)
        arm.pieces = [model.body(i).name for i in range(model.nbody) if model.body(i).name.startswith("piece_")]
        arm.welds = {
            model.eq(i).name.removeprefix("mag_"): i
            for i in range(model.neq)
            if model.eq(i).name.startswith("mag_")
        }
        mujoco.mj_forward(model, data)
        arm.data.ctrl[:] = arm.data.qpos[:3]
        return arm

    # ---------------------------------------------------------------- kinematics
    @staticmethod
    def ik(x: float, y: float, elbow_sign: float = 1.0) -> tuple[float, float]:
        r2 = x * x + y * y
        c2 = (r2 - L1 * L1 - L2 * L2) / (2 * L1 * L2)
        if c2 > 1.0 or c2 < -1.0:
            raise UnreachableError(f"({x:.3f}, {y:.3f}) is outside the arm reach")
        q2 = elbow_sign * math.acos(max(-1.0, min(1.0, c2)))
        q1 = math.atan2(y, x) - math.atan2(L2 * math.sin(q2), L1 + L2 * math.cos(q2))
        return q1, q2

    def tip(self) -> np.ndarray:
        return self.data.site("magnet_tip").xpos.copy()

    def tip_z_to_q3(self, z: float) -> float:
        return float(np.clip(z - TIP_Z_AT_ZERO, self.model.jnt_range[2][0], self.model.jnt_range[2][1]))

    # ---------------------------------------------------------------- simulation
    def step(self, n: int = 1) -> None:
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)
            self._hold_attached()
            if self.on_step is not None:
                self.on_step()

    def settle(self, target: np.ndarray, tol: float, timeout_s: float = 4.0) -> bool:
        steps = int(timeout_s / self.model.opt.timestep)
        for _ in range(steps):
            self.step()
            if np.all(np.abs(self.data.qpos[:3] - target) < tol) and np.all(np.abs(self.data.qvel[:3]) < 0.05):
                return True
        return False

    def wait(self, seconds: float) -> None:
        self.step(int(seconds / self.model.opt.timestep))

    def _go(self, q_target: np.ndarray, tol=(0.01, 0.01, 0.002), timeout_s: float = 4.0) -> bool:
        # Ramp the position targets so the physics arm follows without overshoot.
        start = self.data.ctrl[:3].copy()
        n = max(1, int(0.6 / self.model.opt.timestep))
        for i in range(1, n + 1):
            self.data.ctrl[:3] = start + (q_target - start) * (i / n)
            self.step()
        return self.settle(q_target, np.array(tol), timeout_s)

    # ---------------------------------------------------------------- primitives
    def move_z(self, z: float) -> bool:
        q = self.data.ctrl[:3].copy()
        q[2] = self.tip_z_to_q3(z)
        return self._go(q)

    def move_xy(self, x: float, y: float) -> bool:
        if self.tip()[2] < TRAVEL_Z - 0.005:
            self.move_z(TRAVEL_Z)
        # pick the elbow solution closest to the current configuration
        candidates = []
        for sign in (1.0, -1.0):
            q1, q2 = self.ik(x, y, sign)
            if self.model.jnt_range[0][0] <= q1 <= self.model.jnt_range[0][1] and self.model.jnt_range[1][0] <= q2 <= self.model.jnt_range[1][1]:
                candidates.append((abs(q1 - self.data.qpos[0]) + abs(q2 - self.data.qpos[1]), q1, q2))
        if not candidates:
            raise UnreachableError(f"({x:.3f}, {y:.3f}) violates joint limits")
        _, q1, q2 = min(candidates)
        q = self.data.ctrl[:3].copy()
        q[0], q[1] = q1, q2
        return self._go(q)

    def home(self) -> bool:
        self.move_z(TRAVEL_Z)
        return self.move_xy(*HOME_XY)

    def set_magnet(self, on: bool) -> str | None:
        self.magnet_on = on
        if not on:
            released = self.attached
            if self.attached is not None:
                self.data.eq_active[self.welds[self.attached]] = 0
                self.attached = None
            self.wait(0.15)
            return released
        # Attach the nearest ferrous piece within range of the magnet face.
        tip = self.tip()
        best, best_d = None, 1e9
        for name, eq_id in self.welds.items():
            pos = self.data.body(name).xpos
            horiz = float(np.hypot(pos[0] - tip[0], pos[1] - tip[1]))
            vert = float(tip[2] - (pos[2] + PIECE_TOP_Z / 2))
            if horiz < MAGNET_RADIUS and -0.004 < vert < MAGNET_RANGE and horiz < best_d:
                best, best_d = name, horiz
        if best is not None:
            self._weld(best)
            self.attached = best
        self.wait(0.15)
        return best

    def _weld(self, piece: str) -> None:
        eq_id = self.welds[piece]
        b1 = self.data.body("magnet")
        b2 = self.data.body(piece)
        r1 = b1.xmat.reshape(3, 3)
        rel_pos = r1.T @ (b2.xpos - b1.xpos)
        q1_inv = np.zeros(4)
        mujoco.mju_negQuat(q1_inv, b1.xquat)
        rel_quat = np.zeros(4)
        mujoco.mju_mulQuat(rel_quat, q1_inv, b2.xquat)
        self.model.eq_data[eq_id, 0:3] = 0.0
        self.model.eq_data[eq_id, 3:6] = rel_pos
        self.model.eq_data[eq_id, 6:10] = rel_quat
        self.model.eq_data[eq_id, 10] = 1.0
        self.data.eq_active[eq_id] = 1

    def _hold_attached(self) -> None:
        # Nothing to do: the weld carries the piece. Kept as a hook for future dynamics tweaks.
        return

    # ---------------------------------------------------------------- macros
    def pick(self, x: float, y: float) -> tuple[bool, str]:
        if not (WORKSPACE["x"][0] - 0.02 <= x <= WORKSPACE["x"][1] + 0.02 and WORKSPACE["y"][0] - 0.02 <= y <= WORKSPACE["y"][1] + 0.02):
            return False, f"pick ({x:.3f},{y:.3f}) is outside the workspace"
        self.move_xy(x, y)
        self.move_z(PICK_Z)
        got = self.set_magnet(True)
        self.move_z(TRAVEL_Z)
        if got is None:
            self.set_magnet(False)
            return False, f"pick at ({x:.3f},{y:.3f}): nothing stuck to the magnet (non-ferrous piece or missed position)"
        lifted = self.data.body(got).xpos[2] > 0.05
        if not lifted:
            self.set_magnet(False)
            return False, f"pick at ({x:.3f},{y:.3f}): piece {got} did not lift"
        return True, f"pick at ({x:.3f},{y:.3f}): lifted {got}"

    def place(self, target: str) -> tuple[bool, str]:
        if target not in TARGETS:
            return False, f"unknown target {target!r}"
        x, y, z = TARGETS[target]
        self.move_xy(x, y)
        self.move_z(z)
        released = self.set_magnet(False)
        self.wait(0.6)
        self.move_z(TRAVEL_Z)
        if released is None:
            return False, f"place {target}: magnet was empty"
        return True, f"place {target}: dropped {released}"

    # ---------------------------------------------------------------- programs
    def run_program(self, program: list[dict], pixel_to_table: Callable[[float, float], tuple[float, float]] | None = None) -> list[StepResult]:
        results: list[StepResult] = []
        for op in program:
            try:
                results.append(self._run_op(op, pixel_to_table))
            except UnreachableError as exc:
                results.append(StepResult(op, False, str(exc)))
            self.log.append(results[-1].detail)
        return results

    def _run_op(self, op: dict, pixel_to_table) -> StepResult:
        name = op.get("op")
        if name in ("pick", "move_xy"):
            if op.get("u") is not None and op.get("v") is not None:
                if pixel_to_table is None:
                    return StepResult(op, False, "pixel coordinates given but no camera calibration")
                x, y = pixel_to_table(float(op["u"]), float(op["v"]))
            elif op.get("x") is not None and op.get("y") is not None:
                x, y = float(op["x"]), float(op["y"])
            else:
                return StepResult(op, False, f"{name} needs (u,v) or (x,y)")
            if name == "pick":
                ok, detail = self.pick(x, y)
                return StepResult(op, ok, detail, self.attached)
            ok = self.move_xy(x, y)
            return StepResult(op, ok, f"move_xy ({x:.3f},{y:.3f}) {'reached' if ok else 'timed out'}")
        if name == "move_z":
            ok = self.move_z(float(op.get("z") if op.get("z") is not None else TRAVEL_Z))
            return StepResult(op, ok, f"move_z {op.get('z')} {'reached' if ok else 'timed out'}")
        if name == "magnet_on":
            got = self.set_magnet(True)
            return StepResult(op, got is not None, f"magnet on: {'attached ' + got if got else 'nothing attached'}", got)
        if name == "magnet_off":
            rel = self.set_magnet(False)
            return StepResult(op, True, f"magnet off: {'released ' + rel if rel else 'was empty'}")
        if name == "place":
            ok, detail = self.place(op.get("target") or "iron_bin")
            return StepResult(op, ok, detail)
        if name == "home":
            ok = self.home()
            return StepResult(op, ok, "home")
        if name == "wait":
            self.wait(float(op.get("seconds") or 0.5))
            return StepResult(op, True, f"wait {op.get('seconds')}")
        return StepResult(op, False, f"unknown op {name!r}")

    # ---------------------------------------------------------------- scene helpers
    def piece_positions(self) -> dict[str, np.ndarray]:
        return {p: self.data.body(p).xpos.copy() for p in self.pieces}

    def ferrous_left_on_table(self) -> list[str]:
        out = []
        for p in self.pieces:
            if not p.startswith(FERROUS_PREFIX):
                continue
            pos = self.data.body(p).xpos
            in_bin = abs(pos[0] - TARGETS["iron_bin"][0]) < 0.06 and abs(pos[1] - TARGETS["iron_bin"][1]) < 0.05
            if not in_bin and pos[2] > -0.1:
                out.append(p)
        return out

    def _set_piece(self, name: str, x: float, y: float, z: float = 0.012) -> None:
        jnt = self.model.body(name).jntadr[0]
        adr = self.model.jnt_qposadr[jnt]
        self.data.qpos[adr : adr + 7] = [x, y, z, 1, 0, 0, 0]
        self.data.qvel[self.model.jnt_dofadr[jnt] : self.model.jnt_dofadr[jnt] + 6] = 0

    def scatter_pieces(self, rng: np.random.Generator, pieces: list[str] | None = None) -> None:
        """Random non-overlapping layout inside the workspace."""
        placed: list[tuple[float, float]] = []
        for name in pieces or self.pieces:
            for _ in range(200):
                x = rng.uniform(WORKSPACE["x"][0] + 0.03, WORKSPACE["x"][1] - 0.03)
                y = rng.uniform(WORKSPACE["y"][0] + 0.03, WORKSPACE["y"][1] - 0.03)
                if all(np.hypot(x - px, y - py) > 0.065 for px, py in placed):
                    break
            placed.append((x, y))
            self._set_piece(name, x, y)
        mujoco.mj_forward(self.model, self.data)
        self.wait(0.3)

    def layout_twin(self, detections: list[dict]) -> dict[str, str]:
        """Real-camera mode: mirror detected pieces into the sim. Ferrous → iron bodies,
        the rest → non-ferrous bodies. Unused bodies are parked off the table."""
        ferrous = [p for p in self.pieces if p.startswith(FERROUS_PREFIX)]
        others = [p for p in self.pieces if not p.startswith(FERROUS_PREFIX)]
        mapping: dict[str, str] = {}
        for det in detections:
            pool = ferrous if det.get("ferrous") else others
            if not pool:
                continue
            body = pool.pop(0)
            self._set_piece(body, det["x"], det["y"])
            mapping[det["id"]] = body
        for i, body in enumerate(ferrous + others):
            self._set_piece(body, -0.8 - 0.05 * i, 0.0, -0.75 + 0.01)
        mujoco.mj_forward(self.model, self.data)
        self.wait(0.3)
        return mapping
