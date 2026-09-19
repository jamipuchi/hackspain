"""Bounded UR5e/mink infeed pick-cell simulation.

The UR5e is the pinned MuJoCo Menagerie model. Motion is differential IK and
kinematic: there are no actuator dynamics. Detection is from rendered camera
pixels; grasping is an explicitly ideal attachment after a position-tolerance
check, not a contact-physics result.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import platform
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import imageio.v2 as imageio
import matplotlib.pyplot as plt
import mink
import mujoco
import numpy as np

from render import hud

MENAGERIE_REVISION = "8161bba264d7fa7c99ca301e91e7fb44737676ad"
MENAGERIE_LICENSE = "BSD-3-Clause"
JOINTS = (
    "shoulder_pan_joint",
    "shoulder_lift_joint",
    "elbow_joint",
    "wrist_1_joint",
    "wrist_2_joint",
    "wrist_3_joint",
)
JOINT_VELOCITY_LIMIT_RAD_S = {
    name: value
    for name, value in zip(JOINTS, (2.094, 2.094, 3.142, 3.142, 3.142, 3.142), strict=True)
}
DT = 0.02
GRASP_TOLERANCE_M = 0.018
REACH_TOLERANCE_M = 0.012
OVERSIZE_THRESHOLD_M = 0.045
BELT_Z = 0.105
BELT_START_Y = -0.68
BELT_END_Y = 0.58
DETECTION_Y = -0.34
CAMERA_PERIOD_S = 0.08
CAMERA_FOV_M = 0.50
CAMERA_SIZE = (320, 240)
WORKSPACE_X_MIN_M = 0.35
WORKSPACE_X_MAX_M = 0.75
TOOL_ROTATION = mink.SO3.from_matrix(
    np.array([[1, 0, 0], [0, -1, 0], [0, 0, -1]], dtype=float)
)


@dataclass(frozen=True)
class FeedItem:
    item_id: str
    kind: str
    arrival_s: float
    lane_x_m: float
    size_m: float

    @property
    def is_large_debris(self) -> bool:
        return self.kind == "debris" and self.size_m >= OVERSIZE_THRESHOLD_M


@dataclass(frozen=True)
class Scenario:
    name: str
    belt_speed_m_s: float
    duration_s: float
    items: tuple[FeedItem, ...]


@dataclass
class Detection:
    detection_id: str
    time_s: float
    x_m: float
    y_m: float
    measured_size_m: float


def scenarios() -> dict[str, Scenario]:
    return {
        "nominal": Scenario(
            "nominal",
            0.10,
            18.0,
            (
                FeedItem("n-bean", "bean", 0.0, 0.43, 0.018),
                FeedItem("n-large-1", "debris", 0.2, 0.49, 0.058),
                FeedItem("n-small", "debris", 1.0, 0.58, 0.030),
                FeedItem("n-large-2", "debris", 7.0, 0.57, 0.054),
            ),
        ),
        "burst": Scenario(
            "burst",
            0.28,
            11.0,
            (
                FeedItem("a-large-1", "debris", 0.0, 0.48, 0.056),
                FeedItem("a-bean", "bean", 0.1, 0.56, 0.018),
                FeedItem("a-large-2", "debris", 0.45, 0.58, 0.058),
                FeedItem("a-large-3", "debris", 0.9, 0.43, 0.052),
                FeedItem("a-large-4", "debris", 1.35, 0.64, 0.055),
                FeedItem("a-small", "debris", 1.8, 0.52, 0.030),
            ),
        ),
        "workspace": Scenario(
            "workspace",
            0.10,
            5.0,
            (FeedItem("w-outside", "debris", 0.0, 0.84, 0.058),),
        ),
    }


def select_oversize(detections: list[Detection]) -> list[Detection]:
    """The controller's only selection rule: camera size at/above threshold."""
    return [d for d in detections if d.measured_size_m >= OVERSIZE_THRESHOLD_M]


