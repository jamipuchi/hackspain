"""Single source of truth for the physical builds.

Several BUILDS share the same software stack (camera → GPT-6 → serial → Arduino → servos +
electromagnet) and differ in hardware and task. Select with the SORTER_BUILD env var or
`run_demo.py --build <name>`:

  hobby_v1        first realistic build: MDF board, 3× MG996R printed arm, IRF520 MOSFET, mixed
                  metals, one tray — "pick every ferrous part"
  taras_v1        the Madrid shopping list (19 Sep 2026): wooden arm, 2× MG90S + 1× MG946R,
                  5 V relay, 24 V XP20/15 lifting magnet, white inspection pad, three clear
                  cups — "sort screws, nuts and washers", steel parts only + a few distractors
  taras_conveyor  taras_v1 + homemade conveyor driven by a DS04-NFC continuous servo: belt
                  advances, stops, camera, sort, repeat
  taras_kitting   taras_v1 hardware, task: assemble kits (1 screw + 1 nut + 1 washer per cup)
  taras_grading   taras_v1 hardware, task: grade screws by length, reject rusty ones

Units: metres, kilograms, radians. Frame: work surface is z = 0, arm base at the origin,
workspace in +x. Quaternions are (w, x, y, z).

Only stdlib + numpy here: this module is imported both by the venv and by Blender's Python.
"""

from __future__ import annotations

import copy
import json
import math
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"

# ------------------------------------------------------------------ servo catalogue
SERVO_MODELS = {
    # body dims (standing, output axis +z), no-load speed, stall torque at 5 V, loop stiffness
    "MG996R": dict(w=0.0407, d=0.0197, h=0.0429, speed_deg_s=300.0, torque_nm=1.0, kp=18.0, mass=0.055),
    "MG946R": dict(w=0.0407, d=0.0197, h=0.0429, speed_deg_s=330.0, torque_nm=1.2, kp=20.0, mass=0.055, ramp_deg_s=200.0),
    "MG90S": dict(w=0.0225, d=0.0120, h=0.0285, speed_deg_s=550.0, torque_nm=0.18, kp=4.0, mass=0.0137, ramp_deg_s=200.0),  # firmware ramps it slower so the hanging magnet does not whip
    "DS04-NFC": dict(w=0.0407, d=0.0197, h=0.0429, speed_deg_s=0.0, torque_nm=0.55, kp=0.0, mass=0.038),  # continuous rotation, belt drive
}


# ------------------------------------------------------------------ build configs
@dataclass
class BuildConfig:
    name: str
    title: str
    L1: float
    L2: float
    HANG: float
    SHOULDER_Z: float
    TURRET_Z: float
    joints: dict  # name -> dict(servo, range, servo_offset, servo_sign)
    magnet: dict  # radius, height, hold_force, d0, max_accel, mass, voltage, switch, relay_delay_s
    workspace: dict  # r range, yaw range
    surface_z: float  # where parts lie (0 = board; belt top for the conveyor)
    travel_z: float  # magnet face height for horizontal moves (above surface_z)
    pick_z: float  # magnet face height above the surface when picking
    targets: dict  # container name -> dict(pos, drop_z, size, shape, label)
    board: dict
    markers: dict  # aruco id -> table xy
    marker_size: float
    phone_cam: dict
    cine_cam: dict
    task: dict  # GPT-6 task text: goal, scene, kinds, materials, targets_help
    pieces: str  # piece-set builder name
    conveyor: dict | None = None
    home_servo: tuple = (90, 110, 40)
    wrist_y: float = 0.0
    cameras: dict | None = None  # name -> dict(pos, lookat, fovy_deg, width, height); oblique phone positions  # lateral offset of the wrist/magnet plane from the base axis (servo horn side)


def _polar(r: float, yaw_deg: float) -> tuple:
    a = math.radians(yaw_deg)
    return (round(r * math.cos(a), 4), round(r * math.sin(a), 4))


def _derive(base: BuildConfig, **changes) -> BuildConfig:
    d = copy.deepcopy(asdict(base))
    d.update(changes)
    return BuildConfig(**d)


HOBBY_V1 = BuildConfig(
    name="hobby_v1",
    title="Arduino Uno + 3× MG996R printed arm + IRF520 + 5 V electromagnet",
    L1=0.150,
    L2=0.160,
    HANG=0.045,
    SHOULDER_Z=0.078,
    TURRET_Z=0.052,
    joints={
        "base": dict(servo="MG996R", range=(-math.radians(90), math.radians(90)), servo_offset=90.0, servo_sign=1.0),
        "shoulder": dict(servo="MG996R", range=(math.radians(-10), math.radians(100)), servo_offset=20.0, servo_sign=1.0),
        "elbow": dict(servo="MG996R", range=(math.radians(-150), math.radians(0)), servo_offset=170.0, servo_sign=1.0),
    },
    magnet=dict(radius=0.010, height=0.015, hold_force=25.0, d0=0.0025, max_accel=20.0, mass=0.030, voltage=5, switch="mosfet", relay_delay_s=0.0),
    workspace=dict(r=(0.14, 0.26), yaw=(math.radians(-50), math.radians(50))),
    surface_z=0.0,
    travel_z=0.060,
    pick_z=0.014,
    targets={"iron_bin": dict(pos=(0.049, -0.184), drop_z=0.055, size=(0.045, 0.035, 0.022), shape="tray", label="blue tray (ferrous parts)")},
    board=dict(size=(0.50, 0.40, 0.012), center=(0.12, 0.0), material="mdf"),
    markers={0: (0.15, -0.20), 1: (0.32, -0.20), 2: (0.32, 0.20), 3: (0.15, 0.20)},
    marker_size=0.035,
    phone_cam=dict(pos=(0.21, 0.0, 0.42), fovy_deg=55.0, width=1280, height=960),
    cine_cam=dict(pos=(0.55, -0.56, 0.40), lookat=(0.13, 0.02, 0.05), fovy_deg=38.0, width=1200, height=900),
    task=dict(
        goal='Pick every FERROUS piece (carbon steel: zinc-plated or black-oxide nuts, bolts, washers) and drop it in the blue tray (target "iron_bin"). Leave non-ferrous pieces (brass, copper, aluminium, 300-series stainless) where they are: the magnet cannot lift them and trying wastes a cycle.',
        scene="the orange arm and black servos are the robot (ignore them), the blue tray is the target bin, the black-and-white squares are calibration markers (ignore them), the blue board at the back is the Arduino. Only list loose hardware pieces lying on the board.",
        kinds=["nut", "bolt", "washer", "standoff", "other"],
        materials=["zinc_steel", "black_steel", "stainless", "aluminum", "brass", "copper", "unknown"],
        targets_help="iron_bin: the blue tray.",
    ),
    pieces="mixed_metals",
    cameras={
        "A": dict(pos=(0.55, 0.30, 0.36), lookat=(0.24, 0.0, 0.0), fovy_deg=50.0, width=1280, height=960),
        "B": dict(pos=(0.16, -0.42, 0.30), lookat=(0.24, 0.0, 0.0), fovy_deg=50.0, width=1280, height=960),
    },
)

_TARAS_CAMS = {  # steep enough to look over the 2.2 cm cups into the pad; ≤ 50 cm from the scene
    "A": dict(pos=(0.02, 0.30, 0.30), lookat=(0.085, 0.0, 0.0), fovy_deg=36.0, width=1280, height=960),  # left side, 45° up, phone at ~2× zoom
    "B": dict(pos=(0.02, -0.30, 0.30), lookat=(0.085, 0.0, 0.0), fovy_deg=36.0, width=1280, height=960),  # right side, mirror of A
}

_CUP = dict(size=(0.030, 0.030, 0.022), shape="cup")  # 6 cm yoghurt-cup size: a 2 cm screw hanging off-centre still lands inside
_TARAS_UNKNOWN = dict(pos=_polar(0.070, -85), drop_z=0.045, size=(0.03, 0.022, 0.012), shape="tray", label="small blue tray beside the arm base for parts you cannot identify")

