"""Experiment-local sensor and feed realism for the frozen green classifier."""
from __future__ import annotations

from dataclasses import dataclass
import math
import time

import cv2
import mujoco
import numpy as np

from sim import Bean, SorterSim
from profiles import CAPSULE, ELLIPSOID, HALF
from vision import Inspector


@dataclass(frozen=True)
class SensorConfig:
    brightness_gain: float = 1.0
    horizontal_gradient: float = 0.0
    exposure_us: float = 0.0
    shot_electrons_per_dn: float | None = None
    read_noise_dn: float = 0.0
    noise_label: str = "none"
    seed: int = 0

    def validate(self) -> None:
        for name, value in (("brightness_gain", self.brightness_gain),
                            ("horizontal_gradient", self.horizontal_gradient),
                            ("exposure_us", self.exposure_us),
                            ("read_noise_dn", self.read_noise_dn)):
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.shot_electrons_per_dn is not None and not math.isfinite(self.shot_electrons_per_dn):
            raise ValueError("shot_electrons_per_dn must be finite")
        if not isinstance(self.seed, (int, np.integer)):
            raise ValueError("seed must be an integer")
        if not 0.1 <= self.brightness_gain <= 2.0:
            raise ValueError("brightness_gain must be in [0.1, 2.0]")
        if not 0.0 <= self.horizontal_gradient <= 0.5:
            raise ValueError("horizontal_gradient must be in [0, 0.5]")
        if not 0.0 <= self.exposure_us <= 10_000.0:
            raise ValueError("exposure_us must be in [0, 10000]")
        if self.shot_electrons_per_dn is not None and self.shot_electrons_per_dn <= 0:
            raise ValueError("shot_electrons_per_dn must be positive")
        if self.read_noise_dn < 0:
            raise ValueError("read_noise_dn must be non-negative")
        if (self.shot_electrons_per_dn is not None or self.read_noise_dn) and self.noise_label == "none":
            raise ValueError("enabled noise needs a labeled assumption level")

    @property
    def blur_pixels(self) -> float:
        # Requested physical assumption: 3 m/s * exposure seconds * 4000 pixels/m.
        return 3.0 * self.exposure_us * 1e-6 * 4000.0

    @property
    def blur_kernel_rows(self) -> int:
        return len(motion_blur_kernel(self.blur_pixels))


@dataclass(frozen=True)
class PhysicalConfig:
    belt_jitter_fraction: float = 0.0
    belt_jitter_hz: float = 0.0
    belt_jitter_phase_rad: float = 0.0
    crowded_feed_half_width_m: float | None = None
    crowded_spawn_gap_m: float = 0.0002

    def validate(self) -> None:
        for name, value in (("belt_jitter_fraction", self.belt_jitter_fraction),
                            ("belt_jitter_hz", self.belt_jitter_hz),
                            ("belt_jitter_phase_rad", self.belt_jitter_phase_rad),
                            ("crowded_spawn_gap_m", self.crowded_spawn_gap_m)):
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.crowded_feed_half_width_m is not None and not math.isfinite(self.crowded_feed_half_width_m):
            raise ValueError("crowded_feed_half_width_m must be finite")
        if not 0.0 <= self.belt_jitter_fraction <= 0.2:
            raise ValueError("belt_jitter_fraction must be in [0, 0.2]")
        if self.belt_jitter_fraction and self.belt_jitter_hz <= 0:
            raise ValueError("belt_jitter_hz must be positive when jitter is enabled")
        if self.crowded_feed_half_width_m is not None and not 0.015 <= self.crowded_feed_half_width_m <= 0.24:
            raise ValueError("crowded_feed_half_width_m must be in [0.015, 0.24]")
        if self.crowded_spawn_gap_m < 0:
            raise ValueError("crowded_spawn_gap_m must be non-negative")


@dataclass
class FeedItem:
    planned_id: int
    cls: object
    axes: np.ndarray
    rgba: np.ndarray
    mass: float
    material: int
    yaw: float
    tilt: np.ndarray
    velocity: np.ndarray
    admitted_uid: int | None = None