def _model_xml(model_dir: Path, items: tuple[FeedItem, ...]) -> str:
    source = (model_dir / "ur5e.xml").read_text()
    source = source.replace(
        '<compiler angle="radian" meshdir="assets" autolimits="true"/>',
        f'<compiler angle="radian" meshdir="{model_dir / "assets"}" autolimits="true"/>',
    )
    cell = """
    <light pos="0.5 -0.2 1.4" dir="0 0 -1" directional="true" diffuse="0.8 0.8 0.8"/>
    <geom name="floor" type="plane" size="2 2 0.05" rgba="0.18 0.19 0.20 1"/>
    <geom name="belt" type="box" pos="0.55 -0.05 0.075" size="0.38 0.70 0.03" rgba="0.08 0.25 0.38 1" contype="0" conaffinity="0"/>
    <geom name="reject_bin" type="box" pos="0.27 0.54 0.12" size="0.13 0.12 0.04" rgba="0.75 0.16 0.12 1" contype="0" conaffinity="0"/>
    <geom name="reject_back" type="box" pos="0.27 0.65 0.20" size="0.13 0.01 0.12" rgba="0.45 0.08 0.06 1" contype="0" conaffinity="0"/>
    <camera name="detect_camera" pos="0.60 -0.34 1.05" xyaxes="1 0 0 0 1 0" projection="orthographic" fovy="0.50"/>
    <camera name="overview_camera" pos="1.45 -1.55 1.25" xyaxes="0.73 0.68 0 -0.34 0.37 0.87" fovy="48"/>
    <site name="grasp_marker" pos="0 0 -10" size="0.012" rgba="1 1 0 0.7"/>
    """
    for item in items:
        rgba = "0.78 0.18 0.08 1" if item.kind == "debris" else "0.38 0.19 0.07 1"
        if item.kind == "debris":
            geom = f'<geom type="box" size="{item.size_m / 2:.5f} {item.size_m * 0.38:.5f} {item.size_m * 0.28:.5f}" rgba="{rgba}"/>'
        else:
            geom = f'<geom type="ellipsoid" size="{item.size_m / 2:.5f} {item.size_m * 0.32:.5f} {item.size_m * 0.25:.5f}" rgba="{rgba}"/>'
        cell += f'<body name="{item.item_id}" mocap="true" pos="0 0 -10">{geom}</body>\n'
    return source.replace("</worldbody>", cell + "</worldbody>")


def _item_position(item: FeedItem, scenario: Scenario, time_s: float) -> np.ndarray:
    return np.array(
        [item.lane_x_m, BELT_START_Y + scenario.belt_speed_m_s * (time_s - item.arrival_s), BELT_Z + item.size_m * 0.28]
    )


def _camera_detections(frame: np.ndarray, time_s: float) -> list[Detection]:
    # Debris is the red foreground class. Size and centroid come from pixels.
    rgb = frame.astype(np.int16)
    mask = ((rgb[:, :, 0] > 180) & (rgb[:, :, 0] > rgb[:, :, 1] + 40) & (rgb[:, :, 0] > rgb[:, :, 2] + 20)).astype(np.uint8)
    count, _, stats, centers = cv2.connectedComponentsWithStats(mask, 8)
    width, height = CAMERA_SIZE
    metres_per_pixel = CAMERA_FOV_M / height
    detections: list[Detection] = []
    for index in range(1, count):
        x, y, w, h, area = stats[index]
        if area < 12:
            continue
        cx, cy = centers[index]
        detections.append(
            Detection(
                detection_id=f"d-{time_s:.2f}-{index}",
                time_s=time_s,
                x_m=0.60 + (cx - width / 2) * metres_per_pixel,
                y_m=-0.34 - (cy - height / 2) * metres_per_pixel,
                measured_size_m=max(w, h) * metres_per_pixel,
            )
        )
    return detections


def _pose(position: np.ndarray) -> mink.SE3:
    return mink.SE3.from_rotation_and_translation(TOOL_ROTATION, position)