TARAS_V1 = BuildConfig(
    name="taras_v1",
    title="Wooden arm · 2× MG90S + MG946R · Uno R3 · 5 V relay · 24 V XP20/15 magnet · 3 cups",
    L1=0.070,
    L2=0.070,
    HANG=0.040,
    SHOULDER_Z=0.075,
    TURRET_Z=0.024,
    joints={
        "base": dict(servo="MG90S", range=(-math.radians(90), math.radians(90)), servo_offset=90.0, servo_sign=1.0),
        "shoulder": dict(servo="MG946R", range=(math.radians(-15), math.radians(105)), servo_offset=25.0, servo_sign=1.0),
        "elbow": dict(servo="MG90S", range=(math.radians(-165), math.radians(0)), servo_offset=175.0, servo_sign=1.0),
    },
    magnet=dict(radius=0.010, height=0.015, hold_force=25.0, d0=0.0025, max_accel=20.0, mass=0.032, voltage=24, switch="relay", relay_delay_s=0.010),
    workspace=dict(r=(0.055, 0.108), yaw=(math.radians(-38), math.radians(38))),
    surface_z=0.0,
    travel_z=0.072,  # clears a 4.4 cm cup rim with a 2 cm screw hanging under the magnet
    pick_z=0.013,
    targets={
        "screws": dict(pos=_polar(0.128, -42), drop_z=0.060, label="clear cup for screws", **_CUP),
        "nuts": dict(pos=_polar(0.130, 0), drop_z=0.060, label="clear cup for nuts", **_CUP),
        "washers": dict(pos=_polar(0.128, 42), drop_z=0.060, label="clear cup for washers", **_CUP),
        "unknown": dict(_TARAS_UNKNOWN),
    },
    board=dict(size=(0.30, 0.26, 0.018), center=(0.07, 0.0), material="wood"),
    markers={0: (0.03, -0.113), 1: (0.19, -0.113), 2: (0.19, 0.113), 3: (0.03, 0.113)},
    marker_size=0.025,
    phone_cam=dict(pos=(0.085, 0.0, 0.30), fovy_deg=55.0, width=1280, height=960),
    cine_cam=dict(pos=(0.38, -0.36, 0.26), lookat=(0.07, 0.01, 0.03), fovy_deg=36.0, width=1200, height=900),
    task=dict(
        goal='Sort the steel hardware on the white inspection pad by TYPE: screws into the "screws" cup, nuts into the "nuts" cup, washers into the "washers" cup. A part you cannot identify goes to the "unknown" tray. Some parts may be brass, aluminium or stainless: the magnet cannot lift those; after one failed attempt leave them.',
        scene="the wooden arm with the small servos is the robot (ignore it), the three clear cups beyond the pad are the targets (see the container list for which is which), the small blue tray beside the arm base is for unknown parts, the black-and-white squares are calibration markers (ignore them). Only list loose parts lying on the white pad.",
        kinds=["screw", "nut", "washer", "other"],
        materials=["zinc_steel", "black_steel", "stainless", "aluminum", "brass", "copper", "unknown"],
        targets_help="screws / nuts / washers: the three clear cups; unknown: the small tray.",
    ),
    pieces="screws_nuts_washers",
    home_servo=(150, 125, 75),  # parked up and to the +y side: clear of the pad, the cups and the camera view
    wrist_y=-0.0055,
    cameras=_TARAS_CAMS,
)

_LID = dict(size=(0.020, 0.020, 0.012), shape="lid")
THEKER_V1 = _derive(
    TARAS_V1,
    name="theker_v1",
    title="THEKER shopping sheet v4 · wood 10×10 links on a lazy-susan · 2× MG90S + MG946R · Uno + sensor shield · 5 V relay · Ø20 magnet · 3 shallow lids",
    L1=0.070,
    L2=0.070,
    HANG=0.035,
    SHOULDER_Z=0.080,
    TURRET_Z=0.026,
    magnet=dict(radius=0.010, height=0.015, hold_force=25.0, d0=0.0025, max_accel=20.0, mass=0.025, voltage=12, switch="relay", relay_delay_s=0.010),
    workspace=dict(r=(0.055, 0.108), yaw=(math.radians(-40), math.radians(40))),
    travel_z=0.055,  # lids are 1.2 cm tall: a 2 cm screw hanging under the magnet still clears them
    pick_z=0.011,
    targets={
        "screws": dict(pos=_polar(0.125, -40), drop_z=0.040, label="shallow white lid for screws", **_LID),
        "nuts": dict(pos=_polar(0.128, 0), drop_z=0.040, label="shallow white lid for nuts", **_LID),
        "washers": dict(pos=_polar(0.125, 40), drop_z=0.040, label="shallow white lid for washers", **_LID),
        "unknown": dict(pos=_polar(0.070, -85), drop_z=0.040, size=(0.028, 0.02, 0.010), shape="tray", label="small matte tray beside the arm base for parts you cannot identify"),
    },
    board=dict(size=(0.35, 0.25, 0.018), center=(0.09, 0.0), material="wood"),  # wooden chopping board, clamped to the table
    markers={0: (0.035, -0.10), 1: (0.195, -0.10), 2: (0.195, 0.10), 3: (0.035, 0.10)},
    marker_size=0.025,
    phone_cam=dict(pos=(0.085, 0.0, 0.27), fovy_deg=52.0, width=1280, height=960),
    cine_cam=dict(pos=(0.40, -0.34, 0.26), lookat=(0.08, 0.01, 0.03), fovy_deg=36.0, width=1200, height=900),
    cameras={
        "A": dict(pos=(0.085, 0.0, 0.27), lookat=(0.085, 0.0, 0.0), fovy_deg=52.0, width=1280, height=960),  # phone on the gooseneck, straight down over the tray
        "B": dict(pos=(0.02, -0.30, 0.28), lookat=(0.085, 0.0, 0.0), fovy_deg=36.0, width=1280, height=960),  # optional second phone, right side
    },
    task=dict(
        goal='Sort the steel hardware on the blue card by TYPE: screws into the "screws" lid, nuts into the "nuts" lid, washers into the "washers" lid. A part you cannot identify goes to the "unknown" tray. A part may be brass or another non-magnetic metal: the magnet cannot lift it; after one failed centred attempt leave it.',
        scene="the wooden arm with the small servos is the robot (ignore it), the three shallow white lids beyond the card are the targets (see the container list), the small tray beside the arm base is for unknown parts, the black-and-white squares are calibration markers (ignore them). Only list loose parts lying on the blue card.",
        kinds=["screw", "nut", "washer", "other"],
        materials=["zinc_steel", "black_steel", "stainless", "aluminum", "brass", "copper", "unknown"],
        targets_help="screws / nuts / washers: the three shallow lids; unknown: the small tray.",
    ),
    pieces="m3_m4",
    home_servo=(150, 125, 75),
)


TARAS_CONVEYOR = _derive(
    TARAS_V1,
    name="taras_conveyor",
    title=TARAS_V1.title + " · DS04-NFC conveyor",
    surface_z=0.012,
    travel_z=0.062,
    board=dict(size=(0.30, 0.42, 0.018), center=(0.07, 0.0), material="wood"),
    markers={0: (-0.05, -0.113), 1: (0.19, -0.113), 2: (0.19, 0.113), 3: (-0.05, 0.113)},
    workspace=dict(r=(0.055, 0.110), yaw=(math.radians(-32), math.radians(32))),
    targets={
        "screws": dict(pos=_polar(0.110, -80), drop_z=0.060, label="clear cup for screws", **_CUP),
        "nuts": dict(pos=_polar(0.110, 80), drop_z=0.060, label="clear cup for nuts", **_CUP),
        "washers": dict(pos=_polar(0.065, -82), drop_z=0.060, label="clear cup for washers", **_CUP),
        "unknown": dict(pos=_polar(0.065, 82), drop_z=0.045, size=(0.028, 0.02, 0.012), shape="tray", label="small blue tray for parts you cannot identify"),
    },
    phone_cam=dict(pos=(0.085, 0.0, 0.32), fovy_deg=55.0, width=1280, height=960),
    cine_cam=dict(pos=(0.36, -0.40, 0.27), lookat=(0.07, 0.02, 0.03), fovy_deg=38.0, width=1200, height=900),
    conveyor=dict(x=0.090, width=0.070, length=0.30, roller_r=0.012, speed=0.05, pick_zone_y=(-0.045, 0.045), spawn_y=-0.115, end_tray_y=0.165, servo="DS04-NFC", batch=4),
    task=dict(
        goal=TARAS_V1.task["goal"] + " Parts arrive on a conveyor belt: only the ones inside the pick zone (between the two white lines across the belt) are reachable. When you are done with the current view return done=true; the belt then advances and brings new parts. Parts that leave the pick zone unsorted fall in the tray at the belt end.",
        scene="the wooden arm with the small servos is the robot (ignore it), the dark conveyor belt runs across the image in front of it, the clear cups and the small blue tray are the targets (see the container list), the black-and-white squares are calibration markers (ignore them). Only list loose parts lying on the belt inside the pick zone.",
        kinds=["screw", "nut", "washer", "other"],
        materials=["zinc_steel", "black_steel", "stainless", "aluminum", "brass", "copper", "unknown"],
        targets_help="screws / nuts / washers: the three clear cups; unknown: the small tray.",
    ),
)

TARAS_KITTING = _derive(
    TARAS_V1,
    name="taras_kitting",
    title=TARAS_V1.title + " · kitting",
    targets={
        "kit_A": dict(pos=_polar(0.128, -42), drop_z=0.060, label="clear cup for kit A", **_CUP),
        "kit_B": dict(pos=_polar(0.130, 0), drop_z=0.060, label="clear cup for kit B", **_CUP),
        "kit_C": dict(pos=_polar(0.128, 42), drop_z=0.060, label="clear cup for kit C", **_CUP),
        "unknown": dict(_TARAS_UNKNOWN, label="small blue tray beside the arm base for leftovers"),
    },
    task=dict(
        goal='Assemble hardware KITS: each of the three cups (kit_A, kit_B, kit_C) must end up with exactly ONE screw, ONE nut and ONE washer. Fill kits one at a time in order A, B, C. Keep count of what you already dropped in each cup using the feedback; a cup that already has a nut must not get a second nut. Parts left over after all kits are complete go to the "unknown" tray. Non-steel parts cannot be lifted; leave them after one failed attempt.',
        scene=THEKER_V1.task["scene"],
        kinds=["screw", "nut", "washer", "other"],
        materials=["zinc_steel", "black_steel", "stainless", "aluminum", "brass", "copper", "unknown"],
        targets_help="kit_A / kit_B / kit_C: the three shallow lids; unknown: the small tray.",
    ),
    pieces="kitting_set",
)

