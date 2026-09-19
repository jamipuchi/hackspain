from collections import defaultdict
import json
import sys
from pathlib import Path

import mujoco
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "sim/coffee_sorter"))
from profiles import PROFILES
from scene import Layout
from sim import SorterSim


layout = Layout(n_ellipsoid=400, n_half=48, n_box=20, n_capsule=20)
sim = SorterSim(PROFILES["green_arabica"], layout, rate=500, seed=8)
spawn_clearance = defaultdict(list)
post_feed_max_clearance = defaultdict(list)
original_spawn = sim.spawn


def collision_bottom(body):
    geom = sim.body_col[body]
    kind = int(sim.model.geom_type[geom])
    size = sim.model.geom_size[geom]
    rotation = sim.data.geom_xmat[geom].reshape(3, 3)
    if kind == int(mujoco.mjtGeom.mjGEOM_CAPSULE):
        extent = size[0] + size[1] * abs(rotation[2, 2])
    elif kind == int(mujoco.mjtGeom.mjGEOM_BOX):
        extent = float(np.sum(size * np.abs(rotation[2])))
    else:
        raise RuntimeError(f"unexpected collision geom {kind}")
    return float(sim.data.geom_xpos[geom, 2] - extent)


def spawn(spec=None):
    bean = original_spawn(spec)
    if bean is not None:
        mujoco.mj_forward(sim.model, sim.data)
        spawn_clearance[bean.cls].append(collision_bottom(bean.body) - layout.belt_z)
    return bean


sim.spawn = spawn
for _ in range(int(1.5 / sim.dt)):
    sim.step()
    mujoco.mj_forward(sim.model, sim.data)
    for body, bean in sim.bean_of.items():
        qa = sim.body_qpos[body]
        if -0.85 < sim.data.qpos[qa] < 0:
            post_feed_max_clearance[bean.cls].append(collision_bottom(body) - layout.belt_z)


def summary(values):
    return {name: {"n": len(x), "min_m": min(x), "max_m": max(x)} for name, x in sorted(values.items())}


print(json.dumps({
    "spawn_clearance_m": summary(spawn_clearance),
    "post_feed_collision_bottom_clearance_m": summary(post_feed_max_clearance),
    "steps": int(1.5 / sim.dt),
    "spawned": len(sim.beans),
    "starved": sim.starved,
}, indent=2, sort_keys=True))