_SOURCE_PATHS = {
    "ur5e_infeed.py": Path(__file__),
    "run_ur5e_infeed.py": Path(__file__).with_name("run_ur5e_infeed.py"),
    "render.py": Path(__file__).with_name("render.py"),
}
_SOURCE_BYTES = {name: path.read_bytes() for name, path in _SOURCE_PATHS.items()}
_SOURCE_SHA256 = {
    name: hashlib.sha256(source).hexdigest() for name, source in _SOURCE_BYTES.items()
}


def _verify_source_identity() -> dict[str, str]:
    for name, path in _SOURCE_PATHS.items():
        if path.read_bytes() != _SOURCE_BYTES[name]:
            raise RuntimeError(f"source changed during run: {name}")
    return dict(_SOURCE_SHA256)


def _model_identity(model_dir: Path) -> dict[str, Any]:
    model_dir = model_dir.resolve()
    if not (model_dir / "ur5e.xml").is_file():
        raise FileNotFoundError(f"missing UR5e model: {model_dir / 'ur5e.xml'}")
    repository = Path(
        subprocess.check_output(
            ["git", "-C", str(model_dir), "rev-parse", "--show-toplevel"], text=True
        ).strip()
    ).resolve()
    if model_dir != repository / "universal_robots_ur5e":
        raise ValueError(f"model must be {repository / 'universal_robots_ur5e'}")
    revision = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != MENAGERIE_REVISION:
        raise ValueError(f"Menagerie revision {revision} != required {MENAGERIE_REVISION}")
    dirty = subprocess.check_output(
        ["git", "-C", str(repository), "status", "--porcelain", "--untracked-files=all"],
        text=True,
    ).strip()
    if dirty:
        raise ValueError("Menagerie checkout is dirty; refusing unbound model files")
    files = {
        path.relative_to(model_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(model_dir.rglob("*"))
        if path.is_file()
    }
    return {
        "repository_revision": revision,
        "model_directory": model_dir.name,
        "files_sha256": files,
        "tree_sha256": hashlib.sha256(
            json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def _require_finite(name: str, value: Any) -> None:
    if not np.all(np.isfinite(value)):
        raise FloatingPointError(f"non-finite {name}")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def _associate_detection(
    detection: Detection,
    candidates: list[FeedItem],
    scenario: Scenario,
    time_s: float,
) -> tuple[FeedItem, float] | None:
    if not candidates:
        return None
    point = np.array([detection.x_m, detection.y_m])
    item = min(
        candidates,
        key=lambda candidate: np.linalg.norm(
            _item_position(candidate, scenario, time_s)[:2] - point
        ),
    )
    error = float(np.linalg.norm(_item_position(item, scenario, time_s)[:2] - point))
    _require_finite("camera association error", error)
    return item, error


def run_scenario(
    scenario: Scenario,
    model_dir: Path,
    output_dir: Path,
    *,
    render: bool = True,
) -> dict[str, Any]:
    source_identity = _verify_source_identity()
    model_identity = _model_identity(model_dir)
    for item in scenario.items:
        _require_finite(
            f"feed item {item.item_id}",
            (item.arrival_s, item.lane_x_m, item.size_m),
        )
    _require_finite(
        f"scenario {scenario.name}", (scenario.belt_speed_m_s, scenario.duration_s)
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    model = mujoco.MjModel.from_xml_string(_model_xml(model_dir, scenario.items))
    data = mujoco.MjData(model)
    configuration = mink.Configuration(model)
    mujoco.mj_resetDataKeyframe(model, data, model.key("home").id)
    configuration.update(data.qpos)
    ee_task = mink.FrameTask("attachment_site", "site", 1.0, 0.20, 1.0)
    posture_task = mink.PostureTask(model, 1e-3)
    posture_task.set_target_from_configuration(configuration)
    limits = [mink.ConfigurationLimit(model), mink.VelocityLimit(model, JOINT_VELOCITY_LIMIT_RAD_S)]

    detect_renderer = mujoco.Renderer(model, CAMERA_SIZE[1], CAMERA_SIZE[0])
    overview_renderer = mujoco.Renderer(model, 360, 640) if render else None
    detections: list[Detection] = []
    events: list[dict[str, Any]] = []
    trajectory: list[dict[str, Any]] = []
    frames: list[np.ndarray] = []
    peak_velocity = np.zeros(model.nv)
    detected_items: set[str] = set()
    outcomes: dict[str, str] = {}
    attached: str | None = None
    target_id: str | None = None
    target_detection: Detection | None = None
    state = "idle"
    state_started = 0.0
    last_camera = -CAMERA_PERIOD_S
    item_by_id = {item.item_id: item for item in scenario.items}
    mocap_id = {item.item_id: model.body(item.item_id).mocapid[0] for item in scenario.items}

    def event(time_s: float, name: str, item_id: str | None = None, **details: Any) -> None:
        events.append({"time_s": round(time_s, 4), "event": name, "item_id": item_id, **details})

    time_s = 0.0
    while time_s <= scenario.duration_s + 1e-9:
        for item in scenario.items:
            if item.item_id == attached:
                data.mocap_pos[mocap_id[item.item_id]] = data.site("attachment_site").xpos
            elif outcomes.get(item.item_id) == "success":
                data.mocap_pos[mocap_id[item.item_id]] = np.array([0.27, 0.54, 0.17])
            elif time_s >= item.arrival_s:
                data.mocap_pos[mocap_id[item.item_id]] = _item_position(item, scenario, time_s)
            else:
                data.mocap_pos[mocap_id[item.item_id]] = np.array([0.0, 0.0, -10.0])
        data.qpos[:] = configuration.q
        mujoco.mj_forward(model, data)

        if time_s - last_camera >= CAMERA_PERIOD_S - 1e-9:
            detect_renderer.update_scene(data, camera="detect_camera")
            frame = detect_renderer.render()
            for detection in _camera_detections(frame, time_s):
                if abs(detection.y_m - DETECTION_Y) > 0.03:
                    continue
                candidates = [
                    item for item in scenario.items if time_s >= item.arrival_s
                ]
                association = _associate_detection(detection, candidates, scenario, time_s)
                if association is None:
                    continue
                # Ground truth is used only to label/evaluate a camera detection and
                # to implement the disclosed ideal attachment, never for selection.
                item, error = association
                if error <= 0.055 and item.item_id not in detected_items:
                    detection.detection_id = item.item_id
                    detected_items.add(item.item_id)
                    selected = bool(select_oversize([detection]))
                    event(time_s, "camera_observed", item.item_id, measured_size_m=round(detection.measured_size_m, 5), association_error_m=round(error, 5), selected=selected)
                    if selected:
                        detections.append(detection)
            last_camera = time_s

        if state == "idle":
            available = [d for d in detections if d.detection_id not in outcomes and d.detection_id != target_id]
            if available:
                target_detection = available[0]
                target_id = target_detection.detection_id
                queue_wait_s = time_s - target_detection.time_s
                _require_finite("queue wait", queue_wait_s)
                if not WORKSPACE_X_MIN_M <= target_detection.x_m <= WORKSPACE_X_MAX_M:
                    outcomes[target_id] = "outside_workspace"
                    event(
                        time_s,
                        "outside_configured_workspace",
                        target_id,
                        detected_x_m=round(target_detection.x_m, 5),
                        workspace_x_m=[WORKSPACE_X_MIN_M, WORKSPACE_X_MAX_M],
                        queue_wait_s=round(queue_wait_s, 4),
                    )
                    target_id = None
                    target_detection = None
                else:
                    state, state_started = "pick", time_s
                    event(time_s, "pick_started", target_id, queue_wait_s=round(queue_wait_s, 4))
        target_position = np.array([0.42, -0.10, 0.45])
        if state == "pick" and target_id is not None and target_detection is not None:
            target_position = np.array([
                target_detection.x_m,
                target_detection.y_m + scenario.belt_speed_m_s * (time_s - target_detection.time_s),
                BELT_Z + item_by_id[target_id].size_m * 0.28 + 0.008,
            ])
            if target_position[1] > BELT_END_Y:
                outcomes[target_id] = "missed"
                event(time_s, "missed_belt_end", target_id)
                state, target_id, target_detection = "idle", None, None
            elif time_s - state_started > 3.2:
                outcomes[target_id] = "timed_out"
                event(time_s, outcomes[target_id], target_id)
                state, target_id, target_detection = "idle", None, None
        elif state == "lift" and attached is not None:
            target_position = data.mocap_pos[mocap_id[attached]].copy()
            target_position[2] = 0.38
        elif state == "reject" and attached is not None:
            target_position = np.array([0.27, 0.54, 0.34])
        elif state == "return":
            target_position = np.array([0.42, -0.10, 0.45])

        ee_task.set_target(_pose(target_position))
        velocity = mink.solve_ik(configuration, [ee_task, posture_task], DT, "daqp", damping=1e-3, limits=limits)
        _require_finite("solver velocity", velocity)
        peak_velocity = np.maximum(peak_velocity, np.abs(velocity))
        configuration.integrate_inplace(velocity, DT)
        _require_finite("robot configuration", configuration.q)
        data.qpos[:] = configuration.q
        mujoco.mj_forward(model, data)
        ee_position = data.site("attachment_site").xpos.copy()
        _require_finite("end-effector position", ee_position)
        _require_finite("target position", target_position)
        position_error = float(np.linalg.norm(ee_position - target_position))
        _require_finite("position error", position_error)

        if state == "pick" and target_id is not None and position_error <= GRASP_TOLERANCE_M:
            actual_position = _item_position(item_by_id[target_id], scenario, time_s)
            grasp_error = float(np.linalg.norm(ee_position - actual_position))
            _require_finite("grasp error", grasp_error)
            if grasp_error <= GRASP_TOLERANCE_M:
                attached = target_id
                state, state_started = "lift", time_s
                event(time_s, "ideal_attachment", attached, grasp_error_m=round(grasp_error, 5))
        elif state == "lift" and position_error <= REACH_TOLERANCE_M:
            state, state_started = "reject", time_s
            event(time_s, "lift_complete", attached)
        elif state == "reject" and attached is not None and position_error <= REACH_TOLERANCE_M:
            released_id = attached
            outcomes[released_id] = "success"
            event(time_s, "ideal_placement_in_reject_bin", released_id)
            attached = None
            target_id = None
            target_detection = None
            state, state_started = "return", time_s
        elif state == "return" and position_error <= REACH_TOLERANCE_M:
            state, state_started = "idle", time_s

        for item in scenario.items:
            if item.is_large_debris and item.item_id not in outcomes and item.item_id != target_id:
                if time_s >= item.arrival_s and _item_position(item, scenario, time_s)[1] > BELT_END_Y:
                    outcomes[item.item_id] = "missed"
                    event(time_s, "missed_while_busy_or_undetected", item.item_id)

        if int(round(time_s / DT)) % 5 == 0:
            row = {"time_s": round(time_s, 4), "state": state, "target_id": target_id or "", "ee_x_m": float(ee_position[0]), "ee_y_m": float(ee_position[1]), "ee_z_m": float(ee_position[2]), "target_error_m": position_error}
            row.update({f"q_{index + 1}_rad": float(value) for index, value in enumerate(configuration.q)})
            trajectory.append(row)
            if overview_renderer is not None:
                overview_renderer.update_scene(data, camera="overview_camera")
                frame = overview_renderer.render().copy()
                hud(frame, [
                    f"UR5e infeed | {scenario.name} | {scenario.belt_speed_m_s:.2f} m/s",
                    f"t={time_s:.1f} s | {state} | target={target_id or '-'}",
                    "KINEMATIC / IDEAL PLACE - FIXTURE COLLISIONS OFF",
                ], org=(12, 20), scale=0.45)
                frames.append(frame)
        time_s += DT

    detect_renderer.close()
    if overview_renderer is not None:
        overview_renderer.close()
    for item in scenario.items:
        if item.is_large_debris and item.item_id not in outcomes:
            outcomes[item.item_id] = "timed_out"
            event(scenario.duration_s, "run_ended", item.item_id)

    # Include selected objects lost before service; otherwise congestion victims
    # disappear from the queue statistic. Run-end waits are right-censored.
    queue_end_events = {
        "pick_started", "outside_configured_workspace",
        "missed_while_busy_or_undetected", "run_ended",
    }
    queue_waits_s: dict[str, float] = {}
    for detection in detections:
        end_time = next((
            entry["time_s"] for entry in events
            if entry["item_id"] == detection.detection_id
            and entry["event"] in queue_end_events
        ), scenario.duration_s)
        queue_waits_s[detection.detection_id] = max(0.0, end_time - detection.time_s)

    large_ids = [item.item_id for item in scenario.items if item.is_large_debris]
    outcome_names = ("success", "missed", "outside_workspace", "timed_out")
    counts = {
        name: sum(outcomes.get(item_id) == name for item_id in large_ids)
        for name in outcome_names
    }
    nonlarge_selected = [d.detection_id for d in detections if not item_by_id[d.detection_id].is_large_debris]
    feed_manifest = [asdict(item) | {"is_large_debris": item.is_large_debris} for item in scenario.items]
    config_identity = {
        "scenario": scenario.name,
        "belt_speed_m_s": scenario.belt_speed_m_s,
        "duration_s": scenario.duration_s,
        "items": feed_manifest,
        "oversize_threshold_m": OVERSIZE_THRESHOLD_M,
        "grasp_tolerance_m": GRASP_TOLERANCE_M,
        "reach_tolerance_m": REACH_TOLERANCE_M,
        "dt_s": DT,
        "camera_period_s": CAMERA_PERIOD_S,
        "configured_workspace_x_m": [WORKSPACE_X_MIN_M, WORKSPACE_X_MAX_M],
    }
    config_sha256 = hashlib.sha256(json.dumps(config_identity, sort_keys=True).encode()).hexdigest()
    joint_position_limits = {
        name: [float(value) for value in model.jnt_range[model.joint(name).id]] for name in JOINTS
    }
    metrics: dict[str, Any] = {
        "scenario": scenario.name,
        "seed": 0,
        "method": "fixed-cadence camera selection + mink kinematics + ideal tolerance-gated attachment and placement",
        "claim_boundary": "Kinematic ideal-grasp simulation with collision-disabled fixtures and ideal bin placement; no contact grasp, actuator dynamics, dynamic bin capture, or hardware validation.",
        "config": config_identity | {"pick_timeout_s": 3.2, "joint_position_limits_rad": joint_position_limits, "joint_velocity_limits_rad_s": JOINT_VELOCITY_LIMIT_RAD_S},
        "config_sha256": config_sha256,
        "denominators": {"feed_items": len(scenario.items), "large_debris": len(large_ids), "nonlarge_items": len(scenario.items) - len(large_ids), "camera_selected": len(detections)},
        "outcomes": counts,
        "large_debris_success_rate": counts["success"] / len(large_ids) if large_ids else None,
        "nonlarge_selected": len(nonlarge_selected),
        "nonlarge_selected_ids": nonlarge_selected,
        "nonlarge_terminal_outcomes": {
            item_id: outcomes.get(item_id, "selected_not_completed")
            for item_id in nonlarge_selected
        },
        "queue_wait_s": {
            "samples": [round(value, 4) for value in queue_waits_s.values()],
            "by_item": {key: round(value, 4) for key, value in queue_waits_s.items()},
            "max": max(queue_waits_s.values(), default=0.0),
        },
        "observed_peak_joint_velocity_rad_s": {name: float(peak_velocity[index]) for index, name in enumerate(JOINTS)},
        "per_item_outcome": {
            item.item_id: outcomes.get(
                item.item_id, "not_target" if not item.is_large_debris else "missing_terminal_outcome"
            )
            for item in scenario.items
        },
        "source": {
            "files_sha256": source_identity,
            "menagerie_revision": MENAGERIE_REVISION,
            "menagerie_license": MENAGERIE_LICENSE,
            "model": model_identity,
            "python": platform.python_version(),
            "mujoco": mujoco.__version__,
            "mink": importlib.metadata.version("mink"),
            "numpy": np.__version__,
            "opencv": cv2.__version__,
            "runtime_os": platform.platform(),
            "mesa_fallback_recipe": {
                "pinned": False,
                "packages": [
                    "libosmesa6", "libllvm17t64", "libdrm2", "libelf1t64",
                    "libzstd1", "libsensors5", "libedit2", "libffi8",
                    "libpciaccess0", "libglapi-mesa",
                ],
            },
        },
    }
    _write_json(output_dir / "metrics.json", metrics)
    _write_json(
        output_dir / "feed_manifest.json",
        {"seed": 0, "config_sha256": config_sha256, "items": feed_manifest},
    )
    _write_json(output_dir / "events.json", events)
    if trajectory:
        with (output_dir / "trajectory.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=trajectory[0].keys(), lineterminator="\n")
            writer.writeheader()
            writer.writerows(trajectory)
    if frames:
        imageio.mimsave(output_dir / "overview.mp4", frames, fps=10, codec="libx264", quality=7)
        indices = np.linspace(0, len(frames) - 1, min(6, len(frames)), dtype=int)
        contact = np.concatenate([frames[index][::2, ::2] for index in indices], axis=1)
        imageio.imwrite(output_dir / "contact_sheet.png", contact)
    _verify_source_identity()
    if _model_identity(model_dir) != model_identity:
        raise RuntimeError("UR5e model files changed during run")
    return metrics


def write_summary(output_dir: Path, results: list[dict[str, Any]]) -> None:
    names = [result["scenario"] for result in results]
    categories = ["success", "missed", "outside_workspace", "timed_out"]
    bottom = np.zeros(len(results))
    fig, axis = plt.subplots(figsize=(7.0, 4.2))
    for category in categories:
        values = np.array([result["outcomes"][category] for result in results])
        axis.bar(names, values, bottom=bottom, label=category)
        bottom += values
    axis.set_ylabel("large-debris objects (preserved denominator)")
    axis.set_title("UR5e infeed ideal-grasp outcomes")
    axis.legend()
    fig.tight_layout()
    fig.savefig(output_dir / "outcomes.png", dpi=160)
    plt.close(fig)
    _write_json(output_dir / "metrics.json", {"scenarios": results})
    rows = "\n".join(
        f"| {result['scenario']} | {result['config']['belt_speed_m_s']:.2f} | {result['denominators']['large_debris']} | {result['outcomes']['success']} | {result['outcomes']['missed']} | {result['outcomes']['outside_workspace']} | {result['outcomes']['timed_out']} | {result['queue_wait_s']['max']:.2f} | {result['nonlarge_selected']} |"
        for result in results
    )
    report = f"""# UR5e infeed pick-cell evidence

## Result

This is a **kinematic ideal-grasp result**, not physical grasp or end-to-end
hardware validation. Camera pixels select red foreign matter by measured size;
the real Menagerie UR5e follows mink/DAQP trajectories while the belt remains
moving. A success requires camera selection, tolerance-gated ideal attachment,
lift, and ideal placement at the reject-bin pose. Tool proximity alone is never
counted.

| case | belt m/s | large-debris denominator | success | missed | outside configured workspace | timed out | max queue wait s | non-large selected |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
{rows}

The nominal case spaces two reachable oversized pieces. The burst case combines
a 0.28 m/s belt (2.8x nominal) and tight arrivals; its misses and observed queue
waits remain in the evidence, but this run does not isolate or prove overload
causality. The workspace case exercises the configured x exclusion and does not
claim to certify the arm's physical reach. One deterministic run per case is not
a robustness or statistical claim.

## Method and limits

- Oversize means camera-measured maximum footprint **>= {OVERSIZE_THRESHOLD_M * 1000:.0f} mm**.
- Grasp/reach tolerances are {GRASP_TOLERANCE_M * 1000:.0f}/{REACH_TOLERANCE_M * 1000:.0f} mm; pick timeout is 3.2 s; IK timestep is {DT:.2f} s.
- The camera runs at a fixed, class-independent {1 / CAMERA_PERIOD_S:.1f} Hz cadence.
  Pixel detections are proximity-associated against all arrived feed objects;
  simulator identity and known object height are disclosed oracle inputs for
  association/evaluation and ideal attachment. Selection itself calls the
  measured-size production rule and is never gated by true class.
- Objects move continuously at the configured belt speed until ideal attachment;
  the belt is not stopped. The arm has no actuator dynamics, gripper geometry,
  contact closure, payload slip, collision avoidance, or safety-rated controls.
  Belt/bin fixture collisions are disabled, and bin capture is an ideal placement
  rather than a dynamic drop/contact result.
- Queue residence includes every selected object, ending at service, policy
  exclusion, a terminal miss or run end; run-end waits are right-censored.
- Joint position ranges come from the pinned model; configured velocity limits,
  observed peaks, 10 Hz sampled joint/EE trajectory, per-object events, and
  preserved outcome denominators are in the JSON/CSV artifacts.

## Reproduce

```bash
cd sim/coffee_sorter
python3 -m venv .venv-picking
.venv-picking/bin/pip install -r requirements.txt -r requirements-picking.txt
MENAGERIE=$(./setup_ur5e_pick.sh)
# If the host lacks OSMesa, run ./setup_mesa.sh and apply the printed exports.
TEST_LOG=$(mktemp)
set -o pipefail
MUJOCO_GL=osmesa PYOPENGL_PLATFORM=osmesa \\
MUJOCO_MENAGERIE_DIR="$MENAGERIE" REQUIRE_UR5E_MODEL=1 \\
  .venv-picking/bin/python -m unittest test_ur5e_infeed.py -v 2>&1 | tee "$TEST_LOG"
MUJOCO_GL=osmesa PYOPENGL_PLATFORM=osmesa \\
MUJOCO_MENAGERIE_DIR="$MENAGERIE" REQUIRE_UR5E_MODEL=1 \\
  .venv-picking/bin/python -m unittest discover -p 'test_*.py' -v 2>&1 | tee -a "$TEST_LOG"
MUJOCO_GL=osmesa PYOPENGL_PLATFORM=osmesa \\
  .venv-picking/bin/python run_ur5e_infeed.py \\
  --menagerie-dir "$MENAGERIE" --output runs/ur5e-infeed \\
  --tests-log "$TEST_LOG" --rerun
```

Menagerie source: `google-deepmind/mujoco_menagerie` commit
`{MENAGERIE_REVISION}`, UR5e assets under BSD-3-Clause. The robot checkout is
cached outside this repository by `setup_ur5e_pick.sh`; no large external assets
are copied into Git. Exact Python package pins are in `requirements-picking.txt`.
The optional root-free Mesa helper resolves its listed Ubuntu packages at runtime;
that system recipe is unpinned and the runtime OS is recorded in metrics.

## Artifact map

- `metrics.json`, plus per-case `metrics.json`: configuration/source identity and counts.
- `feed_manifest.json`: deterministic inputs and classification denominator.
- `events.json`: camera observation, selection, ideal attachment and terminal events.
- `trajectory.csv`: 10 Hz sampled state, end-effector target error and six joint positions.
- `outcomes.png`, per-case `contact_sheet.png`, and short `overview.mp4` videos.
- [`tests.log`](tests.log): actual focused and full-project unittest results.
- `completion.json`: final SHA-256 inventory of the saved suite artifacts.

Test counts and status are reported only by the linked log; this report does not
embed a generated hard-coded pass count.
"""
    (output_dir / "REPORT.md").write_text(report)
