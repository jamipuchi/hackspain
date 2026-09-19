"""Measure coffee sorter insertion contacts and class-specific motion."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


for thread_variable in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
):
    os.environ[thread_variable] = "1"

import mujoco
import numpy as np
from threadpoolctl import threadpool_info


ROOT = Path(__file__).resolve().parents[4]
SIM_DIR = ROOT / "sim" / "coffee_sorter"
sys.path.insert(0, str(SIM_DIR))

from profiles import PROFILES
from scene import Layout
from sim import JET_HALF_X, SorterSim


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize(values: list[float]) -> dict:
    if not values:
        return {
            "samples": 0,
            "min": None,
            "p50": None,
            "p95": None,
            "p99": None,
            "max": None,
            "mean": None,
        }
    sample = np.asarray(values, dtype=float)
    return {
        "samples": int(len(sample)),
        "min": float(sample.min()),
        "p50": float(np.percentile(sample, 50)),
        "p95": float(np.percentile(sample, 95)),
        "p99": float(np.percentile(sample, 99)),
        "max": float(sample.max()),
        "mean": float(sample.mean()),
    }


def collision_bottom(model, data, geom: int) -> float:
    kind = int(model.geom_type[geom])
    size = model.geom_size[geom]
    rotation = data.geom_xmat[geom].reshape(3, 3)
    if kind == int(mujoco.mjtGeom.mjGEOM_CAPSULE):
        extent = size[0] + size[1] * abs(rotation[2, 2])
    elif kind == int(mujoco.mjtGeom.mjGEOM_BOX):
        extent = float(np.sum(size * np.abs(rotation[2])))
    else:
        raise RuntimeError(f"unsupported collision geom type: {kind}")
    return float(data.geom_xpos[geom, 2] - extent)


def empty_spawn_measurement() -> dict:
    return {
        "uids": set(),
        "clearance": [],
        "pair_distances": [],
        "closest_distances": [],
        "overlap_depths": [],
        "objects_with_overlap": set(),
        "overlap_pairs": 0,
    }


def empty_contact_measurement() -> dict:
    return {
        "uids": set(),
        "normal_impulses": [],
        "penetration_depths": [],
        "bean_contact_samples": 0,
        "fixture_contact_samples": 0,
    }


def empty_motion_measurement() -> dict:
    return {
        "uids": set(),
        "collision_bottom_relative_to_belt": [],
        "vz": [],
    }


def sync_geometry(model, source, destination) -> None:
    destination.time = source.time
    destination.qpos[:] = source.qpos
    destination.qvel[:] = source.qvel
    mujoco.mj_forward(model, destination)


def finish_spawn(values: dict) -> dict:
    return {
        "objects": len(values["uids"]),
        "collision_bottom_clearance_m": summarize(values["clearance"]),
        "evaluated_existing_geom_pairs": len(values["pair_distances"]),
        "signed_existing_geom_distance_m": summarize(values["pair_distances"]),
        "closest_existing_geom_distance_m": summarize(values["closest_distances"]),
        "objects_with_overlap": len(values["objects_with_overlap"]),
        "overlap_pairs": values["overlap_pairs"],
        "overlap_depth_m": summarize(values["overlap_depths"]),
    }


def finish_contacts(values: dict) -> dict:
    return {
        "samples": len(values["normal_impulses"]),
        "objects": len(values["uids"]),
        "normal_impulse_n_s": summarize(values["normal_impulses"]),
        "penetration_depth_m": summarize(values["penetration_depths"]),
        "bean_contact_samples": values["bean_contact_samples"],
        "fixture_contact_samples": values["fixture_contact_samples"],
    }


def finish_motion(values: dict) -> dict:
    return {
        "samples": len(values["vz"]),
        "objects": len(values["uids"]),
        "collision_bottom_relative_to_belt_m": summarize(
            values["collision_bottom_relative_to_belt"]
        ),
        "vertical_velocity_m_s": summarize(values["vz"]),
        "absolute_vertical_velocity_m_s": summarize([abs(v) for v in values["vz"]]),
    }


def measure(preset_path: Path, seconds: float) -> dict:
    preset = json.loads(preset_path.read_text())
    profile_name = preset["profile"]
    if profile_name not in PROFILES:
        raise ValueError(f"unsupported profile: {profile_name}")
    profile = PROFILES[profile_name]
    layout = Layout(**preset["layout"])
    sim = SorterSim(
        profile,
        layout,
        rate=float(preset["requested_rate"]),
        seed=int(preset["seed"]),
    )
    model = sim.model
    geometry_data = mujoco.MjData(model)
    configured_model_path = Path(preset["model_path"])
    model_path = (
        configured_model_path
        if configured_model_path.is_absolute()
        else (SIM_DIR / configured_model_path).resolve()
    )
    class_names = profile.names
    spawn_values = {name: empty_spawn_measurement() for name in class_names}
    contact_values = {
        region: {name: empty_contact_measurement() for name in class_names}
        for region in ("belt_feed", "downstream")
    }
    motion_values = {
        region: {name: empty_motion_measurement() for name in class_names}
        for region in ("post_feed", "inspection", "jet")
    }
    original_spawn = sim.spawn

    def instrumented_spawn(spec=None):
        bean = original_spawn(spec)
        if bean is None:
            return None
        sync_geometry(model, sim.data, geometry_data)
        values = spawn_values[bean.cls]
        values["uids"].add(bean.uid)
        incoming_geom = sim.body_col[bean.body]
        values["clearance"].append(
            collision_bottom(model, geometry_data, incoming_geom) - layout.belt_z
        )
        pair_distances = []
        for other_body in sim.bean_of:
            if other_body == bean.body:
                continue
            distance = float(
                mujoco.mj_geomDistance(
                    model,
                    geometry_data,
                    incoming_geom,
                    sim.body_col[other_body],
                    1.0,
                    None,
                )
            )
            pair_distances.append(distance)
            values["pair_distances"].append(distance)
            if distance < 0.0:
                values["objects_with_overlap"].add(bean.uid)
                values["overlap_pairs"] += 1
                values["overlap_depths"].append(-distance)
        if pair_distances:
            values["closest_distances"].append(min(pair_distances))
        return bean

    sim.spawn = instrumented_spawn
    while sim.data.time + 1e-12 < seconds:
        sim.step()
        sync_geometry(model, sim.data, geometry_data)

        active_beans = sim.bean_of
        for contact_id, contact in enumerate(sim.data.contact[: sim.data.ncon]):
            body1 = int(model.geom_bodyid[contact.geom1])
            body2 = int(model.geom_bodyid[contact.geom2])
            participants = [body for body in (body1, body2) if body in active_beans]
            if not participants:
                continue
            force = np.zeros(6)
            mujoco.mj_contactForce(model, sim.data, contact_id, force)
            normal_impulse = max(0.0, float(force[0])) * sim.dt
            penetration_depth = max(0.0, -float(contact.dist))
            for body in participants:
                bean = active_beans[body]
                other_body = body2 if body == body1 else body1
                qa = sim.body_qpos[body]
                region = "belt_feed" if sim.data.qpos[qa] < 0 else "downstream"
                values = contact_values[region][bean.cls]
                values["uids"].add(bean.uid)
                values["normal_impulses"].append(normal_impulse)
                values["penetration_depths"].append(penetration_depth)
                if other_body in active_beans:
                    values["bean_contact_samples"] += 1
                else:
                    values["fixture_contact_samples"] += 1

        for body, bean in active_beans.items():
            qa = sim.body_qpos[body]
            va = sim.body_qvel[body]
            x = float(sim.data.qpos[qa])
            vz = float(sim.data.qvel[va + 2])
            zones = []
            if -0.85 < x < 0:
                zones.append("post_feed")
            if abs(x - layout.cam_x) <= layout.cam_fov / 2:
                zones.append("inspection")
            if abs(x - layout.ej_x) <= JET_HALF_X:
                zones.append("jet")
            for zone in zones:
                values = motion_values[zone][bean.cls]
                values["uids"].add(bean.uid)
                values["collision_bottom_relative_to_belt"].append(
                    collision_bottom(model, geometry_data, sim.body_col[body])
                    - layout.belt_z
                )
                values["vz"].append(vz)

    spawn = {name: finish_spawn(spawn_values[name]) for name in class_names}
    contacts = {
        region: {name: finish_contacts(values[name]) for name in class_names}
        for region, values in contact_values.items()
    }
    motion = {
        zone: {name: finish_motion(values[name]) for name in class_names}
        for zone, values in motion_values.items()
    }
    return {
        "schema_version": 1,
        "preset_path": str(preset_path),
        "preset": preset,
        "versions": {
            "source_revision": subprocess.check_output(
                ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
            ).strip(),
            "source_sha256": {
                name: sha256(SIM_DIR / name)
                for name in ("sim.py", "scene.py", "profiles.py")
            },
            "evaluator_sha256": sha256(Path(__file__)),
            "preset_sha256": sha256(preset_path),
            "model_path": str(model_path),
            "model_sha256": sha256(model_path) if model_path.is_file() else None,
        },
        "measurement_scope": {
            "geometry_state": (
                "Geometry queries use a dedicated MjData. They cannot change the simulated solver state."
            ),
            "contact_impulse": (
                "Reported impulse is the solved contact force multiplied by one physics timestep."
            ),
            "inspection_zone_x_m": [
                layout.cam_x - layout.cam_fov / 2,
                layout.cam_x + layout.cam_fov / 2,
            ],
            "post_feed_zone_x_m": [-0.85, 0.0],
            "jet_zone_x_m": [layout.ej_x - JET_HALF_X, layout.ej_x + JET_HALF_X],
            "jets_enabled": False,
            "camera_enabled": False,
        },
        "runtime": {
            "requested_seconds": seconds,
            "simulated_seconds": float(sim.data.time),
            "physics_timestep_s": sim.dt,
            "requested_rate_objects_s": sim.rate,
            "objects_spawned": len(sim.beans),
            "admitted_rate_objects_s": len(sim.beans) / float(sim.data.time),
            "pool_starved": sim.starved,
            "native_thread_limits": {
                name: os.environ.get(name)
                for name in (
                    "OMP_NUM_THREADS",
                    "OPENBLAS_NUM_THREADS",
                    "MKL_NUM_THREADS",
                    "VECLIB_MAXIMUM_THREADS",
                )
            },
            "native_threadpools": [
                {
                    key: pool.get(key)
                    for key in ("internal_api", "prefix", "version", "num_threads")
                }
                for pool in threadpool_info()
            ],
        },
        "classes": class_names,
        "spawn": {"by_class": spawn},
        "contacts": {
            "belt_feed": {"by_class": contacts["belt_feed"]},
            "downstream": {"by_class": contacts["downstream"]},
        },
        "motion": {
            "post_feed": {"by_class": motion["post_feed"]},
            "inspection": {"by_class": motion["inspection"]},
            "jet": {"by_class": motion["jet"]},
        },
        "sample_counts_by_class": {
            name: {
                "spawn_objects": spawn[name]["objects"],
                "insertion_geom_pairs": spawn[name]["evaluated_existing_geom_pairs"],
                "belt_feed_contact_samples": contacts["belt_feed"][name]["samples"],
                "downstream_contact_samples": contacts["downstream"][name]["samples"],
                "post_feed_samples": motion["post_feed"][name]["samples"],
                "inspection_samples": motion["inspection"][name]["samples"],
                "jet_samples": motion["jet"][name]["samples"],
            }
            for name in class_names
        },
    }


def output_path(path: Path) -> Path:
    if path.suffix.lower() == ".json":
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    path.mkdir(parents=True, exist_ok=True)
    return path / "motion.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preset",
        type=Path,
        default=SIM_DIR / "configs" / "default_demo.json",
    )
    parser.add_argument("--seconds", type=float, default=2.0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not 0 < args.seconds <= 10:
        parser.error("seconds must be in the range 0 to 10")
    preset_path = args.preset.resolve()
    result = measure(preset_path, args.seconds)
    destination = output_path(args.out.resolve())
    destination.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(
        json.dumps(
            {
                "motion_report": str(destination),
                "simulation_seconds": result["runtime"]["simulated_seconds"],
                "objects_spawned": result["runtime"]["objects_spawned"],
            },
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
