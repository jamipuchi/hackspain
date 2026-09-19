"""Real-time sorting controller: tracks blobs across frames, fuses per-frame class probabilities,
decides, and schedules the air-jet valves with time-of-flight prediction.

Only inputs: camera frames. Only output: `sim.fire(nozzle, t_on, duration, force)`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import time
import numpy as np

from vision import Inspector, Blobs
from classifier import Model
from sim import JET_FORCE


@dataclass
class Policy:
    name: str = "specialty"
    reject_severities: tuple = ("minor", "major", "foreign")   # 'commercial' keeps minor defects
    threshold: float = 0.5           # P(reject) needed to fire
    anomaly: bool = True             # fire on never-seen-before objects
    base_pulse: float = 0.003        # s of air for a 0.2 g bean
    ref_mass: float = 0.0002
    max_pulse: float = 0.012
    lead: float = 0.0015             # open the valve this early (jet rise time)
    latency_floor: float = 0.004     # s: camera exposure + transfer, even if compute is instant
    induced_delay: float = 0.0       # s: controlled extra availability delay for deadline experiments
    fixed_latency: float | None = None  # s: imposed total exposure-to-availability latency


COMMERCIAL = Policy("commercial", ("major", "foreign"))
SPECIALTY = Policy("specialty")


@dataclass
class Track:
    tid: int
    y: float
    x: float
    t: float
    obs_t: list = field(default_factory=list)
    obs_x: list = field(default_factory=list)
    prob_sum: np.ndarray | None = None
    n: int = 0
    anomaly: float = 0.0
    area: float = 0.0
    misses: int = 0
    done: bool = False


@dataclass
class Decision:
    tid: int
    t_decided: float
    t_available: float
    x: float
    y: float
    v: float
    probs: np.ndarray
    anomaly: float
    reject: bool
    nozzles: list
    t_fire: float
    pulse: float
    late: bool
    n_obs: int
    cls: str
    scheduled: bool
    target_uids: tuple = ()


class Controller:
    def __init__(self, sim, inspector: Inspector, model: Model, policy: Policy = SPECIALTY,
                 jet_force: float = JET_FORCE):
        self.sim, self.insp, self.model, self.pol = sim, inspector, model, policy
        self.L = sim.L
        self.classes = model.classes
        self.jet_force = jet_force
        spec = {c.name: c for c in sim.P.classes}
        self.reject_mask = np.array([spec[c].defect and spec[c].severity in policy.reject_severities for c in self.classes])
        from profiles import sample_instance
        rng = np.random.default_rng(0)
        self.class_mass = np.array([np.mean([sample_instance(spec[c], rng)[2] for _ in range(64)]) for c in self.classes])
        self.tracks: list[Track] = []
        self.decisions: list[Decision] = []
        self.next_tid = 0
        self.latency_ms: list[float] = []
        self.compute_ms: list[float] = []
        self.frames = 0

    # -------------------------------------------------------------- per frame
    def on_frame(self, frame, t):
        frame_started_wall = time.perf_counter()
        blobs = self.insp.detect(frame, t)
        full = ~blobs.partial
        P, A = self.model.predict(blobs.X[full])
        blob_tracks = self._associate(blobs, full, P, A, t)
        self.frames += 1
        self._finalize(t, frame_started_wall)
        compute = time.perf_counter() - frame_started_wall
        measured_latency = self.pol.latency_floor + compute
        latency = (max(self.pol.fixed_latency, measured_latency) if self.pol.fixed_latency is not None else
                   measured_latency + self.pol.induced_delay)
        self.compute_ms.append(compute * 1e3)
        self.latency_ms.append(latency * 1e3)
        return blobs, P, A, full, blob_tracks

    def _associate(self, blobs: Blobs, full, P, A, t):
        v_belt = self.L.belt_speed
        live = [tr for tr in self.tracks if not tr.done or tr.misses < 2]
        if live:
            px = np.array([tr.x + v_belt * (t - tr.t) for tr in live])
            py = np.array([tr.y for tr in live])
        used = np.zeros(len(live), bool)
        idx_full = np.where(full)[0]
        blob_tracks = np.full(blobs.n, -1, int)
        for k, i in enumerate(idx_full):
            bx, by = blobs.x[i], blobs.y[i]
            tr = None
            if live:
                d2 = ((px - bx) / 0.006) ** 2 + ((py - by) / 0.003) ** 2
                d2[used] = np.inf
                j = int(np.argmin(d2))
                if d2[j] < 1.0:
                    tr = live[j]; used[j] = True
            if tr is None:
                tr = Track(self.next_tid, by, bx, t); self.next_tid += 1
                tr.prob_sum = np.zeros(len(self.classes))
                self.tracks.append(tr)
            tr.x, tr.y, tr.t = bx, 0.7 * tr.y + 0.3 * by if tr.n else by, t
            blob_tracks[i] = tr.tid
            tr.misses = 0
            if tr.done:
                continue
            tr.obs_t.append(t); tr.obs_x.append(bx)
            tr.prob_sum += P[k]; tr.n += 1
            tr.anomaly = max(tr.anomaly, A[k]); tr.area = blobs.X[i, 0]
        for j, tr in enumerate(live):
            if not used[j]:
                tr.misses += 1
        return blob_tracks

    def _finalize(self, t, frame_started_wall):
        L, pol = self.L, self.pol
        for tr in self.tracks:
            if tr.done or tr.n == 0:
                continue
            x_pred = tr.x + L.belt_speed * (t - tr.t)
            # Decide by the camera centre so measured compute spikes still fit the 73 ms jet budget.
            if x_pred < L.cam_x and tr.misses < 2:
                continue
            tr.done = True
            probs = tr.prob_sum / tr.n
            if tr.n >= 2 and (tr.obs_t[-1] - tr.obs_t[0]) > 1e-4:
                v = float(np.polyfit(tr.obs_t, tr.obs_x, 1)[0])
                v = float(np.clip(v, 0.6 * L.belt_speed, 1.15 * L.belt_speed))
            else:
                v = L.belt_speed
            p_reject = float(probs[self.reject_mask].sum())
            anomalous = pol.anomaly and tr.anomaly > self.model.anomaly_thresh
            reject = p_reject >= pol.threshold or anomalous
            cls = self.classes[int(np.argmax(probs))]
            t_leave = tr.t + (0.0 - tr.x) / v
            t_fire = t_leave + L.ej_x / v
            nozzles, pulse = [], 0.0
            late = False
            measured_to_schedule = time.perf_counter() - frame_started_wall
            measured_latency = pol.latency_floor + measured_to_schedule
            available_latency = (max(pol.fixed_latency, measured_latency) if pol.fixed_latency is not None else
                                  measured_latency + pol.induced_delay)
            t_available = t + available_latency
            scheduled = False
            if reject:
                mass = float(probs @ self.class_mass)
                if anomalous and p_reject < pol.threshold:
                    mass = max(mass, 0.0006)                      # unknown object: assume heavy
                pulse = float(np.clip(pol.base_pulse * mass / pol.ref_mass, pol.base_pulse * 0.8, pol.max_pulse))
                j = int((tr.y + L.belt_w / 2) // L.nozzle_pitch)
                nozzles = [j]
                off = tr.y - L.nozzle_y(j)
                if abs(off) > 0.3 * L.nozzle_pitch or mass > 0.0005:
                    nozzles.append(j + (1 if off > 0 else -1))
                if mass > 0.0005:
                    nozzles.append(j - (1 if off > 0 else -1))
                t_on = t_fire - pol.lead
                if t_available > t_fire + 0.002:
                    late = True                                   # bean already past the jets
                else:
                    t_on = max(t_on, t_available)
                    for nz in nozzles:
                        scheduled |= self.sim.fire(nz, t_on, pulse + 0.001,
                                                   self.jet_force, uid=tr.tid) is not None
            self.decisions.append(Decision(tr.tid, t, t_available, tr.x, tr.y, v, probs, tr.anomaly,
                                           reject, nozzles, t_fire, pulse, late, tr.n, cls, scheduled))
        # prune
        if len(self.tracks) > 4000:
            self.tracks = [tr for tr in self.tracks if not tr.done or tr.misses < 2]
