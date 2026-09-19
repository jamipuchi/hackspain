"""Build the MJCF of a belt-type optical sorter.

Layout (x = belt travel, y = across the belt, z = up):

   feeder ──▶ [ fast belt, 3 m/s ] ──▶ camera strip ──▶ belt end ──▶ ejector bank ──▶ splitter ──▶ accept / reject
             x=-L                       x=CAM_X       x=0            x=EJ_X            x=SPLIT_X
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np

from assets import ASSETS, N_VARIANTS, FAMILIES
from profiles import Profile, ELLIPSOID, HALF, BOX, CAPSULE


@dataclass(frozen=True)
class Layout:
    belt_len: float = 1.1          # m, belt runs from -belt_len to 0
    belt_w: float = 0.50           # m
    belt_z: float = 0.60           # top surface height
    belt_speed: float = 3.0        # m/s
    feed_x: tuple = (-1.05, -0.95)
    feed_vx: float = 2.6           # infeed hands beans over at this speed
    feed_drop: float = 0.006       # drop height onto the belt
    cam_x: float = -0.12           # inspection strip centre (upstream of the belt end)
    cam_fov: float = 0.048         # world height of the strip (m) along x
    cam_w: int = 2080              # px across the belt  (0.25 mm/px over 0.52 m)
    cam_h: int = 192
    ej_x: float = 0.10             # ejector bank, past the belt end
    n_nozzles: int = 64
    ej_z_offset: float = 0.045     # nozzles sit above the flight path
    split_x: float = 0.34
    split_z_drop: float = 0.125    # splitter blade this far below belt_z
    n_ellipsoid: int = 1150
    n_half: int = 48
    n_box: int = 20
    n_capsule: int = 20
    timestep: float = 0.002

    @property
    def nozzle_pitch(self): return self.belt_w / self.n_nozzles
    def nozzle_y(self, j): return -self.belt_w / 2 + self.nozzle_pitch * (j + 0.5)
    @property
    def cam_span_y(self): return self.cam_fov * self.cam_w / self.cam_h
    @property
    def px_per_m(self): return self.cam_h / self.cam_fov
    @property
    def split_z(self): return self.belt_z - self.split_z_drop


def _f(v): return " ".join(f"{x:.6g}" for x in v)


def build_xml(profile: Profile, L: Layout = Layout(), seed: int = 0) -> str:
    rng = np.random.default_rng(seed)
    tex_assets, mats = [], []
    for fam in FAMILIES:
        for k in range(N_VARIANTS):
            tex_assets.append(f'<texture name="tx_{fam}_{k}" type="2d" file="tex_{fam}_{k}.png"/>')
            mats.append(f'<material name="m_{fam}_{k}" texture="tx_{fam}_{k}" specular="0.25" shininess="0.35"/>')
    half_scales = [0.9, 1.0, 1.1]

    bodies = []
    # parked bodies float at x=+6 with gravity compensation (no contacts, ~zero cost)
    def park(i): return f"6 {(-2 + (i % 400) * 0.01):.3f} {1 + (i // 400) * 0.05:.3f}"
    for i in range(L.n_ellipsoid):
        bodies.append(f'<body name="e{i}" pos="{park(i)}" gravcomp="1"><freejoint/>'
                      f'<geom class="skin" type="ellipsoid" size="0.005 0.0036 0.0026" rgba="0.5 0.6 0.46 1" material="m_good_0"/>'
                      f'<geom class="bean" type="capsule" size="0.0026 0.0024" quat="0.7071068 0 0.7071068 0"/></body>')
    for i in range(L.n_half):
        s = half_scales[i % 3]
        bodies.append(f'<body name="h{i}" pos="{park(1000 + i)}" gravcomp="1"><freejoint/>'
                      f'<geom class="skin" type="mesh" mesh="half_{i % 3}" rgba="0.5 0.6 0.46 1" material="m_good_0"/>'
                      f'<geom class="bean" type="capsule" size="{0.0013 * s:.5f} {0.0037 * s:.5f}" pos="0 0 {0.0013 * s:.5f}" quat="0.7071068 0 0.7071068 0"/></body>')
    for i in range(L.n_box):
        bodies.append(f'<body name="b{i}" pos="{park(1100 + i)}" gravcomp="1"><freejoint/>'
                      f'<geom class="bean" type="box" size="0.004 0.003 0.0025" rgba="0.45 0.44 0.42 1" group="0"/></body>')
    for i in range(L.n_capsule):
        bodies.append(f'<body name="c{i}" pos="{park(1200 + i)}" gravcomp="1"><freejoint/>'
                      f'<geom class="bean" type="capsule" size="0.0012 0.01" rgba="0.42 0.3 0.16 1" group="0"/></body>')

    meshes = "".join(f'<mesh name="half_{k}" file="half_bean.obj" scale="{_f(np.array([0.0050, 0.0036, 0.0026]) * s)}"/>' for k, s in enumerate(half_scales))

    nozzles = "".join(
        f'<site name="nz{j}" pos="{L.ej_x:.4f} {L.nozzle_y(j):.5f} {L.belt_z + L.ej_z_offset:.4f}" size="0.002 0.002 0.008" type="cylinder" rgba="0.75 0.75 0.78 1" group="1"/>'
        for j in range(L.n_nozzles))
    br, bg, bb = profile.belt_rgb
    bz = L.belt_z
    xml = f"""<mujoco model="coffee_belt_sorter">
  <compiler angle="radian" texturedir="{ASSETS}" meshdir="{ASSETS}"/>
  <option timestep="{L.timestep}" integrator="implicitfast" solver="CG" iterations="12" tolerance="1e-6" cone="pyramidal"/>
  <visual>
    <global offwidth="{max(L.cam_w, 1600)}" offheight="{max(L.cam_h, 900)}"/>
    <quality shadowsize="0"/>
    <headlight ambient="0.28 0.28 0.28" diffuse="0.3 0.3 0.3" specular="0.1 0.1 0.1"/>
    <map znear="0.01" zfar="20"/>
  </visual>
  <asset>
    <texture type="skybox" builtin="gradient" rgb1="0.82 0.85 0.9" rgb2="0.45 0.48 0.55" width="64" height="64"/>
    <texture name="floor" type="2d" builtin="checker" rgb1="0.28 0.28 0.3" rgb2="0.33 0.33 0.35" width="256" height="256"/>
    <material name="floor" texture="floor" texrepeat="12 12" reflectance="0.05"/>
    <material name="steel" rgba="0.62 0.64 0.68 1" specular="0.5" shininess="0.6"/>
    <material name="frame" rgba="0.15 0.16 0.18 1"/>
    <material name="belt" rgba="{br} {bg} {bb} 1" specular="0.05" shininess="0.1"/>
    <material name="chute" rgba="0.55 0.58 0.62 0.55" specular="0.3"/>
    {''.join(tex_assets)}
    {''.join(mats)}
    {meshes}
  </asset>
  <default>
    <geom condim="3" friction="0.9 0.01 0.0005" solref="0.004 1" solimp="0.95 0.99 0.001"/>
    <default class="bean">
      <geom condim="3" friction="0.8 0.01 0.0005" solref="0.006 1" priority="1" mass="0.00015" group="3"/>
    </default>
    <default class="skin"><geom contype="0" conaffinity="0" mass="0" group="0"/></default>
    <default class="static"><geom contype="1" conaffinity="1" group="1"/></default>
    <default class="visual"><geom contype="0" conaffinity="0" group="1"/></default>
  </default>
  <worldbody>
    <light directional="true" pos="0 0 3" dir="0 0 -1" castshadow="false" diffuse="0.45 0.45 0.45" specular="0.1 0.1 0.1"/>
    <light pos="{L.cam_x} 0 {bz + 0.6}" dir="0 0 -1" castshadow="false" diffuse="0.45 0.45 0.45" specular="0.1 0.1 0.1" cutoff="60"/>
    <light pos="-0.6 -0.8 {bz + 1.0}" dir="0.3 0.6 -0.8" castshadow="false" diffuse="0.25 0.25 0.27"/>
    <geom name="floor" type="box" size="4 3 0.02" pos="0 0 -0.02" material="floor" class="static"/>

    <!-- belt table -->
    <body name="belt" pos="{-L.belt_len / 2:.4f} 0 {bz - 0.004:.4f}">
      <joint name="belt" type="slide" axis="1 0 0" damping="0"/>
      <geom name="belt_top" type="box" size="{L.belt_len / 2:.4f} {L.belt_w / 2:.4f} 0.004" material="belt" class="static" group="2" mass="500"/>
    </body>
    <geom type="box" size="{L.belt_len / 2:.4f} {L.belt_w / 2 + 0.03:.4f} 0.04" pos="{-L.belt_len / 2:.4f} 0 {bz - 0.05:.4f}" material="frame" class="visual"/>
    <geom type="box" size="{L.belt_len / 2:.4f} 0.004 0.012" pos="{-L.belt_len / 2:.4f} {L.belt_w / 2 + 0.004:.4f} {bz + 0.012:.4f}" material="steel" class="static"/>
    <geom type="box" size="{L.belt_len / 2:.4f} 0.004 0.012" pos="{-L.belt_len / 2:.4f} {-L.belt_w / 2 - 0.004:.4f} {bz + 0.012:.4f}" material="steel" class="static"/>
    <body name="roller_head" pos="0 0 {bz - 0.03:.4f}"><joint name="roller_head" type="hinge" axis="0 1 0" damping="0"/>
      <geom type="cylinder" size="0.03 {L.belt_w / 2 + 0.01:.4f}" zaxis="0 1 0" material="steel" class="visual"/></body>
    <body name="roller_tail" pos="{-L.belt_len:.4f} 0 {bz - 0.03:.4f}"><joint name="roller_tail" type="hinge" axis="0 1 0" damping="0"/>
      <geom type="cylinder" size="0.03 {L.belt_w / 2 + 0.01:.4f}" zaxis="0 1 0" material="steel" class="visual"/></body>
    <!-- legs -->
    <geom type="box" size="0.02 0.02 {(bz - 0.09) / 2:.4f}" pos="-0.1 {L.belt_w / 2 + 0.05:.3f} {(bz - 0.09) / 2:.4f}" material="frame" class="visual"/>
    <geom type="box" size="0.02 0.02 {(bz - 0.09) / 2:.4f}" pos="-0.1 {-L.belt_w / 2 - 0.05:.3f} {(bz - 0.09) / 2:.4f}" material="frame" class="visual"/>
    <geom type="box" size="0.02 0.02 {(bz - 0.09) / 2:.4f}" pos="{-L.belt_len + 0.1:.3f} {L.belt_w / 2 + 0.05:.3f} {(bz - 0.09) / 2:.4f}" material="frame" class="visual"/>
    <geom type="box" size="0.02 0.02 {(bz - 0.09) / 2:.4f}" pos="{-L.belt_len + 0.1:.3f} {-L.belt_w / 2 - 0.05:.3f} {(bz - 0.09) / 2:.4f}" material="frame" class="visual"/>

    <!-- infeed hopper (visual) -->
    <geom type="box" size="0.08 {L.belt_w / 2 + 0.02:.4f} 0.004" pos="{L.feed_x[0] - 0.05:.3f} 0 {bz + 0.12:.3f}" euler="0 -0.35 0" material="steel" class="visual"/>
    <geom type="box" size="0.08 0.004 0.05" pos="{L.feed_x[0] - 0.05:.3f} {L.belt_w / 2 + 0.02:.4f} {bz + 0.16:.3f}" euler="0 -0.35 0" material="steel" class="visual"/>
    <geom type="box" size="0.08 0.004 0.05" pos="{L.feed_x[0] - 0.05:.3f} {-L.belt_w / 2 - 0.02:.4f} {bz + 0.16:.3f}" euler="0 -0.35 0" material="steel" class="visual"/>

    <!-- inspection camera housing + line light -->
    <geom type="box" size="0.05 {L.belt_w / 2 + 0.06:.4f} 0.03" pos="{L.cam_x:.3f} 0 {bz + 0.42:.3f}" material="frame" class="visual"/>
    <geom type="box" size="0.006 {L.belt_w / 2 + 0.02:.4f} 0.006" pos="{L.cam_x - 0.05:.3f} 0 {bz + 0.25:.3f}" rgba="1 1 0.9 1" class="visual"/>
    <geom type="box" size="0.006 {L.belt_w / 2 + 0.02:.4f} 0.006" pos="{L.cam_x + 0.05:.3f} 0 {bz + 0.25:.3f}" rgba="1 1 0.9 1" class="visual"/>
    <camera name="inspect" pos="{L.cam_x:.4f} 0 {bz + 0.40:.4f}" xyaxes="0 1 0 -1 0 0" projection="orthographic" fovy="{L.cam_fov}"/>

    <!-- ejector manifold -->
    <geom type="box" size="0.012 {L.belt_w / 2 + 0.02:.4f} 0.012" pos="{L.ej_x:.3f} 0 {bz + L.ej_z_offset + 0.018:.4f}" material="steel" class="visual"/>
    {nozzles}

    <!-- splitter blade and chutes (accept above, reject below) -->
    <geom name="splitter" type="box" size="0.14 {L.belt_w / 2 + 0.02:.4f} 0.002" pos="{L.split_x + 0.14:.3f} 0 {L.split_z:.4f}" euler="0 0.25 0" material="steel" class="static"/>
    <geom type="box" size="0.16 0.002 0.16" pos="{L.split_x + 0.12:.3f} {L.belt_w / 2 + 0.022:.4f} {bz - 0.12:.3f}" material="chute" class="visual"/>
    <geom type="box" size="0.16 0.002 0.16" pos="{L.split_x + 0.12:.3f} {-L.belt_w / 2 - 0.022:.4f} {bz - 0.12:.3f}" material="chute" class="visual"/>
    <geom name="bin_accept" type="box" size="0.12 {L.belt_w / 2 + 0.02:.4f} 0.003" pos="{L.split_x + 0.30:.3f} 0 {bz - 0.30:.3f}" rgba="0.2 0.6 0.25 1" class="static"/>
    <geom name="bin_reject" type="box" size="0.12 {L.belt_w / 2 + 0.02:.4f} 0.003" pos="{L.split_x + 0.06:.3f} 0 {bz - 0.42:.3f}" rgba="0.75 0.2 0.15 1" class="static"/>
    <geom type="box" size="0.003 {L.belt_w / 2 + 0.02:.4f} 0.06" pos="{L.split_x + 0.42:.3f} 0 {bz - 0.24:.3f}" rgba="0.2 0.6 0.25 0.5" class="static"/>
    <geom type="box" size="0.003 {L.belt_w / 2 + 0.02:.4f} 0.06" pos="{L.split_x + 0.18:.3f} 0 {bz - 0.36:.3f}" rgba="0.75 0.2 0.15 0.5" class="static"/>

    <!-- overview cameras for humans -->
    <camera name="overview" pos="0.55 -1.35 {bz + 0.75:.3f}" xyaxes="0.92 0.39 0 -0.17 0.4 0.9" fovy="50"/>
    <camera name="discharge" pos="0.25 -0.75 {bz + 0.12:.3f}" xyaxes="1 0 0 0 0.25 0.97" fovy="40"/>
    <camera name="topdown" pos="-0.55 0 {bz + 1.6:.3f}" xyaxes="0 1 0 -1 0 0" fovy="55"/>
    {''.join(bodies)}
  </worldbody>
</mujoco>"""
    return xml