TARAS_GRADING = _derive(
    TARAS_V1,
    name="taras_grading",
    title=TARAS_V1.title + " · length grading + rust rejection",
    targets={
        "short": dict(pos=_polar(0.128, -42), drop_z=0.060, label="clear cup for short screws (≤ 16 mm)", **_CUP),
        "long": dict(pos=_polar(0.130, 0), drop_z=0.060, label="clear cup for long screws (≥ 25 mm)", **_CUP),
        "reject": dict(pos=_polar(0.128, 42), drop_z=0.060, label="clear cup for rusty or damaged screws", **_CUP),
        "unknown": dict(_TARAS_UNKNOWN, label="small blue tray beside the arm base for anything that is not a screw"),
    },
    task=dict(
        goal='Quality-grade the steel screws on the pad. Estimate each screw\'s length from the image (the calibration markers are 25 mm squares: use them as a scale): screws up to 16 mm go to "short", screws 25 mm or longer go to "long". Any screw with visible rust or a damaged head goes to "reject" regardless of length. Anything that is not a screw goes to "unknown".',
        scene=THEKER_V1.task["scene"],
        kinds=["screw", "nut", "washer", "other"],
        materials=["zinc_steel", "black_steel", "rusty_steel", "stainless", "aluminum", "brass", "unknown"],
        targets_help="short / long / reject: the three shallow lids; unknown: the small tray.",
    ),
    pieces="grading_set",
)

BUILDS = {b.name: b for b in (HOBBY_V1, TARAS_V1, TARAS_CONVEYOR, TARAS_KITTING, TARAS_GRADING, THEKER_V1)}


# ------------------------------------------------------------------ active build → module globals
def configure(name: str) -> BuildConfig:
    global CFG, BUILD, L1, L2, HANG, SHOULDER_Z, TURRET_Z, JOINTS, MAGNET, MAGNET_RADIUS, MAGNET_HEIGHT, MAGNET_HOLD_FORCE, MAGNET_D0, MAGNET_MAX_ACCEL
    global WORKSPACE, SURFACE_Z, TRAVEL_Z, PICK_Z, TARGETS, BIN, BOARD, MARKERS, MARKER_SIZE, PHONE_CAM, CINE_CAM, TASK, CONVEYOR, HOME_SERVO, WRIST_Y, CAMERAS
    CFG = BUILDS[name]
    BUILD = CFG.name
    L1, L2, HANG, SHOULDER_Z, TURRET_Z = CFG.L1, CFG.L2, CFG.HANG, CFG.SHOULDER_Z, CFG.TURRET_Z
    JOINTS = CFG.joints
    MAGNET = CFG.magnet
    MAGNET_RADIUS, MAGNET_HEIGHT = MAGNET["radius"], MAGNET["height"]
    MAGNET_HOLD_FORCE, MAGNET_D0, MAGNET_MAX_ACCEL = MAGNET["hold_force"], MAGNET["d0"], MAGNET["max_accel"]
    WORKSPACE, SURFACE_Z, TRAVEL_Z, PICK_Z = CFG.workspace, CFG.surface_z, CFG.travel_z, CFG.pick_z
    TARGETS = CFG.targets
    BIN = next(iter(TARGETS.values()))
    BOARD, MARKERS, MARKER_SIZE = CFG.board, CFG.markers, CFG.marker_size
    PHONE_CAM, CINE_CAM, TASK, CONVEYOR, HOME_SERVO = CFG.phone_cam, CFG.cine_cam, CFG.task, CFG.conveyor, CFG.home_servo
    WRIST_Y = CFG.wrist_y
    CAMERAS = CFG.cameras or {"A": dict(CFG.phone_cam, lookat=(CFG.phone_cam["pos"][0], CFG.phone_cam["pos"][1], 0.0))}
    return CFG


def joint_servo(joint: str) -> dict:
    return SERVO_MODELS[JOINTS[joint]["servo"]]


# ------------------------------------------------------------------ materials (PBR)
@dataclass
class Material:
    name: str
    base_color: tuple  # linear RGB 0-1
    metallic: float = 0.0
    roughness: float = 0.5
    anisotropic: float = 0.0
    emission: tuple | None = None
    texture: str | None = None  # png in assets/
    procedural: str | None = None  # blender-only procedural preset name
    alpha: float = 1.0  # < 1 → see-through plastic in the MuJoCo preview
    transmission: float = 0.0  # Blender glass-like transmission


MATERIALS = {
    "mdf": Material("mdf", (0.40, 0.29, 0.18), 0.0, 0.75, texture="mdf.png", procedural="mdf"),
    "wood": Material("wood", (0.62, 0.46, 0.28), 0.0, 0.55, texture="wood.png", procedural="wood"),
    "foam_white": Material("foam_white", (0.92, 0.92, 0.90), 0.0, 0.85),
    "card_blue": Material("card_blue", (0.10, 0.22, 0.42), 0.0, 0.92),  # matte blue A3 card
    "lid_white": Material("lid_white", (0.88, 0.88, 0.86), 0.0, 0.5),  # shallow plastic lids used as bins
    "shield_blue": Material("shield_blue", (0.05, 0.18, 0.45), 0.0, 0.35),
    "wago_orange": Material("wago_orange", (0.95, 0.45, 0.05), 0.0, 0.4),
    "clamp_red": Material("clamp_red", (0.55, 0.05, 0.05), 0.2, 0.4),
    "cap_black": Material("cap_black", (0.05, 0.05, 0.08), 0.0, 0.3),
    "plastic_clear": Material("plastic_clear", (0.90, 0.92, 0.95), 0.0, 0.08, alpha=0.35, transmission=0.92),
    "pla_orange": Material("pla_orange", (0.90, 0.35, 0.05), 0.0, 0.45),
    "pla_black": Material("pla_black", (0.03, 0.03, 0.03), 0.0, 0.5),
    "servo_black": Material("servo_black", (0.02, 0.02, 0.025), 0.0, 0.35),
    "servo_blue": Material("servo_blue", (0.05, 0.12, 0.35), 0.0, 0.4),  # MG90S shells are blue
    "servo_horn": Material("servo_horn", (0.85, 0.85, 0.85), 0.0, 0.4),
    "pcb_blue": Material("pcb_blue", (0.02, 0.10, 0.22), 0.0, 0.35, texture="pcb.png"),
    "pcb_red": Material("pcb_red", (0.35, 0.02, 0.02), 0.0, 0.35),
    "relay_blue": Material("relay_blue", (0.05, 0.15, 0.55), 0.0, 0.3),
    "header_black": Material("header_black", (0.02, 0.02, 0.02), 0.0, 0.3),
    "chip_black": Material("chip_black", (0.04, 0.04, 0.04), 0.0, 0.25),
    "psu_black": Material("psu_black", (0.03, 0.03, 0.03), 0.0, 0.6),
    "tin": Material("tin", (0.75, 0.76, 0.78), 1.0, 0.3),
    "breadboard": Material("breadboard", (0.90, 0.90, 0.88), 0.0, 0.4, texture="breadboard.png"),
    "bin_blue": Material("bin_blue", (0.05, 0.20, 0.55), 0.0, 0.35),
    "belt_rubber": Material("belt_rubber", (0.08, 0.08, 0.085), 0.0, 0.8),
    "wire_red": Material("wire_red", (0.80, 0.03, 0.02), 0.0, 0.45),
    "wire_black": Material("wire_black", (0.01, 0.01, 0.01), 0.0, 0.45),
    "wire_yellow": Material("wire_yellow", (0.85, 0.65, 0.02), 0.0, 0.45),
    "wire_brown": Material("wire_brown", (0.25, 0.12, 0.05), 0.0, 0.45),
    "wire_orange": Material("wire_orange", (0.90, 0.30, 0.02), 0.0, 0.45),
    "wire_grey": Material("wire_grey", (0.35, 0.35, 0.36), 0.0, 0.5),
    "magnet_body": Material("magnet_body", (0.03, 0.03, 0.03), 0.0, 0.3),
    "magnet_face": Material("magnet_face", (0.70, 0.71, 0.72), 1.0, 0.25),
    "steel_zinc": Material("steel_zinc", (0.66, 0.68, 0.72), 1.0, 0.30, anisotropic=0.3),
    "steel_black": Material("steel_black", (0.06, 0.06, 0.065), 0.9, 0.35),
    "steel_rusty": Material("steel_rusty", (0.36, 0.17, 0.08), 0.5, 0.85, procedural="rust"),
    "stainless": Material("stainless", (0.72, 0.72, 0.73), 1.0, 0.22, anisotropic=0.5),
    "aluminum": Material("aluminum", (0.78, 0.79, 0.80), 1.0, 0.55),
    "brass": Material("brass", (0.80, 0.58, 0.20), 1.0, 0.30),
    "copper": Material("copper", (0.80, 0.42, 0.30), 1.0, 0.30),
    "phone_black": Material("phone_black", (0.02, 0.02, 0.02), 0.2, 0.2),
    "phone_glass": Material("phone_glass", (0.05, 0.05, 0.06), 0.0, 0.05),
    "marker": Material("marker", (1.0, 1.0, 1.0), 0.0, 0.6),
}

FERROUS = {"steel_zinc", "steel_black", "steel_rusty"}


# ------------------------------------------------------------------ geometry
@dataclass
class Geom:
    kind: str  # box | cylinder | hexprism | wire | marker
    size: tuple
    pos: tuple = (0.0, 0.0, 0.0)
    quat: tuple = (1.0, 0.0, 0.0, 0.0)
    material: str = "pla_orange"
    collide: bool = True
    points: tuple = ()
    marker_id: int | None = None
    name: str | None = None
    contype: int | None = None  # MuJoCo collision bitmasks (None → defaults)
    conaffinity: int | None = None