def motion_blur_kernel(length_pixels: float) -> np.ndarray:
    """Centered fractional box exposure over pixel cells, normalized to unit energy."""
    if length_pixels <= 1.0:
        return np.ones(1, np.float32)
    half = length_pixels / 2.0
    cells = range(math.floor(-half - 0.5), math.ceil(half + 0.5) + 1)
    weights = np.array([max(0.0, min(cell + 0.5, half) - max(cell - 0.5, -half))
                        for cell in cells], dtype=np.float32)
    weights = weights[weights > 0]
    return weights / weights.sum()


def transform_frame(frame: np.ndarray, config: SensorConfig, rng: np.random.Generator) -> np.ndarray:
    """Apply assumed image formation. Runtime is intentionally outside controller timing."""
    config.validate()
    if frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError("frame must be an HxWx3 uint8 RGB image")
    identity = (config.brightness_gain == 1.0 and config.horizontal_gradient == 0.0 and
                config.blur_pixels <= 1.0 and config.shot_electrons_per_dn is None and
                config.read_noise_dn == 0.0)
    if identity:
        return frame.copy()

    image = frame.astype(np.float32)
    if config.blur_pixels > 1.0:
        kernel = motion_blur_kernel(config.blur_pixels)[:, None]
        image = cv2.filter2D(image, -1, kernel, borderType=cv2.BORDER_REPLICATE)
    if config.horizontal_gradient:
        gradient = np.linspace(1.0 - config.horizontal_gradient, 1.0 + config.horizontal_gradient,
                               frame.shape[1], dtype=np.float32)
        image *= gradient[None, :, None]
    image *= config.brightness_gain
    if config.shot_electrons_per_dn is not None or config.read_noise_dn:
        shot_variance = (np.maximum(image, 0.0) / config.shot_electrons_per_dn
                         if config.shot_electrons_per_dn is not None else 0.0)
        sigma = np.sqrt(shot_variance + config.read_noise_dn ** 2)
        image += rng.standard_normal(image.shape, dtype=np.float32) * sigma
    return np.clip(np.rint(image), 0, 255).astype(np.uint8)


class SensorInspector(Inspector):
    def __init__(self, sim, sensor_config: SensorConfig):
        sensor_config.validate()
        super().__init__(sim)
        self.sensor_config = sensor_config
        self.sensor_transform_ms: list[float] = []
        self._sensor_rng = np.random.default_rng(sensor_config.seed)

    def capture(self):
        frame, timestamp = super().capture()
        started = time.perf_counter()
        frame = transform_frame(frame, self.sensor_config, self._sensor_rng)
        self.sensor_transform_ms.append(1000.0 * (time.perf_counter() - started))
        # The symmetric exposure kernel is centered on the renderer's exposure timestamp.
        return frame, timestamp


