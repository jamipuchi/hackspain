"""Demo 1: UR5e tracking a moving target with differential IK (MuJoCo + mink).

The end effector follows a circle in front of the robot (kinematic, no actuator dynamics). A green sphere shows the
target, so you can see the tracking error shrink to zero.

Run:
    cd ~/robotics && .venv/bin/mjpython demos/mink_ur5e_ik.py          # interactive viewer (mjpython on macOS)
    cd ~/robotics && .venv/bin/python demos/mink_ur5e_ik.py --headless # print tracking error only
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import mink
import mujoco
import mujoco.viewer
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SCENE = ROOT / "mujoco_menagerie" / "universal_robots_ur5e" / "scene.xml"

DT = 0.002  # simulation step (s)
SOLVER = "daqp"


def target_pose(t: float) -> mink.SE3:
    """Circle of radius 15 cm, centred in front of the base, tool pointing down."""
    center = np.array([0.45, 0.0, 0.45])
    radius = 0.15
    pos = center + radius * np.array([np.cos(0.8 * t), np.sin(0.8 * t), 0.0])
    # Tool z-axis pointing down (-Z world).
    rot = mink.SO3.from_matrix(np.array([[1, 0, 0], [0, -1, 0], [0, 0, -1]], dtype=float))
    return mink.SE3.from_rotation_and_translation(rot, pos)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true", help="no viewer, run 5 s and print errors")
    args = parser.parse_args()

    model = mujoco.MjModel.from_xml_path(str(SCENE))
    data = mujoco.MjData(model)

    configuration = mink.Configuration(model)

    ee_task = mink.FrameTask(
        frame_name="attachment_site",
        frame_type="site",
        position_cost=1.0,
        orientation_cost=0.5,
        lm_damping=1.0,
    )
    posture_task = mink.PostureTask(model, cost=1e-2)
    tasks = [ee_task, posture_task]

    limits = [mink.ConfigurationLimit(model)]

    # Start from the "home" keyframe shipped with the Menagerie model.
    mujoco.mj_resetDataKeyframe(model, data, model.key("home").id)
    configuration.update(data.qpos)
    posture_task.set_target_from_configuration(configuration)

    def step(t: float) -> float:
        target = target_pose(t)
        ee_task.set_target(target)
        vel = mink.solve_ik(configuration, tasks, DT, SOLVER, damping=1e-3, limits=limits)
        configuration.integrate_inplace(vel, DT)
        # Kinematic demo: the IK solution is the robot state (no actuator lag).
        data.qpos[:] = configuration.q
        mujoco.mj_forward(model, data)
        ee_pos = data.site("attachment_site").xpos
        return float(np.linalg.norm(ee_pos - target.translation()))

    if args.headless:
        t = 0.0
        while t < 5.0:
            err = step(t)
            if int(t / DT) % 500 == 0:
                print(f"t={t:4.1f}s  tracking error={err * 1000:6.1f} mm")
            t += DT
        print("done")
        return

    with mujoco.viewer.launch_passive(model, data, show_left_ui=False, show_right_ui=False) as viewer:
        viewer.cam.azimuth, viewer.cam.elevation, viewer.cam.distance = 140, -25, 2.0
        viewer.cam.lookat[:] = [0.3, 0.0, 0.4]
        t = 0.0
        while viewer.is_running():
            start = time.perf_counter()
            err = step(t)
            t += DT

            # Draw the target as a green sphere.
            viewer.user_scn.ngeom = 0
            mujoco.mjv_initGeom(
                viewer.user_scn.geoms[0],
                type=mujoco.mjtGeom.mjGEOM_SPHERE,
                size=[0.02, 0, 0],
                pos=target_pose(t).translation(),
                mat=np.eye(3).flatten(),
                rgba=[0.1, 0.9, 0.2, 0.8],
            )
            viewer.user_scn.ngeom = 1
            viewer.sync()

            if int(t / DT) % 250 == 0:
                print(f"t={t:5.2f}s  tracking error={err * 1000:5.1f} mm", end="\r")
            time.sleep(max(0.0, DT - (time.perf_counter() - start)))


if __name__ == "__main__":
    main()
