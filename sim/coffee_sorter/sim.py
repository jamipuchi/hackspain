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
RESOLVED_RETIRE_GRACE = 0.5  # s: preserve chute motion, then recycle bodies settled in catch bins


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
        self.active = np.zeros(len(self.all_bodies), bool)
        self.retire_at = np.full(len(self.all_bodies), np.inf)
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
        gc = self.body_col[b]
        if spec.shape == ELLIPSOID:
            m.geom_size[g] = axes
            half = axes
            r, hl = axes[2], max(axes[0] - axes[2], 1e-4)     # collision capsule: same length & resting height
            m.geom_size[gc, 0], m.geom_size[gc, 1] = r, hl
            m.geom_rbound[gc] = hl + r
            m.geom_aabb[gc, 3:6] = [hl + r, r, r]
            inertia = mass / 5 * np.array([axes[1] ** 2 + axes[2] ** 2, axes[0] ** 2 + axes[2] ** 2, axes[0] ** 2 + axes[1] ** 2])
        elif spec.shape == BOX:
            m.geom_size[g] = axes
            half = axes
            inertia = mass / 3 * np.array([axes[1] ** 2 + axes[2] ** 2, axes[0] ** 2 + axes[2] ** 2, axes[0] ** 2 + axes[1] ** 2])
        elif spec.shape == CAPSULE:
            r, hl = axes[1], axes[0]
            m.geom_size[g, 0], m.geom_size[g, 1] = r, hl
            half = np.array([r, r, hl + r])
            inertia = np.array([mass * (r ** 2 / 4 + hl ** 2 / 3)] * 2 + [mass * r ** 2 / 2])
        else:  # HALF mesh: fixed scale variants, read the compiled size back
            half = m.geom_aabb[g, 3:6].copy()
            axes = half
            inertia = mass / 5 * np.array([axes[1] ** 2 + axes[2] ** 2, axes[0] ** 2 + axes[2] ** 2, axes[0] ** 2 + axes[1] ** 2])
        if spec.shape != HALF:
            m.geom_rbound[g] = np.linalg.norm(half)
            m.geom_aabb[g, 3:6] = half
        m.geom_rgba[g] = rgba
        m.geom_matid[g] = int(rng.choice(self.material_ids[spec.texture])) if spec.texture else -1
        m.body_mass[b] = mass
        m.body_inertia[b] = np.maximum(inertia, 1e-12)
        # keep the solver's precomputed inverse weights consistent with the new mass/inertia
        va0 = self.body_qvel[b]
        m.dof_invweight0[va0:va0 + 3] = 1.0 / mass
        m.dof_invweight0[va0 + 3:va0 + 6] = 1.0 / m.body_inertia[b].mean()
        m.body_invweight0[b] = [1.0 / mass, 1.0 / m.body_inertia[b].mean()]
        i_mean = m.body_inertia[b].mean()
        m.dof_damping[va0 + 3:va0 + 6] = i_mean / ROT_TAU
        m.dof_armature[va0 + 3:va0 + 6] = ROT_ARMATURE_FACTOR * i_mean
        m.body_gravcomp[b] = 0.0
        # pose: flat on the belt with random yaw, small random tilt; capsule lies along x by default (z-axis -> x)
        yaw = rng.uniform(-np.pi, np.pi)
        tilt = rng.normal(0, 0.15, 2)
        q = np.zeros(4)
        if spec.shape == CAPSULE:
            mujoco.mju_euler2Quat(q, np.array([tilt[0], np.pi / 2 + tilt[1], yaw]), "xyz")
        else:
            mujoco.mju_euler2Quat(q, np.array([tilt[0], tilt[1], yaw]), "xyz")
        if spec.shape == CAPSULE:
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
        d.qpos[qa:qa + 3] = pos
        d.qpos[qa + 3:qa + 7] = q
        d.qvel[va:va + 6] = [L.feed_vx + rng.normal(0, 0.15), rng.normal(0, 0.08), 0, *rng.normal(0, 3.0, 3)]
        bean = Bean(self.uid, b, spec.name, spec.defect, d.time, axes.copy(), mass)
        self.uid += 1
        self.bean_of[b] = bean
        self.n_spawned += 1
        if self.continuous:
            self._spawned_events.append(bean)
        else:
            self.beans.append(bean)
        self.bean_by_uid[bean.uid] = bean
        body_index = self.body_index[b]
        self.retire_at[body_index] = np.inf
        self.active[body_index] = True
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
        self.retire_at[i] = np.inf
        m.body_gravcomp[b] = 1.0
        qa, va = self.body_qpos[b], self.body_qvel[b]
        d.qpos[qa:qa + 3] = [6.0, -2 + (i % 400) * 0.01, 1 + (i // 400) * 0.05]
        d.qpos[qa + 3:qa + 7] = [1, 0, 0, 0]
        d.qvel[va:va + 6] = 0
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
            # outcome capture at the splitter plane and recycling
            past = pos[:, 0] >= L.split_x
            for b, p in zip(bodies[past], pos[past]):
                bean = self.bean_of[b]
                if bean.outcome is None:
                    bean.outcome = "accept" if p[2] > L.split_z else "reject"
                    bean.resolved_t = t
                    self.retire_at[self.body_index[b]] = t + RESOLVED_RETIRE_GRACE
                    if self.continuous:
                        self._outcome_events.append(bean)
            resolved_expired = self.retire_at[act] <= t
            gone = (pos[:, 0] > L.split_x + 0.16) | (pos[:, 2] < L.belt_z - 0.44) | \
                   ((pos[:, 0] < 0) & (np.abs(pos[:, 1]) > L.belt_w / 2 + 0.03)) | \
                   (pos[:, 2] < 0.05) | resolved_expired
            for b, p in zip(bodies[gone], pos[gone]):
                bean = self.bean_of[b]
                bean.last_pos = tuple(np.round(p, 3))
                if bean.outcome is None:
                    bean.outcome = "spilled"
                    bean.resolved_t = t
                    if self.continuous:
                        self._outcome_events.append(bean)
                self._park(b)
        # conveyor: the belt body never moves in position but always carries belt_speed, so friction
        # transports whatever rests on it (standard MuJoCo conveyor idiom)
        d.qpos[self.belt_qpos] = 0.0
        d.qvel[self.belt_qvel] = L.belt_speed
        # spin the rollers (cosmetic)
        w = L.belt_speed / 0.03 * self.dt
        d.qpos[self.roller_head] += w
        d.qpos[self.roller_tail] += w
        mujoco.mj_step(m, d)

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
