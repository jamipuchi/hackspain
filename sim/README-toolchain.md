# Robotics toolchain (macOS arm64)

Everything lives in this folder. Two environments:

| Environment | Path | What it holds |
| --- | --- | --- |
| Python venv (uv) | `.venv/` | MuJoCo 3.13, mink 1.3, Pinocchio 4.1, numpy/scipy |
| pixi / RoboStack | `ros2_ws/` | ROS 2 Jazzy desktop, MoveIt 2, ros2_control, Gazebo Harmonic (gz-sim 8.10) + ros_gz, colcon |

Repos: `mujoco_menagerie/` (robot models), `melfa_ros2_driver/` (Mitsubishi arms; also symlinked into `ros2_ws/src`).

Not installed: **NVIDIA Isaac Sim / Isaac Lab** needs an NVIDIA GPU on Linux or Windows. Use a cloud box if you need it.

## Demo 0 — GPT-6 sorts ferrous hardware with an Arduino servo arm + electromagnet (start here)

```bash
cd ~/robotics/magnet_sorter
../.venv/bin/python run_demo.py                 # GPT-6 + Cycles-rendered phone photos
../.venv/bin/python run_demo.py --phone mujoco  # fast, no Blender
```

Arduino Uno, three MG996R servos, IRF520 MOSFET, 5 V electromagnet, breadboard and wires on an MDF
board with ArUco markers; real-looking nuts, bolts and washers in steel, stainless, brass, copper and
aluminium. MuJoCo physics, Blender Cycles rendering, GPT-6 learning from magnet feedback, real serial
protocol + Arduino sketch. See `magnet_sorter/README.md`. The earlier cartoon version lives in `astra_sort/`.

## Demo 1 — MuJoCo + mink IK

```bash
cd ~/robotics
.venv/bin/mjpython demos/mink_ur5e_ik.py            # opens the MuJoCo viewer (mjpython, not python, on macOS)
.venv/bin/python   demos/mink_ur5e_ik.py --headless # prints tracking error only
```

A UR5e from Menagerie follows a green target sphere moving in a circle using differential IK (`mink.solve_ik` with a frame task, a posture task and joint limits). Tracking error settles under 2 mm.

## Demo 2 — Pinocchio vs MuJoCo kinematics

```bash
.venv/bin/python demos/pinocchio_fk_check.py
```

Loads the same MJCF in Pinocchio, compares forward kinematics against MuJoCo (matches to floating-point precision) and prints the mass matrix and gravity torques. Note: Pinocchio's MJCF loader ignores the root body pose, so compare in the base frame.

## Browse any Menagerie robot

```bash
.venv/bin/python -m mujoco.viewer --mjcf mujoco_menagerie/franka_emika_panda/scene.xml
ls mujoco_menagerie/    # ~70 robots: arms, grippers, quadrupeds, humanoids, mobile bases
```

## ROS 2 + MoveIt 2 (pixi)

Every ROS command runs inside the pixi env. Either prefix with `pixi run` or drop into a shell:

```bash
cd ~/robotics/ros2_ws
pixi shell                   # activates ROS 2 Jazzy; the colcon overlay is auto-sourced via `[activation]` in pixi.toml
```

Convenience tasks (`pixi run <task>`):

| Task | What it launches |
| --- | --- |
| `moveit-demo` | MoveIt 2 + RViz with the Franka Panda on mock hardware. Drag the interactive marker, click *Plan & Execute*. |
| `melfa-demo` | Mitsubishi RV-5AS on mock hardware (ros2_control + RViz). |
| `melfa-moveit` | MoveIt 2 for the RV-5AS. Run in a second terminal alongside `melfa-demo`. |
| `gz` | Gazebo Harmonic GUI with the shapes world. |
| `gz-ros-demo` | Gazebo diff-drive robot bridged into ROS 2 (`ros_gz_sim_demos`). |
| `build` | `colcon build --symlink-install` of `src/` (MELFA packages). |

Other MELFA arms: `rv2fr rv4fr rv4frl rv7frl rv8crl rv13frl rv80fr rh6frh5520 rh6crh6020` all have `melfa_bringup/<name>_control.launch.py` and `melfa_<name>_moveit_config/<name>_moveit.launch.py`.

### MELFA driver notes

- Upstream only has a `humble` branch. It builds against Jazzy except `melfa_driver` (the real-robot UDP hardware interface; Linux-only socket headers). It is excluded with a `COLCON_IGNORE` file. Everything needed for simulation builds: description, msgs, bringup, io_controllers and the 10 MoveIt configs.
- The hardware xacros were patched from `fake_components/GenericSystem` to `mock_components/GenericSystem` (the plugin was renamed after Humble). This is an uncommitted local change in `melfa_ros2_driver/`.

## Adding packages

```bash
cd ~/robotics/ros2_ws
pixi add ros-jazzy-<package-name>      # binary ROS packages from RoboStack
pixi add <conda-forge-package>
cd ~/robotics && uv pip install --python .venv/bin/python <pypi-package>
```

Source packages go in `ros2_ws/src/` and are built with `pixi run build`.

## Gotchas

- macOS has no `timeout`; use `gtimeout` from `brew install coreutils` or a `sleep && kill`.
- Homebrew refuses the `osrf/simulation` tap as untrusted (`brew trust osrf/simulation` would allow it). Not needed: Gazebo Harmonic is already in the pixi env via `ros-jazzy-ros-gz`.
- First launch of MoveIt is slow (dyld cache); controllers take ~30 s to go active the first time.
- `mujoco.viewer.launch_passive` only works under `.venv/bin/mjpython` on macOS; plain `python` raises an error.