@dataclass
class Body:
    name: str
    pos: tuple = (0.0, 0.0, 0.0)
    quat: tuple = (1.0, 0.0, 0.0, 0.0)
    parent: str = "world"
    joint: dict | None = None
    geoms: list = field(default_factory=list)
    mass: float | None = None
    piece: dict | None = None  # {material, ferrous, kind, label, target, ...}


def quat_z(deg: float) -> tuple:
    a = math.radians(deg) / 2
    return (math.cos(a), 0.0, 0.0, math.sin(a))


def quat_y(deg: float) -> tuple:
    a = math.radians(deg) / 2
    return (math.cos(a), 0.0, math.sin(a), 0.0)


def quat_x(deg: float) -> tuple:
    a = math.radians(deg) / 2
    return (math.cos(a), math.sin(a), 0.0, 0.0)


def servo_geoms(prefix: str, model: str, pos, quat=(1, 0, 0, 0)) -> list[Geom]:
    """Servo body + mounting flange + output horn, centred at pos, output axis along local +z."""
    s = SERVO_MODELS[model]
    w, d, h = s["w"], s["d"], s["h"]
    mat = "servo_blue" if model == "MG90S" else "servo_black"
    small = model == "MG90S"
    return [
        Geom("box", (w / 2, d / 2, h / 2), pos, quat, mat, name=f"{prefix}_servo"),
        Geom("box", (w / 2 + 0.006, d / 2 + 0.0005, 0.0012), (pos[0], pos[1], pos[2] + h / 2 - 0.28 * h), quat, mat, collide=False),
        Geom("cylinder", (0.0045 if small else 0.006, 0.002), (pos[0] + (0.0055 if small else 0.010), pos[1], pos[2] + h / 2 + 0.002), quat, "tin", collide=False),
    ]


def _wire(world: Body, name, mat, pts, r=0.0011):
    world.geoms.append(Geom("wire", (r,), material=mat, collide=False, points=tuple(pts), name=name))


def _container(world: Body, name: str, t: dict) -> None:
    """Tray (open blue box) or cup (clear plastic cylinder) at a target."""
    x, y = t["pos"]
    if t["shape"] == "lid":
        # shallow plastic lid / small dish: floor + low rim (rim ≤ 2 cm so nothing collides with the swing)
        r, hh = t["size"][0], t["size"][2]
        world.geoms.append(Geom("cylinder", (r, 0.0012), (x, y, 0.0012), material="lid_white", name=f"{name}_floor"))
        n = 14
        for i in range(n):
            a = 2 * math.pi * i / n
            seg = math.pi * r / n * 1.05
            world.geoms.append(Geom("box", (0.0015, seg, hh), (x + r * math.cos(a), y + r * math.sin(a), hh), quat_z(math.degrees(a)), material="lid_white", name=f"{name}_wall" if i == 0 else None))
        return
    if t["shape"] == "cup":
        r, hh = t["size"][0], t["size"][2]
        wall = 0.0012
        world.geoms.append(Geom("cylinder", (r, 0.001), (x, y, 0.001), material="plastic_clear", name=f"{name}_floor"))
        n = 16
        for i in range(n):  # cup wall from thin boxes (collision needs convex pieces)
            a = 2 * math.pi * i / n
            seg = math.pi * r / n * 1.05
            world.geoms.append(Geom("box", (wall, seg, hh), (x + r * math.cos(a), y + r * math.sin(a), hh), quat_z(math.degrees(a)), material="plastic_clear", name=f"{name}_wall" if i == 0 else None))
    else:
        hw, hd, hh = t["size"]
        tw = 0.0015
        world.geoms += [
            Geom("box", (hw, hd, tw), (x, y, tw), material="bin_blue", name=f"{name}_floor"),
            Geom("box", (hw, tw, hh), (x, y - hd, hh), material="bin_blue"),
            Geom("box", (hw, tw, hh), (x, y + hd, hh), material="bin_blue"),
            Geom("box", (tw, hd, hh), (x - hw, y, hh), material="bin_blue"),
            Geom("box", (tw, hd, hh), (x + hw, y, hh), material="bin_blue"),
        ]


def _arduino(world: Body, ax: float, ay: float, az: float = 0.0) -> None:
    world.geoms += [
        Geom("box", (0.0343, 0.0267, 0.0008), (ax, ay, az + 0.0008), material="pcb_blue", name="arduino"),
        Geom("box", (0.0343, 0.0267, 0.0035), (ax, ay, az + 0.0035), material="pcb_blue", collide=True),
        Geom("box", (0.0190, 0.0013, 0.0045), (ax + 0.006, ay + 0.0245, az + 0.011), material="header_black", collide=False),
        Geom("box", (0.0100, 0.0013, 0.0045), (ax - 0.022, ay + 0.0245, az + 0.011), material="header_black", collide=False),
        Geom("box", (0.0100, 0.0013, 0.0045), (ax + 0.006, ay - 0.0245, az + 0.011), material="header_black", collide=False),
        Geom("box", (0.0080, 0.0013, 0.0045), (ax + 0.024, ay - 0.0245, az + 0.011), material="header_black", collide=False),
        Geom("box", (0.0060, 0.0060, 0.0055), (ax - 0.028, ay + 0.012, az + 0.012), material="tin", collide=False),
        Geom("box", (0.0045, 0.0055, 0.0055), (ax - 0.028, ay - 0.014, az + 0.012), material="chip_black", collide=False),
        Geom("box", (0.0180, 0.0045, 0.0020), (ax + 0.008, ay - 0.008, az + 0.009), material="chip_black", collide=False),
        Geom("cylinder", (0.0025, 0.0020), (ax - 0.016, ay - 0.004, az + 0.009), material="tin", collide=False),
        Geom("box", (0.0010, 0.0006, 0.0006), (ax - 0.006, ay + 0.010, az + 0.008), material="wire_yellow", collide=False),
    ]


def _phone_boom(world: Body, cam: dict, wooden: bool = False) -> None:
    px, py, pz = cam["pos"]
    world.geoms += [
        Geom("box", (0.0355, 0.0735, 0.004), (px, py, pz + 0.006), material="phone_black", collide=False, name="phone"),
        Geom("cylinder", (0.006, 0.0005), (px + 0.020, py + 0.045, pz + 0.0015), material="phone_glass", collide=False),
    ]
    if cam.get("lookat") is not None and abs(cam["pos"][0] - cam["lookat"][0]) + abs(cam["pos"][1] - cam["lookat"][1]) > 0.05:
        return  # oblique phones stand on small tripods (drawn by _tripods); no boom
    if wooden:
        world.geoms += [
            Geom("box", (0.012, 0.012, (pz + 0.02) / 2), (px + 0.03, py + 0.19, (pz + 0.02) / 2 - 0.018), material="wood", collide=False),
            Geom("box", (0.012, 0.10, 0.008), (px + 0.03, py + 0.10, pz + 0.018), material="wood", collide=False),
        ]
    else:
        world.geoms += [
            Geom("box", (0.006, 0.006, 0.20), (px + 0.045, py + 0.20, pz + 0.21), material="wire_grey", collide=False),
            Geom("box", (0.006, 0.20, 0.006), (px + 0.045, py + 0.40, pz + 0.006), material="wire_grey", collide=False),
        ]


def _tripods(world: Body, cfg: BuildConfig) -> None:
    """A phone on a small tabletop tripod at each camera position (cosmetic)."""
    cams = cfg.cameras or {}
    for name, cam in cams.items():
        px, py, pz = cam["pos"]
        lx, ly, lz = cam["lookat"]
        yaw = math.degrees(math.atan2(ly - py, lx - px))
        pitch = -math.degrees(math.atan2(pz - lz, math.hypot(lx - px, ly - py)))
        q = (math.cos(math.radians(yaw) / 2), 0.0, 0.0, math.sin(math.radians(yaw) / 2))
        # phone body: a slab facing the look direction (local +x = look), tilted down by pitch
        qp = quat_mul(q, quat_y(-pitch))
        world.geoms.append(Geom("box", (0.004, 0.0355, 0.0735), (px, py, pz), qp, material="phone_black", collide=False, name=f"phone_{name}"))
        world.geoms.append(Geom("cylinder", (0.006, 0.0006), (px, py, pz), quat_mul(qp, quat_y(90)), material="phone_glass", collide=False))
        # tripod: centre column + three legs to the floor plane of the board (z=0 or the desk)
        base_z = 0.0 if abs(px - cfg.board["center"][0]) < cfg.board["size"][0] / 2 and abs(py - cfg.board["center"][1]) < cfg.board["size"][1] / 2 else -cfg.board["size"][2]
        col_h = (pz - 0.04 - base_z) / 2
        world.geoms.append(Geom("cylinder", (0.006, col_h), (px, py, base_z + col_h), material="wire_grey", collide=False))
        for k in range(3):
            a = math.radians(120 * k + yaw + 60)
            world.geoms.append(Geom("wire", (0.004,), material="wire_grey", collide=False, points=((px, py, base_z + 0.10), (px + 0.09 * math.cos(a), py + 0.09 * math.sin(a), base_z))))


def quat_mul(q1, q2) -> tuple:
    w1, x1, y1, z1 = q1
    w2, x2, y2, z2 = q2
    return (w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2, w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2, w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2, w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2)


