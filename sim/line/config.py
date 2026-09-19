"""Line configuration: one JSON file, one section per subsystem, each owned by that subsystem's agent.

Load with `cfg = load()`; the panel edits values live and calls `save(cfg)`. Implementations receive the
whole `LineConfig` and read their own section, so adding a key never breaks another module.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"


@dataclass
class ArduinoCfg:  # owner: arduino agent
    backend: str = "http"  # 'http' (proxy to conveyor_button.py) | 'serial' | 'fake'
    http_url: str = "http://127.0.0.1:8765"
    port_glob: str = "/dev/cu.usbmodem*"
    baud: int = 115200
    boot_wait_s: float = 2.5
    conveyor_default_speed: int = 60


@dataclass
class GateCfg:  # owner: arduino agent (angles measured on the bench by the build agent)
    channel: str = "base"  # which servo of `S b s e` drives the door: base = D9
    flush_deg: int = 90
    open_deg: int = 65  # 115 if the horn is mounted mirrored
    hold_deg: tuple = (90, 75)  # shoulder/elbow values sent alongside (nothing connected on D10/D11)
    settle_ms: int = 400  # 25° swing with the default firmware ramp (700 °/s²): ≈0.35 s. ≈120 with ramp_override.
    default_dwell_s: float = 0.5
    ramp_override: bool = False  # send `R <ch> <vmax> <accel>` for the door channel at start (firmware ≥ 19 Sep 13:50)
    door_vmax_deg_s: int = 400  # build agent's cap: door tip ≈ 40 cm/s into the foam soft stop
    door_accel_deg_s2: int = 8000  # 25° in ≈0.11 s (10° accel, 5° cruise, 10° decel)


@dataclass
class CameraCfg:  # owner: camera agent
    backend: str = "real"  # 'real' | 'file' | 'synthetic'
    match: str = "iphone"
    width: int = 1280
    height: int = 720
    fps: int = 60
    file_paths: list = field(default_factory=list)
    zone: list = field(default_factory=lambda: [400, 200, 880, 520])  # ROI x0,y0,x1,y1 px over the 9–15 cm stretch
    px_per_mm: float = 6.0  # measured from the pencil ticks 6 cm apart on the wall tops
    flow_axis: str = "x"  # image axis along which beans travel, '+x' | '-x' | '+y' | '-y'


@dataclass
class VisionCfg:  # owner: camera agent
    paper_gray_min: int = 150  # paper is brighter than this
    bean_gray_max: int = 120  # bean pixels are darker than this
    min_area_px: int = 400
    max_area_px: int = 40000
    dark_spot_gray: int = 45
    max_fg_frac: float = 0.5  # more of the ROI than this is dark -> no paper in view, detector returns nothing


@dataclass
class ClassifierCfg:  # owner: coffee-sim agent
    backend: str = "rule"  # 'rule' | 'sklearn'
    model_path: str = "models/beans_v1.joblib"
    suspect_threshold: float = 0.5
    rules: dict = field(default_factory=lambda: {
        "min_major_mm": 8.0, "max_major_mm": 16.0,  # roasted arabica ~10–13 mm long
        "min_aspect": 1.1, "max_aspect": 2.2,
        "max_dark_frac": 0.35,  # black / burnt beans
        "min_mean_gray": 40, "max_mean_gray": 150,  # too dark = burnt, too light = quaker/shell
        "max_n_dark_spots": 2,
    })


@dataclass
class TimingCfg:  # owner: coffee-sim agent (kinematics) with as-built numbers from the build agent
    slope_deg: float = 15.0
    zone_from_top_cm: tuple = (9.0, 15.0)
    door_from_top_cm: tuple = (30.0, 36.0)
    bean_speed_cm_s: float = 35.0  # measured on the bench; the panel's stopwatch tool updates this ('measured' model)
    zone_to_door_s: float = 0.55  # derived by timing.refresh(): zone centre -> door start with the active speed model
    speed_model: str = "drag"  # 'drag' (build agent: v' = a - k v) | 'measured' (constant stopwatch speed) | 'rolling'
    drag_accel_cm_s2_at_15deg: float = 64.0  # net drive at 15°; a(θ) = (a15 + f)·sinθ/sin15° − f (48/64/80/96 at 12/15/18/21°)
    drag_friction_cm_s2: float = 19.2  # f: constant friction term of the drive (bean stalls below ~3.4°)
    drag_k_per_s: float = 1.6  # linear drag -> terminal speed a/k = 40 cm/s at 15°
    door_lead_s: float = 0.0  # extra margin on top of the door swing (gate.settle_ms): photo age + jitter. Total lead = settle + this


@dataclass
class LineConfig:
    arduino: ArduinoCfg = field(default_factory=ArduinoCfg)
    gate: GateCfg = field(default_factory=GateCfg)
    camera: CameraCfg = field(default_factory=CameraCfg)
    vision: VisionCfg = field(default_factory=VisionCfg)
    classifier: ClassifierCfg = field(default_factory=ClassifierCfg)
    timing: TimingCfg = field(default_factory=TimingCfg)
    panel_port: int = 8800
    dry_run: bool = True  # the closed loop logs gate pulses instead of sending them until switched off in the panel


def _merge(dc, d: dict):
    for k, v in d.items():
        if hasattr(dc, k):
            cur = getattr(dc, k)
            if hasattr(cur, "__dataclass_fields__") and isinstance(v, dict):
                _merge(cur, v)
            else:
                setattr(dc, k, tuple(v) if isinstance(cur, tuple) else v)
    return dc


def load(path: Path = CONFIG_PATH) -> LineConfig:
    cfg = LineConfig()
    if path.exists():
        _merge(cfg, json.loads(path.read_text()))
    return cfg


def save(cfg: LineConfig, path: Path = CONFIG_PATH) -> None:
    path.write_text(json.dumps(asdict(cfg), indent=2) + "\n")


if __name__ == "__main__":
    c = load()
    save(c)
    print(json.dumps(asdict(c), indent=2))
