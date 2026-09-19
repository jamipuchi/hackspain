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
    # channel "belt_spin": continuous-rotation servo on D6 as the door, MCU-timed `T <speed> <ms>` pulses into end stops
    spin_speed: int = 12  # % of full speed (bench 16:31: speed 12, 300 ms into the stops)
    spin_open_ms: int = 300
    spin_close_ms: int = 300
    spin_open_dir: int = 1  # +1: positive speed opens; -1 if the horn is mirrored


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
    trigger_frac: float = 0.0  # a bean is judged once it has travelled this fraction of the zone (0 = as soon as fully visible)


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
    algo: str = "extratrees"  # learner for train_from_*: 'extratrees' (2 ms/bean) | 'mlp' (0.2 ms) | 'hgb' (accurate, ~100 ms/bean: too slow live)
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
    lead_margin_s: float = 0.0  # extra margin on top of the door swing (gate.settle_ms): photo age + jitter
    door_lead_s: float = 0.40  # DERIVED (timing.refresh): gate.settle_ms/1000 + lead_margin_s. 0.40 stock ramp, 0.12 with ramp_override
    mode: str = "fixed"  # 'fixed': open fixed_delay_s after the verdict; 'model': chute kinematics (timing.py)
    fixed_delay_s: float = 0.0  # 'fixed' mode: seconds from verdict to door open


@dataclass
class LineConfig:
    arduino: ArduinoCfg = field(default_factory=ArduinoCfg)
    gate: GateCfg = field(default_factory=GateCfg)
    camera: CameraCfg = field(default_factory=CameraCfg)
    vision: VisionCfg = field(default_factory=VisionCfg)
    classifier: ClassifierCfg = field(default_factory=ClassifierCfg)
    timing: TimingCfg = field(default_factory=TimingCfg)
    panel_port: int = 8800
    door_on_d6: bool = False  # bench workaround: the door servo answers only on D6 → translate `S <door> ..` into `C <speed>` (see panel.DoorOnD6)
    door_d6_mode: str = "continuous"  # 'continuous': FS90R-type servo, door driven by timed spin pulses into mechanical stops; 'positional': angle mapping
    door_d6_speed: int = 60  # spin speed -100..100 used for the pulses
    door_d6_open_ms: int = 180  # how long to spin towards OPEN (into the stop)
    door_d6_close_ms: int = 180  # how long to spin back to CLOSED
    door_d6_trim: int = 0  # dead-centre correction added to every spin command (−20..20): raise if CLOSE travels less than OPEN at equal ms
    door_d6_dir: int = 1  # +1 or -1: flip if OPEN spins the wrong way
    door_policy: str = "state"  # 'state': the door holds its last side and only moves when a bean's verdict differs from it; 'pulse': act, dwell, return
    action_position: str = "closed"  # which saved position the door takes when it acts on a bean: 'closed' | 'open' (rest = the other one)
    act_on: str = "all"  # 'all': the door moves for every bean (bring-up); 'suspect': only for suspect verdicts (sorting)
    lost_after_s: float = 0.4  # no blob for this long while tracking → bean lost, re-arm
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
    cfg.__dict__["_loaded_snapshot"] = asdict(cfg)  # what this copy saw on disk; save() writes only what changed since
    return cfg


def _changed(old: dict, new: dict) -> dict:
    """Keys of `new` whose value differs from `old` (recursing into dict sections)."""
    out = {}
    for k, v in new.items():
        o = old.get(k, object())
        if isinstance(v, dict) and isinstance(o, dict):
            d = _changed(o, v)
            if d:
                out[k] = d
        elif v != o:
            out[k] = v
    return out


def save(cfg: LineConfig, path: Path = CONFIG_PATH) -> None:
    """Write `cfg`, but never clobber another process's edits: re-read the file, apply only the keys this copy
    changed since its own load(), write the merge, and refresh the snapshot. Several agents and the panel share
    config.json; before this, a panel save() wrote its whole stale in-memory copy over everyone (19 Sep 14:38–14:50)."""
    mine = asdict(cfg)
    snapshot = cfg.__dict__.get("_loaded_snapshot")
    if snapshot is None or not path.exists():
        merged = mine
    else:
        on_disk = json.loads(path.read_text())
        edits = _changed(snapshot, mine)
        merged = _deep_update(on_disk, edits)
    path.write_text(json.dumps(merged, indent=2) + "\n")
    _merge(cfg, merged)  # pick up others' edits into this copy too
    cfg.__dict__["_loaded_snapshot"] = asdict(cfg)


def _deep_update(base: dict, edits: dict) -> dict:
    for k, v in edits.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v
    return base


if __name__ == "__main__":
    c = load()
    save(c)
    print(json.dumps(asdict(c), indent=2))