class SensorSorterSim(SorterSim):
    """Physics variant; controller always sees the restored nominal Layout."""
    def __init__(self, *args, physical_config: PhysicalConfig, **kwargs):
        physical_config.validate()
        super().__init__(*args, **kwargs)
        self.physical_config = physical_config
        self.feed_seed = int(kwargs.get("seed", args[3] if len(args) > 3 else 0))
        self.placement_rng = self.rng
        self.pending_feed_item: FeedItem | None = None
        self.feed_items: list[FeedItem] = []
        self.admitted_feed_ids: dict[int, int] = {}
        self.desired_feed_events = 0
        self.admission_limited_events = 0
        self._spawn_blocked_this_step = False
        self.nominal_belt_speed = self.L.belt_speed
        self.actual_belt_speed_trace: list[float] = []
        self.crowded_spawn_failures = 0
        self.minimum_spawn_surface_gap_m: float | None = None
        self.crowded_contact_steps = 0
        self.crowded_contact_pairs: set[tuple[int, int]] = set()

    def _next_feed_item(self, spec=None) -> FeedItem:
        planned_id = len(self.feed_items)
        rng = np.random.default_rng(np.random.SeedSequence([self.feed_seed, planned_id]))
        spec = spec or self.P.classes[rng.choice(len(self.P.classes), p=self.priors)]
        axes, rgba, mass = self._sample_feed_instance(spec, rng)
        material = int(rng.choice(self.material_ids[spec.texture])) if spec.texture else -1
        yaw = float(rng.uniform(-np.pi, np.pi))
        tilt = rng.normal(0, 0.15, 2)
        velocity = np.array([self.L.feed_vx + rng.normal(0, 0.15), rng.normal(0, 0.08), 0,
                             *rng.normal(0, 3.0, 3)])
        item = FeedItem(planned_id, spec, axes, rgba, mass, material, yaw, tilt, velocity)
        self.feed_items.append(item)
        return item

    @staticmethod
    def _sample_feed_instance(spec, rng):
        from profiles import sample_instance
        return sample_instance(spec, rng)

    def spawn(self, spec=None):
        """Use an item-keyed product stream; placement retries never consume later products."""
        if self._spawn_blocked_this_step:
            self.admission_limited_events += 1
            return None
        item = self.pending_feed_item or self._next_feed_item(spec)
        if not self.free[item.cls.shape]:
            self.starved += 1
            self.pending_feed_item = item
            self._spawn_blocked_this_step = True
            return None
        m, d, L = self.model, self.data, self.L
        b = self.free[item.cls.shape].pop()
        g, gc = self.body_geom[b], self.body_col[b]
        axes, rgba, mass = item.axes, item.rgba, item.mass
        if item.cls.shape == ELLIPSOID:
            m.geom_size[g] = axes
            half = axes
            r, hl = axes[2], max(axes[0] - axes[2], 1e-4)
            m.geom_size[gc, 0], m.geom_size[gc, 1] = r, hl
            m.geom_rbound[gc] = hl + r
            m.geom_aabb[gc, 3:6] = [hl + r, r, r]
            inertia = mass / 5 * np.array([axes[1] ** 2 + axes[2] ** 2, axes[0] ** 2 + axes[2] ** 2,
                                           axes[0] ** 2 + axes[1] ** 2])
        elif item.cls.shape == CAPSULE:
            r, hl = axes[1], axes[0]
            m.geom_size[g, 0], m.geom_size[g, 1] = r, hl
            half = np.array([r, r, hl + r])
            inertia = np.array([mass * (r ** 2 / 4 + hl ** 2 / 3)] * 2 + [mass * r ** 2 / 2])
        elif item.cls.shape == HALF:
            half = m.geom_aabb[g, 3:6].copy()
            axes = half
            inertia = mass / 5 * np.array([axes[1] ** 2 + axes[2] ** 2, axes[0] ** 2 + axes[2] ** 2,
                                           axes[0] ** 2 + axes[1] ** 2])
        else:
            m.geom_size[g] = axes
            half = axes
            inertia = mass / 3 * np.array([axes[1] ** 2 + axes[2] ** 2, axes[0] ** 2 + axes[2] ** 2,
                                           axes[0] ** 2 + axes[1] ** 2])
        if item.cls.shape != HALF:
            m.geom_rbound[g] = np.linalg.norm(half)
            m.geom_aabb[g, 3:6] = half
        m.geom_rgba[g] = rgba
        m.geom_matid[g] = item.material
        m.body_mass[b] = mass
        m.body_inertia[b] = np.maximum(inertia, 1e-12)
        va0 = self.body_qvel[b]
        m.dof_invweight0[va0:va0 + 3] = 1.0 / mass
        m.dof_invweight0[va0 + 3:va0 + 6] = 1.0 / m.body_inertia[b].mean()
        m.body_invweight0[b] = [1.0 / mass, 1.0 / m.body_inertia[b].mean()]
        i_mean = m.body_inertia[b].mean()
        m.dof_damping[va0 + 3:va0 + 6] = i_mean / 1.5e-3
        m.dof_armature[va0 + 3:va0 + 6] = 2 * i_mean
        m.body_gravcomp[b] = 0.0
        q = np.zeros(4)
        if item.cls.shape == CAPSULE:
            mujoco.mju_euler2Quat(q, np.array([item.tilt[0], np.pi / 2 + item.tilt[1], item.yaw]), "xyz")
        else:
            mujoco.mju_euler2Quat(q, np.array([item.tilt[0], item.tilt[1], item.yaw]), "xyz")
        pos = self._free_spot(half, 0.012)
        if pos is None:
            self.free[item.cls.shape].append(b)
            self.pending_feed_item = item
            self._spawn_blocked_this_step = True
            return None
        qa, va = self.body_qpos[b], self.body_qvel[b]
        d.qpos[qa:qa + 3] = pos
        d.qpos[qa + 3:qa + 7] = q
        d.qvel[va:va + 6] = item.velocity
        bean = Bean(self.uid, b, item.cls.name, item.cls.defect, d.time, axes.copy(), mass)
        item.admitted_uid = bean.uid
        self.admitted_feed_ids[bean.uid] = item.planned_id
        self.uid += 1
        self.bean_of[b] = bean
        self.beans.append(bean)
        self.bean_by_uid[bean.uid] = bean
        self.active[self.body_index[b]] = True
        self.pending_feed_item = None
        return bean

    def step(self):
        cfg = self.physical_config
        self.desired_feed_events += int(self.spawn_accum + self.rate * self.dt)
        self._spawn_blocked_this_step = False
        if cfg.belt_jitter_fraction:
            fraction = cfg.belt_jitter_fraction * np.sin(
                2 * np.pi * cfg.belt_jitter_hz * self.data.time + cfg.belt_jitter_phase_rad)
            actual_speed = self.nominal_belt_speed * (1.0 + fraction)
        else:
            actual_speed = self.nominal_belt_speed
        self.actual_belt_speed_trace.append(float(actual_speed))
        object.__setattr__(self.L, "belt_speed", float(actual_speed))
        try:
            super().step()
            if cfg.crowded_feed_half_width_m is not None:
                touching_this_step = False
                for contact in self.data.contact[:self.data.ncon]:
                    if contact.dist > 0:
                        continue
                    bodies = tuple(sorted((int(self.model.geom_bodyid[contact.geom1]),
                                           int(self.model.geom_bodyid[contact.geom2]))))
                    if bodies[0] != bodies[1] and all(
                            body in self.body_index and self.active[self.body_index[body]] for body in bodies):
                        touching_this_step = True
                        self.crowded_contact_pairs.add(tuple(sorted(self.bean_of[body].uid for body in bodies)))
                self.crowded_contact_steps += int(touching_this_step)
        finally:
            object.__setattr__(self.L, "belt_speed", self.nominal_belt_speed)

    def _free_spot(self, half, margin, tries=12):
        cfg = self.physical_config
        if cfg.crowded_feed_half_width_m is None:
            return super()._free_spot(half, margin, tries)

        # A sphere enclosing each tilted/oriented footprint is conservative but cannot admit
        # a collision geometry that is already penetrating at spawn.
        incoming_radius = float(np.linalg.norm(half))
        lateral_limit = cfg.crowded_feed_half_width_m - incoming_radius
        if lateral_limit <= 0:
            raise ValueError("crowded feed is narrower than the incoming bean")
        active_bodies, positions, _ = self.active_state()
        near = positions[:, 0] < self.L.feed_x[1] + 0.06 if len(positions) else np.zeros(0, bool)
        near_bodies, near_positions = active_bodies[near], positions[near]
        other_radii = np.array([self._spawn_footprint_radius(self.bean_of[int(body)])
                                for body in near_bodies])
        for _ in range(max(tries, 80)):
            x = self.rng.uniform(*self.L.feed_x)
            y = self.rng.uniform(-lateral_limit, lateral_limit)
            if len(near_positions):
                centre_distance = np.hypot(near_positions[:, 0] - x, near_positions[:, 1] - y)
                surface_gap = centre_distance - incoming_radius - other_radii
                candidate_gap = float(surface_gap.min())
                if candidate_gap < cfg.crowded_spawn_gap_m:
                    continue
                self.minimum_spawn_surface_gap_m = (candidate_gap if self.minimum_spawn_surface_gap_m is None else
                                                    min(self.minimum_spawn_surface_gap_m, candidate_gap))
            return np.array([x, y, self.L.belt_z + self.L.feed_drop + half[2]])
        self.crowded_spawn_failures += 1
        return None

    def _spawn_footprint_radius(self, bean) -> float:
        axes = bean.axes
        if self.class_by_name[bean.cls].shape == "capsule":
            half = np.array([axes[1], axes[1], axes[0] + axes[1]])
        else:
            half = axes
        return float(np.linalg.norm(half))

    def feed_manifest(self) -> dict:
        items = [dict(planned_id=item.planned_id, cls=item.cls.name, defect=item.cls.defect,
                      axes_m=[float(x) for x in item.axes], material=item.material,
                      yaw_rad=item.yaw, tilt_rad=[float(x) for x in item.tilt],
                      velocity_m_s=[float(x) for x in item.velocity], admitted_uid=item.admitted_uid)
                 for item in self.feed_items]
        return dict(generator="per-planned-object RNG; placement retries preserve the pending item",
                    seed=self.feed_seed, desired_feed_events=self.desired_feed_events,
                    admission_limited_events=self.admission_limited_events, pending_planned_id=(
                        self.pending_feed_item.planned_id if self.pending_feed_item else None), items=items)

    def eligible_feed_records(self, start_s: float, end_s: float) -> list[dict]:
        records = []
        for bean in self.beans:
            if start_s <= bean.spawn_t <= end_s:
                item = self.feed_items[self.admitted_feed_ids[bean.uid]]
                records.append(dict(planned_id=item.planned_id, cls=item.cls.name,
                                    axes_m=[float(x) for x in item.axes], material=item.material,
                                    yaw_rad=item.yaw, tilt_rad=[float(x) for x in item.tilt],
                                    velocity_m_s=[float(x) for x in item.velocity]))
        return records


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def diagnostic_metrics(metrics: dict, controller, inspector: SensorInspector, sim: SensorSorterSim) -> dict:
    """Derived counts retain every denominator; no causal claim is inferred from outcomes."""
    blobs = metrics["camera_blobs"]
    single = int(blobs.get("single_observations", 0))
    merged = int(blobs.get("merged_observations", 0))
    tp = int(blobs.get("single_action_tp", 0) + blobs.get("merged_action_tp", 0))
    fn = int(blobs.get("single_action_fn", 0) + blobs.get("merged_action_fn", 0))
    fp = int(blobs.get("single_action_fp", 0) + blobs.get("merged_action_fp", 0))
    tn = int(blobs.get("single_action_tn", 0) + blobs.get("merged_action_tn", 0))
    eligible = int(metrics["denominators"]["eligible_beans"])
    cohorts = metrics["camera_bean_cohorts"]
    covered = int(cohorts["single_only"]["n"] + cohorts["ever_merged"]["n"])
    reject_decisions = int(metrics["reject_decisions"])
    scheduled = int(metrics["scheduled_reject_decisions"])
    activated = int(metrics["activated_reject_decisions"])
    associated = int(metrics["associated_reject_decisions"])
    own_hit = int(metrics["associated_jet_hit"])
    own_hit_rejected = int(metrics["associated_rejected"])
    compute = np.asarray(controller.compute_ms, dtype=float)
    transform = np.asarray(inspector.sensor_transform_ms, dtype=float)
    camera_interval_ms = 4.0
    speed = np.asarray(sim.actual_belt_speed_trace, dtype=float)
    return {
        "vision": {
            "single_top1_correct": int(blobs.get("single_class_correct", 0)),
            "single_top1_denominator": single,
            "single_top1_accuracy": _ratio(int(blobs.get("single_class_correct", 0)), single),
            "defect_action_true_positive": tp, "defect_action_false_negative": fn,
            "defect_action_recall_denominator": tp + fn,
            "defect_action_recall": _ratio(tp, tp + fn),
            "good_action_false_positive": fp, "good_action_true_negative": tn,
            "good_false_eject_denominator": fp + tn,
            "good_false_eject_rate": _ratio(fp, fp + tn),
            "note": "Observation-level diagnostics; merged truth means any constituent is rejectable.",
        },
        "camera_decision_coverage": {
            "eligible_beans": eligible, "beans_seen_in_full_blob": covered,
            "bean_coverage": _ratio(covered, eligible),
            "full_blob_observations": single + merged,
            "tracks_created": int(controller.next_tid), "finalized_decisions": len(controller.decisions),
            "track_decision_coverage": _ratio(len(controller.decisions), int(controller.next_tid)),
            "frames": int(controller.frames),
        },
        "late_miss_own_hit_funnel": {
            "reject_decisions": reject_decisions, "late_no_fire_decisions": int(metrics["late_decisions"]),
            "scheduled_reject_decisions": scheduled,
            "scheduled_not_activated": scheduled - activated,
            "activated_reject_decisions": activated,
            "reject_decisions_with_ground_truth_association": associated,
            "associated_activated_own_pulse_miss": int(metrics["associated_activated"] - own_hit),
            "own_pulse_hit_decisions": own_hit, "own_pulse_hit_and_rejected": own_hit_rejected,
            "note": "Diagnostic funnel only; final outcomes alone do not isolate vision or timing causality.",
        },
        "merged_cohorts": {
            "single_only_beans": int(cohorts["single_only"]["n"]),
            "ever_merged_beans": int(cohorts["ever_merged"]["n"]),
            "never_observed_beans": int(cohorts["never_observed"]["n"]),
            "eligible_beans": eligible,
            "ever_merged_occupancy": _ratio(int(cohorts["ever_merged"]["n"]), eligible),
            "merged_blob_observations": merged, "full_blob_observations": single + merged,
            "merged_observation_fraction": _ratio(merged, single + merged),
        },
        "runtime": {
            "camera_interval_ms": camera_interval_ms,
            "detector_controller_cpu_overruns": int((compute > camera_interval_ms).sum()),
            "detector_controller_frames": int(len(compute)),
            "detector_controller_overrun_fraction": _ratio(int((compute > camera_interval_ms).sum()), len(compute)),
            "sensor_transform_ms": ({"p50": float(np.percentile(transform, 50)),
                                     "p99": float(np.percentile(transform, 99)),
                                     "max": float(transform.max())} if len(transform) else None),
            "sensor_transform_excluded_from_detector_controller_cpu": True,
            "effective_camera_fps_sim_time": controller.frames / metrics["simulation_seconds"],
            "no_real_time_claim": True,
        },
        "physical": {
            "actual_belt_speed_m_per_s": ({"minimum": float(speed.min()), "mean": float(speed.mean()),
                                            "maximum": float(speed.max())} if len(speed) else None),
            "controller_nominal_belt_speed_m_per_s": sim.nominal_belt_speed,
            "crowded_spawn_failures": sim.crowded_spawn_failures,
            "minimum_admitted_spawn_surface_gap_m": sim.minimum_spawn_surface_gap_m,
            "desired_feed_events": getattr(sim, "desired_feed_events", 0),
            "admission_limited_events": getattr(sim, "admission_limited_events", 0),
            "pending_planned_feed_item": (getattr(sim, "pending_feed_item", None).planned_id
                                           if getattr(sim, "pending_feed_item", None) else None),
            "crowded_bean_contact_steps": getattr(sim, "crowded_contact_steps", 0),
            "crowded_bean_contact_pairs": len(getattr(sim, "crowded_contact_pairs", set())),
        },
    }
