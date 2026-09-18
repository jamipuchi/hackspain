"""Hardware layer: the Arduino, three hobby servos and the electromagnet.

Two implementations of the same serial protocol:

  SimArduino   runs the firmware logic in Python against the MuJoCo model (servo speed ramp,
               1° command quantisation, torque-limited servo loop, electromagnet force field).
  SerialArduino talks to a real Uno over USB running firmware/magnet_arm/magnet_arm.ino.

Protocol (ASCII lines, 115200 baud):
  S <base> <shoulder> <elbow>   set servo targets in degrees (0-180 ints)  -> "ok"
  M <0|1>                       electromagnet off/on                       -> "ok"
  ?                             -> "P <base> <shoulder> <elbow> M <0|1> B <0|1>"  (B = still moving)
  H                             home pose                                  -> "ok"
  C <speed>                     conveyor servo (DS04-NFC), -100..100, 0 stops -> "ok"
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import mujoco
import numpy as np

import scene_def as sd

TICK_S = 0.020  # firmware servo update period (50 Hz, one servo pulse)
ACCEL_DEG_S2 = 700.0  # firmware acceleration limit: a hanging magnet with a part must not be whipped


def home_servo() -> tuple:
    return tuple(sd.HOME_SERVO)


def joint_to_servo(joint: str, q_rad: float) -> int:
    j = sd.JOINTS[joint]
    deg = j["servo_offset"] + j["servo_sign"] * math.degrees(q_rad)
    return int(round(min(180, max(0, deg))))


def servo_to_joint(joint: str, servo_deg: float) -> float:
    j = sd.JOINTS[joint]
    return math.radians((servo_deg - j["servo_offset"]) / j["servo_sign"])


@dataclass
class SerialLog:
    lines: list[str] = field(default_factory=list)

    def tx(self, s: str) -> None:
        self.lines.append(f"> {s}")

    def rx(self, s: str) -> None:
        self.lines.append(f"< {s}")


class SimArduino:
    """Firmware emulation + physics coupling. Call .tick() every sim step."""

    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData, pieces: list[sd.Body], log: SerialLog | None = None):
        self.model, self.data = model, data
        self.pieces = pieces
        self.log = log or SerialLog()
        self.target = list(home_servo())  # commanded servo angles (deg)
        self.current = list(home_servo())  # ramped servo angles the firmware is outputting
        self.magnet = False  # relay/MOSFET commanded state
        self.magnet_coil = 0.0  # 0..1 effective coil current (relay delay + flyback decay)
        self._magnet_cmd_t = 0.0
        self.belt_speed = 0  # -100..100 (% of DS04-NFC full speed)
        self.speeds = [min(sd.joint_servo(j)["speed_deg_s"], sd.joint_servo(j).get("ramp_deg_s", 1e9)) for j in ("base", "shoulder", "elbow")]
        self.servo_vel = [0.0, 0.0, 0.0]
        self.joint_names = ("base", "shoulder", "elbow")
        self._acc = 0.0
        self.face_site = model.site("magnet_face").id
        self.wrist_body = model.body("wrist").id
        self.piece_ids = {p.name: model.body(p.name).id for p in pieces}
        self.ferrous = {p.name for p in pieces if p.piece["ferrous"]}
        self.half_h = {p.name: sd.piece_half_height(p) for p in pieces}
        self.magnet_force_total = 0.0
        self.attracted: list[str] = []
        self.piece_dofadr = {p.name: model.jnt_dofadr[model.body(p.name).jntadr[0]] for p in pieces}
        self.velocity_clamps = 0
        self.welds = {name: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_EQUALITY, f"mag_{name}") for name in self.ferrous}
        self.held: set[str] = set()
        self._apply_servos()
        # start the joints at the home pose so the arm doesn't fall from zero
        for jn, deg in zip(self.joint_names, home_servo()):
            adr = model.jnt_qposadr[model.joint(jn).id]
            data.qpos[adr] = servo_to_joint(jn, deg)
        mujoco.mj_forward(model, data)

    # ------------------------------------------------------------ serial
    def write(self, line: str) -> str:
        line = line.strip()
        self.log.tx(line)
        parts = line.split()
        if not parts:
            return ""
        cmd = parts[0].upper()
        if cmd == "S" and len(parts) == 4:
            self.target = [int(min(180, max(0, int(float(p))))) for p in parts[1:]]
            reply = "ok"
        elif cmd == "M" and len(parts) == 2:
            self.magnet = parts[1] == "1"
            self._magnet_cmd_t = self.data.time
            reply = "ok"
        elif cmd == "C" and len(parts) == 2:
            self.belt_speed = int(max(-100, min(100, int(float(parts[1])))))
            reply = "ok"
        elif cmd == "H":
            self.target = list(home_servo())
            reply = "ok"
        elif cmd == "?":
            moving = int(any(abs(t - c) > 0.01 for t, c in zip(self.target, self.current)))
            reply = f"P {int(round(self.current[0]))} {int(round(self.current[1]))} {int(round(self.current[2]))} M {int(self.magnet)} B {moving}"
        else:
            reply = "err"
        self.log.rx(reply)
        return reply

    def busy(self) -> bool:
        return any(abs(t - c) > 0.01 for t, c in zip(self.target, self.current)) or any(abs(v) > 1e-6 for v in self.servo_vel)

    # ------------------------------------------------------------ physics coupling
    def tick(self) -> None:
        """Advance the firmware clock by one sim step and apply servo/magnet effects."""
        self._acc += self.model.opt.timestep
        if self._acc >= TICK_S:
            self._acc -= TICK_S
            for i in range(3):
                # trapezoidal profile: accelerate at ACCEL_DEG_S2, cruise at the servo's ramp speed, decelerate to stop on target
                diff = self.target[i] - self.current[i]
                vmax = self.speeds[i]
                v_des = math.copysign(min(vmax, math.sqrt(2 * ACCEL_DEG_S2 * abs(diff))), diff) if abs(diff) > 1e-9 else 0.0
                v = self.servo_vel[i]
                dv = max(-ACCEL_DEG_S2 * TICK_S, min(ACCEL_DEG_S2 * TICK_S, v_des - v))
                v += dv
                move = v * TICK_S
                if abs(move) >= abs(diff):
                    move, v = diff, 0.0
                self.current[i] += move
                self.servo_vel[i] = v
            self._apply_servos()
        self._update_coil()
        self._apply_magnet()
        self._drive_belt()
        self._guard_velocities()

    def _update_coil(self) -> None:
        """Relay contacts close ~10 ms after the command; the flyback diode lets the field collapse in ~20 ms."""
        dt = self.model.opt.timestep
        delay = sd.MAGNET.get("relay_delay_s", 0.0)
        if self.magnet and self.data.time - self._magnet_cmd_t >= delay:
            self.magnet_coil = min(1.0, self.magnet_coil + dt / 0.015)
        elif not self.magnet:
            self.magnet_coil = max(0.0, self.magnet_coil - dt / 0.020)

    def _drive_belt(self) -> None:
        c = sd.CONVEYOR
        if c is None:
            return
        v = c["speed"] * self.belt_speed / 100.0
        span = c["length"] + 0.02
        for k in range(4):
            jid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, f"belt_seg_{k}")
            if jid < 0:
                return
            dof, adr = self.model.jnt_dofadr[jid], self.model.jnt_qposadr[jid]
            self.data.qvel[dof] = v
            if self.belt_speed != 0:
                bid = self.model.jnt_bodyid[jid]
                if self.model.body_pos[bid][1] + self.data.qpos[adr] > c["length"] / 2 + 0.01 + span / 8:
                    self.data.qpos[adr] -= span  # this segment passed the end roller: back to the start
        for i in range(2):
            rid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, f"roller_{i}")
            if rid >= 0:
                self.data.qvel[self.model.jnt_dofadr[rid]] = -v / c["roller_r"]

    def _guard_velocities(self, vmax: float = 2.5) -> None:
        """Numerical safety net: a pinched part can pick up absurd speed in one 2 ms step."""
        for name, adr in self.piece_dofadr.items():
            v = self.data.qvel[adr : adr + 3]
            speed = float(np.linalg.norm(v))
            if speed > vmax:
                self.data.qvel[adr : adr + 3] = v * (vmax / speed)
                self.data.qvel[adr + 3 : adr + 6] *= 0.5
                self.velocity_clamps += 1

    def _apply_servos(self) -> None:
        for i, jn in enumerate(self.joint_names):
            self.data.ctrl[i] = servo_to_joint(jn, round(self.current[i]))  # 1° quantisation of the pulse

    def _weld(self, name: str, on: bool) -> None:
        eq_id = self.welds.get(name, -1)
        if eq_id < 0:
            return
        if on:
            b1 = self.data.body("wrist")
            b2 = self.data.body(name)
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
            self.held.add(name)
        else:
            self.data.eq_active[eq_id] = 0
            self.held.discard(name)

    def _apply_magnet(self) -> None:
        self.data.xfrc_applied[:] = 0.0
        self.attracted = list(self.held)
        self.magnet_force_total = 0.0
        if self.magnet_coil <= 0.3:
            for name in list(self.held):  # field collapsed: everything drops
                self._weld(name, False)
            return
        face = self.data.site_xpos[self.face_site]
        normal = -self.data.site_xmat[self.face_site].reshape(3, 3)[:, 2]  # face points down the bracket
        for name in self.ferrous:
            if name in self.held:
                continue
            bid = self.piece_ids[name]
            com = self.data.xpos[bid]
            v = com - face
            axial = float(np.dot(v, normal))  # >0 below the face
            lateral = float(np.linalg.norm(v - axial * normal))
            gap = max(0.0, axial - self.half_h[name])
            reach = sd.MAGNET_RADIUS + 0.002  # small parts are drawn in from just beyond the pole face
            if gap > 0.02 or lateral > reach:
                continue
            f_axial = self.magnet_coil * sd.MAGNET_HOLD_FORCE / (1.0 + (gap / sd.MAGNET_D0) ** 2) ** 1.5
            f_axial *= max(0.0, 1.0 - (lateral / reach) ** 2)
            if f_axial < 1e-4:
                continue
            # A 25 N pull on a 5 g nut is 500 g of acceleration: real nuts just slam onto the face,
            # but a 2 ms integrator would launch them. Cap the acceleration and damp the approach.
            mass = self.model.body_mass[bid]
            direction = -v / (np.linalg.norm(v) + 1e-9)  # pull towards the face centre
            vel = self.data.cvel[bid, 3:6]
            approach = float(np.dot(vel, direction))
            if gap <= 0.0012 and lateral < sd.MAGNET_RADIUS + 0.002:
                # touching the pole face: a 25 N magnet holds a few-gram part rigidly → weld it to the magnet
                self._weld(name, True)
                continue
            # approach: a real magnet snaps parts in fast, but a 2 ms integrator cannot resolve a 1 m/s impact of a
            # 5 mm part, so cap the acceleration and let the pull fade as the part nears ~0.3 m/s (terminal speed)
            f_axial = min(f_axial, mass * sd.MAGNET_MAX_ACCEL) * max(0.0, 1.0 - max(0.0, approach) / 0.3)
            force = direction * f_axial
            self.data.xfrc_applied[bid, :3] += force
            self.data.xfrc_applied[self.wrist_body, :3] -= force
            self.attracted.append(name)
            self.magnet_force_total += f_axial


class SerialArduino:
    """Real Arduino over USB (pyserial). Same write() semantics as SimArduino."""

    def __init__(self, port: str, baud: int = 115200, log: SerialLog | None = None):
        import serial

        self.ser = serial.Serial(port, baud, timeout=2.0)
        time.sleep(2.0)  # Uno resets on open
        self.ser.reset_input_buffer()
        self.log = log or SerialLog()

    def write(self, line: str) -> str:
        self.log.tx(line)
        self.ser.write((line.strip() + "\n").encode())
        reply = self.ser.readline().decode(errors="replace").strip()
        self.log.rx(reply)
        return reply

    def busy(self) -> bool:
        return self.write("?").endswith("B 1")

    def tick(self) -> None:
        return None