def _magnet_wrist(cfg: BuildConfig, pos, wire_offsets=(0.006, 0.008)) -> Body:
    m = cfg.magnet
    wrist = Body("wrist", pos=pos, parent="forearm", joint=dict(name="wrist", type="hinge", axis=(0, -1, 0), range=(-1.55, 1.55), damping=0.004, armature=0.00005, stiffness=0.0))  # ≈2× critical for a 40 g magnet on a 4 cm hanger: settles in ~0.3 s without swinging
    hang, mr, mh = cfg.HANG, m["radius"], m["height"]
    wrist.geoms += [
        Geom("cylinder", (0.0025, 0.009), (0, 0, 0), quat_x(90), material="tin", collide=False),
        Geom("box", (0.003, 0.003, (hang - mh) / 2), (0.0, 0.0, -(hang - mh) / 2), material="pla_black", collide=False, name="magnet_bracket"),
        # the magnet does not collide: holding is a weld, and a colliding face would crush parts that stood up
        Geom("cylinder", (mr, mh / 2), (0.0, 0.0, -(hang - mh / 2)), material="magnet_body", collide=False, name="magnet_body"),
        Geom("cylinder", (mr, 0.0005), (0.0, 0.0, -(hang - 0.0005)), material="magnet_face", collide=False, name="magnet_face"),
        Geom("wire", (0.0009,), material="wire_red", collide=False, points=((0.0, wire_offsets[0], -0.006), (0.004, wire_offsets[0] + 0.005, -0.020), (0.006, wire_offsets[0] + 0.003, -(hang - mh + 0.002)))),
        Geom("wire", (0.0009,), material="wire_black", collide=False, points=((0.0, wire_offsets[1], -0.004), (0.006, wire_offsets[1] + 0.005, -0.018), (0.008, wire_offsets[1] + 0.003, -(hang - mh + 0.002)))),
    ]
    wrist.mass = m["mass"] + 0.010
    return wrist


# ------------------------------------------------------------------ piece sets
def _piece(name, mat, kind, mass, target, extra=None) -> Body:
    return Body(name, parent="world", joint=dict(type="free"), mass=mass, piece=dict(material=mat, ferrous=mat in FERROUS, kind=kind, label=name, target=target, **(extra or {})))


def hexnut(name, af, h, mat, mass, target=None, extra=None):
    b = _piece(name, mat, "nut", mass, target, extra)
    b.geoms.append(Geom("hexprism", (af / math.sqrt(3), h / 2), (0, 0, 0), material=mat, name=f"{name}_hex"))
    b.geoms.append(Geom("cylinder", (af * 0.42, h / 2 + 0.0002), (0, 0, 0), material="steel_black" if mat != "steel_black" else "chip_black", collide=False, name=f"{name}_hole"))
    return b


def washer(name, od, t, mat, mass, target=None, extra=None):
    b = _piece(name, mat, "washer", mass, target, extra)
    b.geoms.append(Geom("cylinder", (od / 2, t / 2), (0, 0, 0), material=mat, name=f"{name}_ring"))
    b.geoms.append(Geom("cylinder", (od * 0.27, t / 2 + 0.0002), (0, 0, 0), material="__surface__", collide=False, name=f"{name}_hole"))
    return b


def bolt(name, af, head_h, shaft_d, length, mat, mass, target=None, extra=None):
    """Hex bolt lying on its side along +x: head at x=0, shaft to +x."""
    b = _piece(name, mat, "bolt", mass, target, dict(length_mm=round(length * 1000), **(extra or {})))
    b.geoms.append(Geom("hexprism", (af / math.sqrt(3), head_h / 2), (0, 0, 0), quat_y(90), material=mat, name=f"{name}_head"))
    b.geoms.append(Geom("cylinder", (shaft_d / 2, length / 2), (head_h / 2 + length / 2, 0, 0), quat_y(90), material=mat, name=f"{name}_shaft"))
    return b


def screw(name, head_d, head_h, shaft_d, length, mat, mass, target=None, extra=None):
    """Pan-head machine screw lying on its side along +x, Phillips cross on the head."""
    b = _piece(name, mat, "screw", mass, target, dict(length_mm=round(length * 1000), **(extra or {})))
    b.geoms.append(Geom("cylinder", (head_d / 2, head_h / 2), (0, 0, 0), quat_y(90), material=mat, name=f"{name}_head"))
    b.geoms.append(Geom("cylinder", (shaft_d / 2, length / 2), (head_h / 2 + length / 2, 0, 0), quat_y(90), material=mat, name=f"{name}_shaft"))
    b.geoms.append(Geom("box", (0.0003, head_d * 0.32, 0.0004), (-head_h / 2 - 0.0002, 0, 0), material="chip_black", collide=False))
    b.geoms.append(Geom("box", (0.0003, 0.0004, head_d * 0.32), (-head_h / 2 - 0.0002, 0, 0), material="chip_black", collide=False))
    return b


def pieces_mixed_metals() -> list[Body]:
    fe = "iron_bin"
    out = [
        hexnut("nut_m8_zinc", 0.013, 0.0065, "steel_zinc", 0.0055, target=fe),
        hexnut("nut_m8_brass", 0.013, 0.0065, "brass", 0.0058),
        hexnut("nut_m6_black", 0.010, 0.0050, "steel_black", 0.0025, target=fe),
        hexnut("nut_m10_stainless", 0.017, 0.0080, "stainless", 0.0120),
        hexnut("nut_m5_stainless", 0.008, 0.0040, "stainless", 0.0012),
        bolt("bolt_m6_zinc", 0.010, 0.004, 0.006, 0.025, "steel_zinc", 0.0080, target=fe),
        bolt("bolt_m6_stainless", 0.010, 0.004, 0.006, 0.025, "stainless", 0.0080),
        washer("washer_m8_zinc", 0.016, 0.0016, "steel_zinc", 0.0018, target=fe),
        washer("washer_m8_copper", 0.016, 0.0015, "copper", 0.0019),
        washer("washer_m10_alu", 0.020, 0.0020, "aluminum", 0.0012),
    ]
    standoff = _piece("standoff_alu", "aluminum", "standoff", 0.0020, None)
    standoff.geoms.append(Geom("cylinder", (0.004, 0.0075), (0, 0, 0), quat_y(90), material="aluminum", name="standoff_alu_cyl"))
    out.append(standoff)
    return out


def pieces_screws_nuts_washers() -> list[Body]:
    """First live-demo set: 'place parts in one layer with gaps' (proposal) → 6 steel parts + 1 brass distractor."""
    return [
        screw("screw_m4x16_zinc", 0.0075, 0.0026, 0.004, 0.016, "steel_zinc", 0.0028, target="screws"),
        screw("screw_m5x20_black", 0.0095, 0.0033, 0.005, 0.020, "steel_black", 0.0052, target="screws"),
        hexnut("nut_m5_zinc", 0.008, 0.0040, "steel_zinc", 0.0012, target="nuts"),
        hexnut("nut_m6_zinc", 0.010, 0.0050, "steel_zinc", 0.0024, target="nuts"),
        washer("washer_m5_zinc", 0.010, 0.0010, "steel_zinc", 0.0005, target="washers"),
        washer("washer_m6_zinc", 0.012, 0.0016, "steel_zinc", 0.0010, target="washers"),
        screw("screw_m4x16_brass", 0.0075, 0.0026, 0.004, 0.016, "brass", 0.0030),  # distractor: cannot be lifted
    ]


def pieces_kitting_set() -> list[Body]:
    out = []
    for k in "ABC":
        out.append(screw(f"screw_m4x20_{k}", 0.0070, 0.0026, 0.004, 0.020, "steel_zinc", 0.0032, target=None, extra={"kit_part": "screw"}))
        out.append(hexnut(f"nut_m4_{k}", 0.007, 0.0032, "steel_zinc", 0.0008, target=None, extra={"kit_part": "nut"}))
        out.append(washer(f"washer_m4_{k}", 0.009, 0.0008, "steel_zinc", 0.0003, target=None, extra={"kit_part": "washer"}))
    out.append(washer("washer_m4_extra", 0.009, 0.0008, "steel_zinc", 0.0003, target="unknown", extra={"kit_part": "washer"}))
    return out


def pieces_grading_set() -> list[Body]:
    return [
        screw("screw_m4x12", 0.0075, 0.0026, 0.004, 0.012, "steel_zinc", 0.0022, target="short"),
        screw("screw_m4x16", 0.0075, 0.0026, 0.004, 0.016, "steel_zinc", 0.0028, target="short"),
        screw("screw_m5x16", 0.0095, 0.0033, 0.005, 0.016, "steel_zinc", 0.0044, target="short"),
        screw("screw_m4x30", 0.0075, 0.0026, 0.004, 0.030, "steel_zinc", 0.0044, target="long"),
        screw("screw_m5x30", 0.0095, 0.0033, 0.005, 0.030, "steel_zinc", 0.0070, target="long"),
        screw("screw_m4x25_black", 0.0075, 0.0026, 0.004, 0.025, "steel_black", 0.0036, target="long"),
        screw("screw_m4x16_rusty", 0.0075, 0.0026, 0.004, 0.016, "steel_rusty", 0.0028, target="reject"),
        screw("screw_m5x30_rusty", 0.0095, 0.0033, 0.005, 0.030, "steel_rusty", 0.0070, target="reject"),
        hexnut("nut_m5_zinc", 0.008, 0.0040, "steel_zinc", 0.0012, target="unknown"),
    ]


