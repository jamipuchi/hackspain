"""Physics of the belt sorter: spawning with ground truth, friction-driven belt transport, air-jet ejectors,
accept/reject capture and body recycling. Pure MuJoCo; the controller never touches this
except through `fire()` (the ejector valves) and the camera.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np
import mujoco

from assets import N_VARIANTS
from profiles import Profile, ClassSpec, sample_instance, ELLIPSOID, HALF, BOX, CAPSULE
from scene import Layout, build_xml

ROT_TAU = 1.5e-3         # s: rotational damping time constant (damping = I / tau). Tumbling settles in ms,
                         # as on a real belt, and the explicit gyroscopic term can never run away.
ROT_ARMATURE_FACTOR = 2  # added rotational inertia as a multiple of the body's own (numerical safety)
JET_FORCE = 0.09          # N per nozzle on a body inside the jet
JET_HALF_X = 0.010        # m, jet footprint along travel
JET_HALF_Y_FACTOR = 0.75  # jet half-width as a multiple of the nozzle pitch (jets overlap slightly)


@dataclass
class Bean:
    uid: int
    body: int
    cls: str
    defect: bool
    spawn_t: float
    axes: np.ndarray
    mass: float
    outcome: str | None = None       # accept | reject | spilled
    resolved_t: float | None = None
    targeted: bool = False           # a valve was fired for this bean
    fired_target: bool = False       # a scheduled valve actually reached its on-time
    jet_hits: int = 0                # physics steps during which a jet pushed it
    camera_observations: int = 0     # full camera blobs containing this bean's rendered centre
    merged_observations: int = 0     # those observations whose component contains >=2 bean centres
    last_pos: tuple | None = None    # where it was recycled (diagnostics)
    last_quat: tuple | None = None   # WXYZ orientation at the same collection pose


@dataclass
class Fire:
    nozzle: int
    t_on: float
    t_off: float
    force: float
    uid: int | None = None
    activated: bool = False
    hit_objects: set[int] = field(default_factory=set)

    def records_first_hit(self, object_id: int) -> bool:
        if object_id in self.hit_objects:
            return False
        self.hit_objects.add(object_id)
        return True


class SorterSim:
    def __init__(self, profile: Profile, layout: Layout = Layout(), rate: float = 1500.0,
                 seed: int = 0, defect_boost: float = 1.0):
        if mujoco.__version__ != "3.13.0":
            raise RuntimeError("Pooled physics requires MuJoCo 3.13.0. Revalidate the constant refresh before changing versions.")
        self.P, self.L = profile, layout
        self.rate = rate
        self.rng = np.random.default_rng(seed)
        self.priors = profile.priors(defect_boost)
        self.model = mujoco.MjModel.from_xml_string(build_xml(profile, layout, seed))
        self.data = mujoco.MjData(self.model)
        m = self.model
        self.dt = m.opt.timestep

        # body pools --------------------------------------------------------------
        self.pools = {ELLIPSOID: [], HALF: [], BOX: [], CAPSULE: []}
        prefix = {"e": ELLIPSOID, "h": HALF, "b": BOX, "c": CAPSULE}
        self.body_geom, self.body_col, self.body_qpos, self.body_qvel = {}, {}, {}, {}
        for b in range(1, m.nbody):
            name = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, b)
            if name and name[0] in prefix and name[1:].isdigit():
                self.pools[prefix[name[0]]].append(b)
                self.body_geom[b] = m.body_geomadr[b]                       # visual ("skin") geom
                self.body_col[b] = m.body_geomadr[b] + m.body_geomnum[b] - 1  # collision geom (same geom if single)
                j = m.body_jntadr[b]
                self.body_qpos[b] = m.jnt_qposadr[j]
                self.body_qvel[b] = m.jnt_dofadr[j]
        self.free = {k: list(v) for k, v in self.pools.items()}
        self.all_bodies = np.array(sorted(self.body_geom))
        self.qpos_adr = np.array([self.body_qpos[b] for b in self.all_bodies])
        self.qvel_adr = np.array([self.body_qvel[b] for b in self.all_bodies])
        self.geom_of = np.array([self.body_geom[b] for b in self.all_bodies])
        self.body_index = {b: i for i, b in enumerate(self.all_bodies)}
        self.collision_body = {geom: body for body, geom in self.body_col.items()}
        self.active = np.zeros(len(self.all_bodies), bool)
        self.continuous = False
        self.bean_of = {}                  # body -> Bean (active)
        self.beans: list[Bean] = []        # every bean ever spawned (ground truth log)
        self.bean_by_uid: dict[int, Bean] = {}
        self._spawned_events: list[Bean] = []
        self._outcome_events: list[Bean] = []
        self._retired_events: list[Bean] = []
        self._activated_events: list[int] = []
        self._fire_hit_events: list[tuple[int, int]] = []
        self.material_ids = {}
        for fam in ("good", "faded", "black", "sour", "insect", "roast"):
            self.material_ids[fam] = [mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_MATERIAL, f"m_{fam}_{k}") for k in range(N_VARIANTS)]
        jb = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, "belt")
        self.belt_qpos, self.belt_qvel = m.jnt_qposadr[jb], m.jnt_dofadr[jb]
        self.roller_head = m.jnt_qposadr[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, "roller_head")]
        self.roller_tail = m.jnt_qposadr[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, "roller_tail")]
        self.nozzle_y = np.array([layout.nozzle_y(j) for j in range(layout.n_nozzles)])
        self.fires: list[Fire] = []
        self.n_fired = 0                   # valve commands accepted (legacy name)
        self.n_activated = 0               # valve commands that reached t_on in simulation
        self.fired_targets: set[int] = set()
        self.fire_hits: set[tuple[int, int]] = set()  # (controller track id, bean uid)
        self.uid = 0
        self.n_spawned = 0
        self.starved = 0
        self.spawn_accum = 0.0
        self.class_by_name = {c.name: c for c in profile.classes}
        mujoco.mj_forward(m, self.data)
        self.collection_geom = {
            mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, "bin_accept"): "accept",
            mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, "bin_reject"): "reject",
        }
        self.ground_geom = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, "floor")
        end_walls = [
            mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, name)
            for name in ("bin_accept_end", "bin_reject_end")
        ]
        # A body must clear the exterior of both collection end walls.
        self.collection_end_x = max(
            m.geom_pos[geom, 0] + m.geom_size[geom, 0] for geom in end_walls
        )
        # The local refresh below depends on independent free bodies and fixed frames.
        if m.ntendon or m.nflex or m.nu or m.neq or m.nplugin:
            raise ValueError("Pooled physics requires no coupled model elements.")
        if (np.any(m.cam_mode != mujoco.mjtCamLight.mjCAMLIGHT_FIXED)
                or np.any(m.light_mode != mujoco.mjtCamLight.mjCAMLIGHT_FIXED)):
            raise ValueError("Pooled physics requires fixed cameras and lights.")
        expected_geoms = {
            ELLIPSOID: (mujoco.mjtGeom.mjGEOM_ELLIPSOID, mujoco.mjtGeom.mjGEOM_CAPSULE),
            HALF: (mujoco.mjtGeom.mjGEOM_MESH, mujoco.mjtGeom.mjGEOM_CAPSULE),
            BOX: (mujoco.mjtGeom.mjGEOM_BOX,), CAPSULE: (mujoco.mjtGeom.mjGEOM_CAPSULE,),
        }
        shape_of = {body: shape for shape, bodies in self.pools.items() for body in bodies}
        self.inertia_axes = {}
        for b in self.all_bodies:
            j, qa = m.body_jntadr[b], self.body_qpos[b]
            leaf = m.body_bvhadr[b]
            if (m.body_parentid[b] != 0 or np.any(m.body_parentid == b)
                    or m.body_jntnum[b] != 1 or m.jnt_type[j] != mujoco.mjtJoint.mjJNT_FREE
                    or m.body_bvhnum[b] != 1 or m.bvh_nodeid[leaf] != self.body_col[b]
                    or not np.array_equal(m.qpos0[qa + 3:qa + 7], [1, 0, 0, 0])):
                raise ValueError("Pooled physics requires independent free bodies with one collision leaf and an identity reference rotation.")
            geoms = slice(m.body_geomadr[b], m.body_geomadr[b] + m.body_geomnum[b])
            if tuple(m.geom_type[geoms]) != expected_geoms[shape_of[b]]:
                raise ValueError("Pooled physics requires the compiled geometry types for each pool.")
            rotation = np.empty(9)
            mujoco.mju_quat2Mat(rotation, m.body_iquat[b])
            axes = np.abs(rotation.reshape(3, 3))
            if not np.allclose(axes @ axes.T, np.eye(3), atol=1e-12):
                raise ValueError("Pooled physics requires axis-aligned principal inertia.")
            self.inertia_axes[b] = axes.argmax(axis=0)
        self._stat_points = np.concatenate((
            self.data.xpos[1:], self.data.xipos[1:], self.data.xanchor,
            self.data.site_xpos, self.data.geom_xpos))
        self._stat_radii = np.zeros(len(self._stat_points))

    def _refresh_body_constants(self, b):
        """Refresh operational physics constants without changing live data.

        MuJoCo 3.13 caches simple-body inertia in dof_M0. Full mj_setConst is
        quadratic in pool size. This local equivalent also includes armature,
        offset COM, inverse weights, subtree mass, and solver length scales.
        The read-only ngravcomp count retains its compiled value. Runtime gravity
        uses flg_gravcomp and body_gravcomp instead.
        """
        m, va = self.model, self.body_qvel[b]
        mass, inertia = m.body_mass[b], m.body_inertia[b]
        rotation = np.empty(9)
        mujoco.mju_quat2Mat(rotation, m.body_iquat[b])
        rotation = rotation.reshape(3, 3)
        x, y, z = m.body_ipos[b]
        jacobian = np.eye(6)
        jacobian[:3, 3:] = [[0, z, -y], [-z, 0, x], [y, -x, 0]]
        spatial_mass = np.zeros((6, 6))
        spatial_mass[:3, :3] = mass * np.eye(3)
        spatial_mass[3:, 3:] = rotation @ np.diag(inertia) @ rotation.T
        mass_matrix = jacobian.T @ spatial_mass @ jacobian + np.diag(m.dof_armature[va:va + 6])
        inverse_mass = np.linalg.inv(mass_matrix)
        body_inverse = jacobian @ inverse_mass @ jacobian.T
        old_mass = m.body_subtreemass[b]
        old_inertia = m.dof_M0[va:va + 6].sum()
        old_size = m.dof_length[va + 3]
        m.dof_M0[va:va + 6] = np.diag(mass_matrix)
        m.dof_invweight0[va:va + 3] = np.diag(inverse_mass)[:3].mean()
        m.dof_invweight0[va + 3:va + 6] = np.diag(inverse_mass)[3:].mean()
        m.body_invweight0[b] = [np.diag(body_inverse)[:3].mean(), np.diag(body_inverse)[3:].mean()]
        m.body_subtreemass[b] = mass
        m.body_subtreemass[0] += mass - old_mass
        geoms = slice(m.body_geomadr[b], m.body_geomadr[b] + m.body_geomnum[b])
        size = max(1e-5, np.linalg.norm(m.body_ipos[b]),
                   np.max(m.geom_rbound[geoms] + np.linalg.norm(m.geom_pos[geoms] - m.body_ipos[b], axis=1)))
        m.dof_length[va + 3:va + 6] = size
        m.stat.meanmass += (mass - old_mass) / (m.nbody - 1)
        m.stat.meaninertia += (np.trace(mass_matrix) - old_inertia) / m.nv
        m.stat.meansize += (size - old_size) / (m.nbody - 1)
        self._stat_radii[-m.ngeom:] = m.geom_rbound
        lower = np.min(self._stat_points - self._stat_radii[:, None], axis=0)
        upper = np.max(self._stat_points + self._stat_radii[:, None], axis=0)
        m.stat.center[:] = (lower + upper) / 2
        m.stat.extent = max(1e-5, np.max(upper - lower), 2 * m.stat.meansize)

    def _configure_body(self, b, shape, axes, mass):
        """Apply sampled geometry and physical properties for both feeder paths."""
        m, g = self.model, self.body_geom[b]
        gc = self.body_col[b]
        if shape == ELLIPSOID:
            m.geom_size[g] = axes
            m.geom_rbound[g] = np.max(axes)
            half = axes
            r, hl = axes[2], max(axes[0] - axes[2], 1e-4)     # collision capsule: same length & resting height
            m.geom_size[gc, 0], m.geom_size[gc, 1] = r, hl
            m.geom_rbound[gc] = hl + r
            # Bounds use the capsule's local z axis, before geom_quat rotates it.
            m.geom_aabb[gc, 3:6] = [r, r, hl + r]
            inertia = mass / 5 * np.array([axes[1] ** 2 + axes[2] ** 2, axes[0] ** 2 + axes[2] ** 2, axes[0] ** 2 + axes[1] ** 2])
        elif shape == BOX:
            m.geom_size[g] = axes
            m.geom_rbound[g] = np.linalg.norm(axes)
            half = axes
            inertia = mass / 3 * np.array([axes[1] ** 2 + axes[2] ** 2, axes[0] ** 2 + axes[2] ** 2, axes[0] ** 2 + axes[1] ** 2])
        elif shape == CAPSULE:
            r, hl = axes[1], axes[0]
            m.geom_size[g, 0], m.geom_size[g, 1] = r, hl
            m.geom_rbound[g] = hl + r
            half = np.array([r, r, hl + r])
            inertia = np.array([mass * (r ** 2 / 4 + hl ** 2 / 3)] * 2 + [mass * r ** 2 / 2])
        else:  # HALF mesh: fixed scale variants, read the compiled size back
            half = m.geom_aabb[g, 3:6].copy()
            axes = half
            # Ellipsoid approximation from visual bounds, around the compiled collider COM.
            inertia = mass / 5 * np.array([axes[1] ** 2 + axes[2] ** 2, axes[0] ** 2 + axes[2] ** 2, axes[0] ** 2 + axes[1] ** 2])
        if shape != HALF:
            m.geom_aabb[g, 3:6] = half
            # Each pooled body has one collision leaf, stored in its inertial frame.
            inertial_rotation, geom_rotation = np.empty(9), np.empty(9)
            mujoco.mju_quat2Mat(inertial_rotation, m.body_iquat[b])
            mujoco.mju_quat2Mat(geom_rotation, m.geom_quat[gc])
            inertial_rotation = inertial_rotation.reshape(3, 3).T
            geom_rotation = geom_rotation.reshape(3, 3)
            leaf = m.body_bvhadr[b]
            m.bvh_aabb[leaf, :3] = inertial_rotation @ (
                m.geom_pos[gc] + geom_rotation @ m.geom_aabb[gc, :3] - m.body_ipos[b])
            m.bvh_aabb[leaf, 3:] = np.abs(inertial_rotation @ geom_rotation) @ m.geom_aabb[gc, 3:]
        m.body_mass[b] = mass
        # Analytic inertia uses body axes, while MuJoCo stores principal-frame axes.
        m.body_inertia[b] = np.maximum(inertia[self.inertia_axes[b]], 1e-12)
        va0 = self.body_qvel[b]
        i_mean = m.body_inertia[b].mean()
        m.dof_damping[va0 + 3:va0 + 6] = i_mean / ROT_TAU
        m.dof_armature[va0 + 3:va0 + 6] = ROT_ARMATURE_FACTOR * i_mean
        self._refresh_body_constants(b)
        return axes, half

    # ------------------------------------------------------------------ spawning
    def _sample_class(self) -> ClassSpec:
        return self.P.classes[self.rng.choice(len(self.P.classes), p=self.priors)]

    def spawn(self, spec: ClassSpec | None = None) -> Bean | None:
        m, d, L, rng = self.model, self.data, self.L, self.rng
        spec = spec or self._sample_class()
        if not self.free[spec.shape]:
            self.starved += 1
            return None
        b = self.free[spec.shape].pop()
        g = self.body_geom[b]
        axes, rgba, mass = sample_instance(spec, rng)
        axes, half = self._configure_body(b, spec.shape, axes, mass)
        m.geom_rgba[g] = rgba
        m.geom_matid[g] = int(rng.choice(self.material_ids[spec.texture])) if spec.texture else -1
        # pose: flat on the belt with random yaw, small random tilt; capsule lies along x by default (z-axis -> x)
        yaw = rng.uniform(-np.pi, np.pi)
        tilt = rng.normal(0, 0.15, 2)
        q = np.zeros(4)
        if spec.shape == CAPSULE:
            mujoco.mju_euler2Quat(q, np.array([tilt[0], np.pi / 2 + tilt[1], yaw]), "xyz")
        else:
            mujoco.mju_euler2Quat(q, np.array([tilt[0], tilt[1], yaw]), "xyz")
        if spec.shape == CAPSULE:
            r, hl = axes[1], axes[0]
            rotation = np.empty(9)
            mujoco.mju_quat2Mat(rotation, q)
            half[2] = r + hl * abs(rotation[8])
        qa, va = self.body_qpos[b], self.body_qvel[b]
        margin = 0.012
        pos = self._free_spot(half, margin)
        if pos is None:                       # hand-off zone is full this step: try again next step
            self.free[spec.shape].append(b)
            self.spawn_accum += 1.0
            return None
        m.body_gravcomp[b] = 0.0
        d.qpos[qa:qa + 3] = pos
        d.qpos[qa + 3:qa + 7] = q
        d.qvel[va:va + 6] = [L.feed_vx + rng.normal(0, 0.15), rng.normal(0, 0.08), 0, *rng.normal(0, 3.0, 3)]
        d.qacc_warmstart[va:va + 6] = 0
        bean = Bean(self.uid, b, spec.name, spec.defect, d.time, axes.copy(), mass)
        self.uid += 1
        self.bean_of[b] = bean
        self.n_spawned += 1
        if self.continuous:
            self._spawned_events.append(bean)
        else:
            self.beans.append(bean)
        self.bean_by_uid[bean.uid] = bean
        self.active[self.body_index[b]] = True
        return bean

    def _free_spot(self, half, margin, tries=12):
        """Pick a landing spot in the hand-off zone that does not overlap a bean already there
        (a real vibratory feeder meters beans out one layer deep)."""
        L, rng, d = self.L, self.rng, self.data
        act = self.active
        if act.any():
            qa = self.qpos_adr[act]
            px, py = d.qpos[qa], d.qpos[qa + 1]
            near = px < L.feed_x[1] + 0.06
            px, py = px[near], py[near]
        else:
            px = py = np.zeros(0)
        clearance = half[0] + 0.006
        for _ in range(tries):
            x = rng.uniform(*L.feed_x)
            y = rng.uniform(-L.belt_w / 2 + margin, L.belt_w / 2 - margin)
            if len(px) == 0 or np.min((px - x) ** 2 + (py - y) ** 2) > clearance ** 2:
                return np.array([x, y, L.belt_z + L.feed_drop + half[2]])
        return None

    def _park(self, b: int):
        m, d = self.model, self.data
        i = self.body_index[b]
        self.active[i] = False
        m.body_gravcomp[b] = 1.0
        # Keep this flag enabled for reusable bodies, even if a reference refresh cleared it.
        m.flg_gravcomp = True
        qa, va = self.body_qpos[b], self.body_qvel[b]
        d.qpos[qa:qa + 3] = [6.0, -2 + (i % 400) * 0.01, 1 + (i // 400) * 0.05]
        d.qpos[qa + 3:qa + 7] = [1, 0, 0, 0]
        d.qvel[va:va + 6] = 0
        d.qacc_warmstart[va:va + 6] = 0
        d.xfrc_applied[b] = 0
        bean = self.bean_of.pop(b)
        if self.continuous:
            self._retired_events.append(bean)
            self.bean_by_uid.pop(bean.uid, None)
        for k, v in self.pools.items():
            if b in v:
                self.free[k].append(b)
                break

    # ------------------------------------------------------------------ actuation
    def fire(self, nozzle: int, t_on: float, duration: float, force: float = JET_FORCE, uid: int | None = None):
        """Open valve `nozzle` from t_on for `duration` seconds (the controller's only handle)."""
        if 0 <= nozzle < self.L.n_nozzles:
            fire = Fire(nozzle, t_on, t_on + duration, force, uid)
            self.fires.append(fire)
            self.n_fired += 1
            return fire
        return None

    # ------------------------------------------------------------------ stepping
    def step(self):
        m, d, L = self.model, self.data, self.L
        t = d.time
        # Poisson feed
        self.spawn_accum += self.rate * self.dt
        n = int(self.spawn_accum)
        if n:
            self.spawn_accum -= n
            for _ in range(n):
                self.spawn()

        act = self.active
        if act.any():
            bodies = self.all_bodies[act]
            qa = self.qpos_adr[act]
            pos = np.stack([d.qpos[qa], d.qpos[qa + 1], d.qpos[qa + 2]], 1)
            va = self.qvel_adr[act]
            vel = np.stack([d.qvel[va], d.qvel[va + 1], d.qvel[va + 2]], 1)
            mass = m.body_mass[bodies]
            f = np.zeros((len(bodies), 3))
            # air jets
            if self.fires:
                live = [fr for fr in self.fires if fr.t_off > t]
                self.fires = live
                inflight = pos[:, 0] > 0.02
                for fr in live:
                    if fr.t_on <= t:
                        if not fr.activated:
                            fr.activated = True
                            self.n_activated += 1
                            if fr.uid is not None:
                                if self.continuous:
                                    self._activated_events.append(fr.uid)
                                else:
                                    self.fired_targets.add(fr.uid)
                        hit = inflight & (np.abs(pos[:, 0] - L.ej_x) < JET_HALF_X) & \
                              (np.abs(pos[:, 1] - self.nozzle_y[fr.nozzle]) < JET_HALF_Y_FACTOR * L.nozzle_pitch) & \
                              (pos[:, 2] > L.belt_z - 0.07) & (pos[:, 2] < L.belt_z + 0.03)
                        f[hit, 2] -= fr.force
                        for b in bodies[hit]:
                            bean = self.bean_of[b]
                            bean.jet_hits += 1
                            if fr.uid is not None:
                                if self.continuous:
                                    if fr.records_first_hit(bean.uid):
                                        self._fire_hit_events.append((fr.uid, bean.uid))
                                else:
                                    self.fire_hits.add((fr.uid, bean.uid))
            d.xfrc_applied[bodies, :3] = f
        # conveyor: the belt body never moves in position but always carries belt_speed, so friction
        # transports whatever rests on it (standard MuJoCo conveyor idiom)
        d.qpos[self.belt_qpos] = 0.0
        d.qvel[self.belt_qvel] = L.belt_speed
        # spin the rollers (cosmetic)
        w = L.belt_speed / 0.03 * self.dt
        d.qpos[self.roller_head] += w
        d.qpos[self.roller_tail] += w
        mujoco.mj_step(m, d)

        if not act.any():
            return

        # implicitfast keeps these contacts and forces from the pre-integration state, at t.
        outcomes, grounded = {}, set()
        for index, contact in enumerate(d.contact[:d.ncon]):
            first, second = contact.geom1, contact.geom2
            if first in self.collection_geom:
                collector, collision = first, second
            elif second in self.collection_geom:
                collector, collision = second, first
            elif first == self.ground_geom:
                collector, collision = None, second
            elif second == self.ground_geom:
                collector, collision = None, first
            else:
                continue
            body = self.collision_body.get(collision)
            if body is None or body not in self.bean_of:
                continue
            force = np.zeros(6)
            mujoco.mj_contactForce(m, d, index, force)
            if force[0] <= 0:
                continue
            if collector is None:
                grounded.add(body)
            else:
                outcomes.setdefault(body, self.collection_geom[collector])

        for b, p in zip(bodies, pos):
            if b not in self.bean_of:
                continue
            escaped = p[0] > self.collection_end_x + m.geom_rbound[self.body_col[b]]
            side_spill = p[0] < 0 and abs(p[1]) > L.belt_w / 2 + 0.03
            outcome = outcomes.get(b)
            if outcome is None and (b in grounded or escaped or side_spill):
                outcome = "spilled"
            if outcome is None:
                continue
            bean = self.bean_of[b]
            bean.outcome = outcome
            bean.resolved_t = t
            bean.last_pos = tuple(np.round(p, 3))
            bean.last_quat = tuple(d.xquat[b])
            if self.continuous:
                self._outcome_events.append(bean)
            self._park(b)

    def drain_continuous_events(self):
        """Return new physical facts and clear their per-step queues."""
        events = (self._spawned_events, self._outcome_events, self._retired_events,
                  self._activated_events, self._fire_hit_events)
        self._spawned_events, self._outcome_events, self._retired_events = [], [], []
        self._activated_events, self._fire_hit_events = [], []
        return events

    # ------------------------------------------------------------------ ground truth helpers
    def active_state(self, rendered=False):
        """(bodies, positions, velocities) of active beans — used only for training labels / metrics.
        rendered=True returns the poses the renderer draws (kinematics of the last completed step)."""
        act = self.active
        bodies = self.all_bodies[act]
        qa, va = self.qpos_adr[act], self.qvel_adr[act]
        if rendered:
            pos = self.data.xpos[bodies].copy()
        else:
            pos = np.stack([self.data.qpos[qa], self.data.qpos[qa + 1], self.data.qpos[qa + 2]], 1)
        vel = np.stack([self.data.qvel[va], self.data.qvel[va + 1], self.data.qvel[va + 2]], 1)
        return bodies, pos, vel

    def n_active(self): return int(self.active.sum())