def pieces_m3_m4() -> list[Body]:
    """The two ferretería boxes (M3×20 and M4×20 with nuts and washers) plus a couple of parts from home."""
    return [
        screw("screw_m3x20_zinc", 0.0055, 0.002, 0.003, 0.020, "steel_zinc", 0.0014, target="screws"),
        screw("screw_m4x20_zinc", 0.0070, 0.0026, 0.004, 0.020, "steel_zinc", 0.0032, target="screws"),
        screw("screw_m4x20_black", 0.0070, 0.0026, 0.004, 0.020, "steel_black", 0.0032, target="screws"),
        hexnut("nut_m3_zinc", 0.0055, 0.0024, "steel_zinc", 0.0004, target="nuts"),
        hexnut("nut_m4_zinc", 0.0070, 0.0032, "steel_zinc", 0.0008, target="nuts"),
        hexnut("nut_m4_zinc_b", 0.0070, 0.0032, "steel_zinc", 0.0008, target="nuts"),
        washer("washer_m3_zinc", 0.0070, 0.0005, "steel_zinc", 0.0001, target="washers"),
        washer("washer_m4_zinc", 0.0090, 0.0008, "steel_zinc", 0.0003, target="washers"),
        washer("washer_m4_zinc_b", 0.0090, 0.0008, "steel_zinc", 0.0003, target="washers"),
        screw("screw_m4x16_brass", 0.0070, 0.0026, 0.004, 0.016, "brass", 0.0030),  # from home: brass, cannot be lifted
    ]


PIECE_SETS = {"mixed_metals": pieces_mixed_metals, "m3_m4": pieces_m3_m4, "screws_nuts_washers": pieces_screws_nuts_washers, "kitting_set": pieces_kitting_set, "grading_set": pieces_grading_set}


# ------------------------------------------------------------------ builds: geometry
def _common_static(cfg: BuildConfig) -> Body:
    world = Body("world_static", parent="world")
    cx, cy = cfg.board["center"]
    sx, sy, sz = cfg.board["size"]
    world.geoms.append(Geom("box", (sx / 2, sy / 2, sz / 2), (cx, cy, -sz / 2), material=cfg.board["material"], name="board"))
    world.geoms.append(Geom("box", (1.5, 1.5, 0.02), (0.1, 0.0, -0.75 - 0.02), material="wire_grey", name="floor"))
    for mid, (mx, my) in cfg.markers.items():
        world.geoms.append(Geom("marker", (cfg.marker_size / 2, cfg.marker_size / 2, 0.0003), (mx, my, 0.0003), material="marker", collide=False, marker_id=mid, name=f"aruco_{mid}"))
    for name, t in cfg.targets.items():
        _container(world, name, t)
    return world


def build_hobby_v1(cfg: BuildConfig) -> list[Body]:
    bodies: list[Body] = []
    world = _common_static(cfg)
    ax, ay = -0.13, 0.12
    _arduino(world, ax, ay)
    bbx, bby = -0.13, 0.215
    world.geoms.append(Geom("box", (0.0825, 0.0275, 0.0045), (bbx, bby, 0.0045), material="breadboard", name="breadboard"))
    mx, my = -0.06, 0.26
    world.geoms += [
        Geom("box", (0.0165, 0.012, 0.0008), (mx, my, 0.0008), material="pcb_red", name="mosfet"),
        Geom("box", (0.0050, 0.0022, 0.0075), (mx + 0.008, my, 0.0083), material="chip_black", collide=False),
        Geom("box", (0.0050, 0.0022, 0.0010), (mx + 0.008, my, 0.0163), material="tin", collide=False),
        Geom("box", (0.0060, 0.0040, 0.0040), (mx - 0.009, my, 0.005), material="header_black", collide=False),
    ]
    _tripods(world, cfg)
    _wire(world, "usb_cable", "wire_black", [(ax - 0.034, ay + 0.012, 0.012), (ax - 0.09, ay + 0.02, 0.010), (ax - 0.16, ay + 0.06, 0.004), (ax - 0.30, ay + 0.08, -0.05)], r=0.0022)
    _wire(world, "power_cable", "wire_black", [(ax - 0.034, ay - 0.014, 0.012), (ax - 0.08, ay - 0.03, 0.006), (ax - 0.16, ay - 0.05, 0.002), (ax - 0.30, ay - 0.06, -0.06)], r=0.0018)
    for i, (mat, dy) in enumerate([("wire_brown", 0.0), ("wire_red", 0.0025), ("wire_orange", 0.005)]):
        _wire(world, f"base_servo_{mat}", mat, [(ax + 0.010 + dy, ay + 0.026, 0.016), (ax + 0.02 + dy, ay + 0.06, 0.03 - i * 0.002), (0.0 + dy, 0.09, 0.05), (-0.02 + dy, 0.02, 0.045), (-0.021 + dy, 0.0, 0.030)])
        _wire(world, f"bb_pwr_{mat}", mat, [(bbx - 0.05 + i * 0.01, bby - 0.02, 0.010), (bbx - 0.04 + i * 0.01, bby, 0.02), (bbx - 0.03 + i * 0.01, bby + 0.02, 0.010)])
    _wire(world, "mosfet_sig", "wire_yellow", [(ax + 0.002, ay + 0.026, 0.016), (ax + 0.03, ay + 0.09, 0.03), (mx - 0.012, my - 0.004, 0.012)])
    _wire(world, "mosfet_gnd", "wire_black", [(ax + 0.030, ay - 0.026, 0.016), (ax + 0.06, ay + 0.02, 0.02), (mx - 0.012, my + 0.004, 0.012)])
    _wire(world, "magnet_lead_red", "wire_red", [(mx + 0.014, my + 0.006, 0.012), (0.03, 0.24, 0.03), (0.01, 0.10, 0.09), (0.0, 0.03, 0.10)])
    _wire(world, "magnet_lead_black", "wire_black", [(mx + 0.014, my - 0.006, 0.012), (0.035, 0.235, 0.028), (0.014, 0.10, 0.088), (0.004, 0.03, 0.098)])
    bodies.append(world)

    base = Body("arm_base", parent="world")
    base.geoms += [
        Geom("box", (0.045, 0.045, 0.004), (0, 0, 0.004), material="pla_orange", name="base_plate"),
        Geom("box", (0.026, 0.016, 0.022), (0, 0, 0.026), material="pla_orange", collide=False),
    ]
    base.geoms += servo_geoms("base", "MG996R", (0.0, 0.0, 0.0265))
    bodies.append(base)

    j = cfg.joints
    turret = Body("turret", pos=(0, 0, cfg.TURRET_Z), parent="world", joint=dict(name="base", type="hinge", axis=(0, 0, 1), range=j["base"]["range"], damping=0.08, armature=0.002))
    turret.geoms += [
        Geom("cylinder", (0.022, 0.003), (0, 0, 0.003), material="pla_orange", collide=False, name="turret_disc"),
        Geom("box", (0.004, 0.024, 0.014), (0.0, 0.0, 0.013), material="pla_orange", collide=False),
    ]
    turret.geoms += servo_geoms("shoulder", "MG996R", (0.0, 0.020, cfg.SHOULDER_Z - cfg.TURRET_Z), quat=quat_x(90))
    bodies.append(turret)

    L1, L2 = cfg.L1, cfg.L2
    upper = Body("upper_arm", pos=(0, 0, cfg.SHOULDER_Z - cfg.TURRET_Z), parent="turret", joint=dict(name="shoulder", type="hinge", axis=(0, -1, 0), range=j["shoulder"]["range"], damping=0.05, armature=0.001))
    upper.geoms += [
        Geom("box", (L1 / 2 + 0.008, 0.004, 0.011), (L1 / 2, -0.016, 0.0), material="pla_orange", name="upper_arm_l"),
        Geom("box", (L1 / 2 + 0.008, 0.004, 0.011), (L1 / 2, 0.016, 0.0), material="pla_orange", name="upper_arm_r"),
        Geom("cylinder", (0.010, 0.0155), (0.0, 0.0, 0.0), quat_x(90), material="pla_orange", collide=False),
        Geom("cylinder", (0.0035, 0.024), (L1 * 0.35, 0.0, 0.0), quat_x(90), material="tin", collide=False),
    ]
    upper.geoms += servo_geoms("elbow", "MG996R", (L1 - 0.010, 0.0, 0.0), quat=quat_x(90))
    upper.mass = 0.030 + 0.055
    upper.geoms.append(Geom("wire", (0.0011,), material="wire_red", collide=False, points=((0.0, 0.026, -0.01), (L1 * 0.4, 0.024, -0.014), (L1 - 0.02, 0.020, -0.01))))
    upper.geoms.append(Geom("wire", (0.0011,), material="wire_brown", collide=False, points=((0.0, 0.028, -0.008), (L1 * 0.4, 0.026, -0.012), (L1 - 0.02, 0.022, -0.008))))
    bodies.append(upper)

    fore = Body("forearm", pos=(L1, 0, 0), parent="upper_arm", joint=dict(name="elbow", type="hinge", axis=(0, -1, 0), range=j["elbow"]["range"], damping=0.04, armature=0.001))
    fore.geoms += [
        Geom("box", (L2 / 2 + 0.006, 0.0045, 0.009), (L2 / 2, 0.0, 0.0), material="pla_orange", name="forearm_bar"),
        Geom("cylinder", (0.009, 0.012), (0.0, 0.0, 0.0), quat_x(90), material="pla_orange", collide=False),
    ]
    fore.mass = 0.025
    fore.geoms.append(Geom("wire", (0.0011,), material="wire_red", collide=False, points=((0.0, 0.007, -0.008), (L2 * 0.5, 0.008, -0.012), (L2, 0.006, -0.010))))
    fore.geoms.append(Geom("wire", (0.0011,), material="wire_black", collide=False, points=((0.0, 0.009, -0.006), (L2 * 0.5, 0.010, -0.010), (L2, 0.008, -0.008))))
    bodies.append(fore)
    bodies.append(_magnet_wrist(cfg, (L2, 0, 0)))
    bodies += PIECE_SETS[cfg.pieces]()
    return bodies


def build_taras(cfg: BuildConfig) -> list[Body]:
    """The Madrid shopping-list build: wood, MG90S/MG946R, relay, 24 V magnet, cups (+ optional conveyor)."""
    bodies: list[Body] = []
    world = _common_static(cfg)
    if cfg.conveyor is None and cfg.name in ("theker_v1", "taras_kitting", "taras_grading"):
        # matte blue card covering the working half of the board (parts lie on it), taped down
        world.geoms.append(Geom("box", (0.060, 0.082, 0.0004), (0.115, 0.0, 0.0004), material="card_blue", name="pad"))
    elif cfg.conveyor is None:
        world.geoms.append(Geom("box", (0.036, 0.055, 0.0015), (0.083, 0.0, 0.0015), material="foam_white", name="pad"))  # white matte inspection pad
    else:
        c = cfg.conveyor
        bx, bw, bl, rr = c["x"], c["width"], c["length"], c["roller_r"]
        top = cfg.surface_z
        world.geoms += [
            Geom("box", (0.006, bl / 2 + 0.02, top / 2 + 0.004), (bx - bw / 2 - 0.007, 0.0, top / 2 - 0.004), material="wood", name="rail_l"),
            Geom("box", (0.006, bl / 2 + 0.02, top / 2 + 0.004), (bx + bw / 2 + 0.007, 0.0, top / 2 - 0.004), material="wood", name="rail_r"),
            Geom("box", (bw / 2, bl / 2, 0.0015), (bx, 0.0, top - 2 * rr - 0.0015), material="belt_rubber", collide=False, name="belt_bottom"),
            Geom("box", (bw / 2, 0.0012, 0.0002), (bx, c["pick_zone_y"][0], top + 0.0001), material="foam_white", collide=False),
            Geom("box", (bw / 2, 0.0012, 0.0002), (bx, c["pick_zone_y"][1], top + 0.0001), material="foam_white", collide=False),
            Geom("box", (bw / 2 + 0.012, 0.045, 0.0015), (bx, c["end_tray_y"], 0.0015), material="bin_blue", name="end_tray_floor"),
            Geom("box", (bw / 2 + 0.012, 0.0015, 0.010), (bx, c["end_tray_y"] + 0.045, 0.010), material="bin_blue"),
            Geom("box", (0.0015, 0.045, 0.006), (bx - bw / 2 - 0.012, c["end_tray_y"], 0.006), material="bin_blue"),
            Geom("box", (0.0015, 0.045, 0.006), (bx + bw / 2 + 0.012, c["end_tray_y"], 0.006), material="bin_blue"),
        ]
        world.geoms += servo_geoms("belt", c["servo"], (bx + bw / 2 + 0.024, bl / 2, top - rr), quat=quat_y(90))

    # electronics at the back-left: Uno R3, 5 V relay module, 5 V 5 A brick, 24 V magnet brick, terminal block
    ax, ay = -0.075, 0.075
    _arduino(world, ax, ay)
    rx, ry = -0.075, 0.140
    world.geoms += [
        Geom("box", (0.0125, 0.0085, 0.0008), (rx, ry, 0.0008), material="relay_blue", name="relay_module"),
        Geom("box", (0.0095, 0.0075, 0.0075), (rx + 0.001, ry, 0.0085), material="relay_blue", collide=False),
        Geom("box", (0.0020, 0.0060, 0.0040), (rx - 0.0105, ry, 0.0045), material="header_black", collide=False),
        Geom("box", (0.0060, 0.0018, 0.0040), (rx + 0.004, ry - 0.008, 0.0045), material="tin", collide=False),
        Geom("box", (0.030, 0.020, 0.012), (-0.125, -0.030, 0.012), material="psu_black", name="psu_5v"),
        Geom("box", (0.027, 0.017, 0.011), (-0.125, -0.085, 0.011), material="psu_black", name="psu_24v"),
        Geom("box", (0.012, 0.005, 0.005), (-0.030, 0.160, 0.005), material="wire_grey", collide=False, name="terminal_block"),
    ]
    if cfg.name in ("theker_v1", "taras_kitting", "taras_grading"):
        # sensor shield V5 stacked on the Uno, 5 V 8 A brick, terminal strip, inline fuse + switch, Wago 221 blocks, capacitor
        world.geoms += [
            Geom("box", (0.0343, 0.0267, 0.0015), (ax, ay, 0.0165), material="shield_blue", collide=False, name="sensor_shield"),
            Geom("box", (0.0300, 0.0020, 0.0050), (ax + 0.002, ay + 0.022, 0.023), material="header_black", collide=False),
            Geom("box", (0.0300, 0.0020, 0.0050), (ax + 0.002, ay + 0.017, 0.023), material="header_black", collide=False),
            Geom("box", (0.0080, 0.0040, 0.0050), (ax - 0.026, ay - 0.018, 0.023), material="wire_grey", collide=False),  # shield power terminal
            Geom("box", (0.040, 0.026, 0.016), (-0.140, -0.040, 0.016), material="psu_black", name="psu_5v8a"),
            Geom("box", (0.030, 0.006, 0.006), (-0.060, 0.170, 0.006), material="wire_grey", collide=False, name="terminal_strip"),
            Geom("cylinder", (0.006, 0.020), (-0.100, 0.150, 0.006), quat_y(90), material="pla_black", collide=False, name="fuse_holder"),
            Geom("box", (0.006, 0.006, 0.005), (-0.070, 0.135, 0.005), material="wire_grey", collide=False, name="switch"),
            Geom("cylinder", (0.006, 0.010), (-0.020, 0.170, 0.010), material="cap_black", collide=False, name="capacitor"),
        ]
        for k in range(4):
            world.geoms.append(Geom("box", (0.006, 0.004, 0.005), (0.000 + 0.014 * k, 0.185, 0.005), material="wago_orange", collide=False, name=f"wago_{k}"))
        # two G-clamps holding the board to the table (far edge, away from the arm swing)
        for yy in (-0.09, 0.09):
            world.geoms += [
                Geom("box", (0.006, 0.006, 0.030), (0.255, yy, 0.010), material="clamp_red", collide=False),
                Geom("box", (0.018, 0.006, 0.004), (0.243, yy, 0.036), material="clamp_red", collide=False),
                Geom("box", (0.018, 0.006, 0.004), (0.243, yy, -0.022), material="clamp_red", collide=False),
            ]
        # gooseneck phone clamp on the far table edge, arching over to the camera position
        px, py, pz = cfg.cameras["A"]["pos"]
        world.geoms.append(Geom("box", (0.012, 0.012, 0.020), (0.29, 0.0, 0.010), material="pla_black", collide=False, name="gooseneck_clamp"))
        world.geoms.append(Geom("wire", (0.006,), material="pla_black", collide=False, points=((0.29, 0.0, 0.03), (0.29, 0.0, 0.15), (0.22, 0.0, 0.29), (px + 0.01, py, pz + 0.02)), name="gooseneck"))
        world.geoms.append(Geom("box", (0.0355, 0.0735, 0.004), (px, py, pz + 0.006), material="phone_black", collide=False, name="phone_A"))
        world.geoms.append(Geom("cylinder", (0.006, 0.0005), (px + 0.020, py + 0.045, pz + 0.0015), material="phone_glass", collide=False))
        # the optional second phone stands on a tripod
        cams_bak = cfg.cameras
        cfg.cameras = {k: v for k, v in cams_bak.items() if k != "A"}
        _tripods(world, cfg)
        cfg.cameras = cams_bak
    else:
        _tripods(world, cfg)
    _wire(world, "usb_cable", "wire_black", [(ax - 0.034, ay + 0.012, 0.012), (ax - 0.08, ay + 0.03, 0.008), (ax - 0.17, ay + 0.06, 0.0), (ax - 0.30, ay + 0.08, -0.06)], r=0.0022)
    _wire(world, "psu5_lead", "wire_black", [(-0.125, -0.010, 0.012), (-0.10, 0.03, 0.01), (-0.065, 0.05, 0.008)], r=0.0016)
    _wire(world, "psu5_mains", "wire_black", [(-0.155, -0.030, 0.012), (-0.22, -0.02, 0.0), (-0.32, 0.0, -0.06)], r=0.0022)
    _wire(world, "psu24_lead_red", "wire_red", [(-0.098, -0.085, 0.011), (-0.09, -0.02, 0.010), (rx + 0.004, ry - 0.011, 0.006)], r=0.0013)
    _wire(world, "psu24_lead_black", "wire_black", [(-0.098, -0.089, 0.011), (-0.085, -0.02, 0.008), (0.0, 0.03, 0.06), (0.0, 0.01, 0.10)], r=0.0013)
    _wire(world, "psu24_mains", "wire_black", [(-0.152, -0.085, 0.011), (-0.22, -0.09, 0.0), (-0.32, -0.10, -0.06)], r=0.0022)
    _wire(world, "relay_sig", "wire_yellow", [(ax + 0.002, ay + 0.026, 0.016), (ax - 0.02, ay + 0.05, 0.02), (rx - 0.012, ry + 0.002, 0.008)])
    _wire(world, "relay_vcc", "wire_red", [(ax + 0.030, ay - 0.026, 0.016), (ax + 0.04, ay + 0.02, 0.02), (rx - 0.012, ry - 0.002, 0.008)])
    _wire(world, "relay_gnd", "wire_black", [(ax + 0.032, ay - 0.026, 0.016), (ax + 0.045, ay + 0.03, 0.018), (rx - 0.012, ry + 0.005, 0.008)])
    _wire(world, "magnet_lead_red", "wire_red", [(rx + 0.008, ry - 0.011, 0.006), (-0.03, 0.09, 0.03), (0.0, 0.035, 0.07), (0.0, 0.012, 0.105)])
    for i, (mat, dy) in enumerate([("wire_brown", 0.0), ("wire_red", 0.002), ("wire_orange", 0.004)]):
        _wire(world, f"servo_lead_{mat}", mat, [(ax + 0.010 + dy, ay + 0.026, 0.016), (ax + 0.02 + dy, ay + 0.045, 0.03), (-0.025 + dy, 0.02, 0.03), (-0.024 + dy, 0.0, 0.020)], r=0.0009)
    bodies.append(world)

    # ---- arm: rotating wooden disc on a pivot pin, driven by the MG90S standing beside it
    base = Body("arm_base", parent="world")
    base.geoms += [
        Geom("box", (0.035, 0.035, 0.008), (0, 0, 0.008), material="wood", name="base_block"),
        Geom("cylinder", (0.004, 0.006), (0, 0, 0.020), material="tin", collide=False, name="pivot_pin"),
    ]
    base.geoms += servo_geoms("base", "MG90S", (-0.028, 0.0, 0.016 + 0.0143))
    bodies.append(base)

    j = cfg.joints
    turret = Body("turret", pos=(0, 0, cfg.TURRET_Z), parent="world", joint=dict(name="base", type="hinge", axis=(0, 0, 1), range=j["base"]["range"], damping=0.02, armature=0.0005))
    post_h = cfg.SHOULDER_Z - cfg.TURRET_Z
    disc_r = 0.035 if cfg.name in ("theker_v1", "taras_kitting", "taras_grading") else 0.030
    turret.geoms += [
        Geom("cylinder", (disc_r, 0.004), (0, 0, 0.004), material="wood", collide=False, name="turret_disc"),
        Geom("box", (0.010, 0.010, post_h / 2), (0.0, 0.030, post_h / 2 + 0.004), material="wood", collide=False, name="post"),
    ]
    if cfg.name in ("theker_v1", "taras_kitting", "taras_grading"):
        turret.geoms.append(Geom("cylinder", (disc_r + 0.002, 0.003), (0, 0, -0.003), material="tin", collide=False, name="lazy_susan"))
    # MG946R bolted to the post, output horn facing -y so the horn face is on the base-axis plane
    turret.geoms += servo_geoms("shoulder", "MG946R", (0.0, 0.0105 + SERVO_MODELS["MG946R"]["h"] / 2 - 0.0215 + 0.011, post_h), quat=quat_x(90))
    turret.mass = 0.09
    bodies.append(turret)

    L1, L2 = cfg.L1, cfg.L2
    upper = Body("upper_arm", pos=(0, 0, post_h), parent="turret", joint=dict(name="shoulder", type="hinge", axis=(0, -1, 0), range=j["shoulder"]["range"], damping=0.02, armature=0.0005))
    upper.geoms += [
        Geom("box", (L1 / 2 + 0.006, 0.0035, 0.008), (L1 / 2, 0.005, 0.0), material="wood", name="upper_link"),  # 7 cm × 16 mm × 7 mm pine strip screwed to the horn
        Geom("cylinder", (0.0025, 0.005), (0.0, 0.005, 0.0), quat_x(90), material="tin", collide=False),
    ]
    # MG90S at the elbow, body on the -y side of the upper link, horn facing +y
    upper.geoms += servo_geoms("elbow", "MG90S", (L1, -0.0015 - SERVO_MODELS["MG90S"]["h"] / 2, 0.0), quat=quat_x(-90))
    upper.mass = 0.012 + 0.0137
    upper.geoms.append(Geom("wire", (0.0009,), material="wire_orange", collide=False, points=((0.0, 0.010, -0.006), (L1 * 0.5, 0.011, -0.010), (L1 - 0.01, -0.006, -0.012))))
    upper.geoms.append(Geom("wire", (0.0009,), material="wire_red", collide=False, points=((0.0, 0.012, -0.008), (L1 * 0.5, 0.013, -0.012), (L1 - 0.01, -0.004, -0.014))))
    bodies.append(upper)

    fore = Body("forearm", pos=(L1, 0.0, 0), parent="upper_arm", joint=dict(name="elbow", type="hinge", axis=(0, -1, 0), range=j["elbow"]["range"], damping=0.015, armature=0.0003))
    fore.geoms += [
        Geom("box", (L2 / 2 + 0.005, 0.0035, 0.007), (L2 / 2, cfg.wrist_y, 0.0), material="wood", name="forearm_link"),
        Geom("cylinder", (0.0025, 0.004), (0.0, cfg.wrist_y, 0.0), quat_x(90), material="tin", collide=False),
    ]
    fore.mass = 0.010
    fore.geoms.append(Geom("wire", (0.0009,), material="wire_red", collide=False, points=((0.0, cfg.wrist_y + 0.004, -0.004), (L2 * 0.5, cfg.wrist_y + 0.005, -0.008), (L2, cfg.wrist_y + 0.004, -0.006))))
    fore.geoms.append(Geom("wire", (0.0009,), material="wire_black", collide=False, points=((0.0, cfg.wrist_y + 0.006, -0.002), (L2 * 0.5, cfg.wrist_y + 0.007, -0.006), (L2, cfg.wrist_y + 0.006, -0.004))))
    bodies.append(fore)
    bodies.append(_magnet_wrist(cfg, (L2, cfg.wrist_y, 0), wire_offsets=(0.006, 0.008)))

    if cfg.conveyor is not None:
        c = cfg.conveyor
        bx, bw, bl, rr = c["x"], c["width"], c["length"], c["roller_r"]
        # the belt's top run is a treadmill of 4 segments on slide joints; the firmware drives them at
        # belt speed and each segment that passes the end roller leaps back to the start, so a part
        # carried past the end loses its support there and drops into the tray under the roller.
        # Segments collide only with parts (contype 2), so the frame and trays may overlap them.
        seg_len = (bl + 0.02) / 4
        for k in range(4):
            cy = -bl / 2 - 0.01 + seg_len * (k + 0.5)
            seg = Body(f"belt_seg_{k}", pos=(bx, cy, cfg.surface_z - 0.0015), parent="world", joint=dict(name=f"belt_seg_{k}", type="slide", axis=(0, 1, 0), range=(-1.0, 1.0), damping=0.0, armature=0.0))
            seg.geoms.append(Geom("box", (bw / 2, seg_len / 2 + 0.0005, 0.0015), (0, 0, 0), material="belt_rubber", name=f"belt_top_{k}", contype=2, conaffinity=2))
            seg.mass = 2.0
            bodies.append(seg)
        for i, yy in enumerate((-bl / 2, bl / 2)):
            roller = Body(f"roller_{i}", pos=(bx, yy, cfg.surface_z - rr), parent="world", joint=dict(name=f"roller_{i}", type="hinge", axis=(1, 0, 0), range=(-1e6, 1e6), damping=0.0005, armature=1e-5))
            roller.geoms.append(Geom("cylinder", (rr, bw / 2 + 0.002), (0, 0, 0), quat_y(90), material="belt_rubber", collide=False, name=f"roller_{i}_cyl"))
            roller.geoms.append(Geom("cylinder", (0.003, bw / 2 + 0.014), (0, 0, 0), quat_y(90), material="tin", collide=False))
            roller.mass = 0.02
            bodies.append(roller)

    bodies += PIECE_SETS[cfg.pieces]()
    return bodies


def build() -> list[Body]:
    bodies = build_hobby_v1(CFG) if CFG.name == "hobby_v1" else build_taras(CFG)
    surface_mat = "belt_rubber" if CFG.conveyor else ("foam_white" if CFG.name != "hobby_v1" else "mdf")
    for b in bodies:
        for g in b.geoms:
            if g.material == "__surface__":
                g.material = surface_mat
    return bodies


def piece_bodies(bodies: list[Body]) -> list[Body]:
    return [b for b in bodies if b.piece is not None]


def piece_half_height(body: Body) -> float:
    hs = []
    for g in body.geoms:
        if g.kind in ("cylinder", "hexprism"):
            hs.append(g.size[1] if g.quat == (1.0, 0.0, 0.0, 0.0) else g.size[0])
        elif g.kind == "box":
            hs.append(g.size[2])
    return max(hs) if hs else 0.005


def to_json(bodies: list[Body]) -> str:
    return json.dumps({"bodies": [asdict(b) for b in bodies], "materials": {k: asdict(v) for k, v in MATERIALS.items()}}, indent=1)


def wire_points(points, n: int = 24) -> np.ndarray:
    p = np.asarray(points, dtype=float)
    if len(p) == 2:
        return np.linspace(p[0], p[1], n)
    pts = np.vstack([p[0], p, p[-1]])
    out = []
    per = max(2, n // (len(p) - 1))
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        for t in np.linspace(0, 1, per, endpoint=False):
            t2, t3 = t * t, t * t * t
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
    out.append(p[-1])
    return np.asarray(out)


configure(os.environ.get("SORTER_BUILD", "hobby_v1"))

if __name__ == "__main__":
    for name in BUILDS:
        configure(name)
        bs = build()
        print(f"{name:16s} {len(bs):2d} bodies, {sum(len(b.geoms) for b in bs):3d} geoms, {len(piece_bodies(bs)):2d} pieces, targets={list(TARGETS)}")
