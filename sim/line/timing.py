"""Bean kinematics on the chute and gate scheduling. Pure functions of `cfg.timing` / `cfg.camera` / `cfg.gate`.

Owner: coffee-sim agent. No hardware, no threads, no state.

Geometry (distances along the chute from its top, in cm; `BUILD_ASBUILT.md` is the source of the numbers):

    top ──── zone [zone_from_top_cm] ──── door [door_from_top_cm] ──── end
     0        9 ─────────── 15            30 ─────────── 36            40

Speed models (`cfg.timing.speed_model`):

* **drag** (default, the build agent's model): a bean released from rest obeys v' = a − k·v with
  a = (a15 + f)·sinθ/sin15° − f (a15 = `drag_accel_cm_s2_at_15deg` = 64, f = `drag_friction_cm_s2` = 19.2 ⇒ 48/64/80/96
  cm/s² at 12/15/18/21°) and k = `drag_k_per_s` = 1.6 /s (terminal 40 cm/s at 15°).
  The slope is a config value because the chute has 12/15/18/21° tilt blocks. Reference table (build agent):
  zone centre → door start ≈ 0.70 / 0.55 / 0.47 / 0.42 s for 12 / 15 / 18 / 21°.
* **measured**: constant `bean_speed_cm_s`, the value the panel's stopwatch writes. Use once the chute exists and
  has been timed; `calibrate_drag()` can instead fold a stopwatch reading into the drag model.
* **rolling**: rigid-body rolling with a rolling-resistance coefficient fitted to `bean_speed_cm_s` at the zone
  centre. Kept for comparison.

`gate_schedule` turns a verdict into (delay_s, dwell_s) for `GateDriver.pulse`. The door swing time is
`cfg.gate.settle_ms` (≈400 ms with the stock firmware ramp, ≈120 ms with the arduino agent's `R` override), so the
open command leads the bean by `settle_ms/1000 + lead_margin_s`; `refresh()` stores that as `cfg.timing.door_lead_s`.
"""
from __future__ import annotations

import math

G_CM_S2 = 981.0
ROLLING_INERTIA_FACTOR = 1.4  # 1 + I/(m r^2): solid sphere
REF_SLOPE_DEG = 15.0
SLOPES_DEG = (12.0, 15.0, 18.0, 21.0)  # the chute's tilt blocks


# ------------------------------------------------------------------ geometry helpers
def zone_centre_cm(cfg) -> float:
    a, b = cfg.timing.zone_from_top_cm
    return 0.5 * (a + b)


def zone_length_cm(cfg) -> float:
    a, b = cfg.timing.zone_from_top_cm
    return b - a


def door_start_cm(cfg) -> float:
    return cfg.timing.door_from_top_cm[0]


def door_length_cm(cfg) -> float:
    a, b = cfg.timing.door_from_top_cm
    return b - a


def _model(cfg, model):
    return (model or getattr(cfg.timing, "speed_model", "drag")).lower()


# ------------------------------------------------------------------ drag model  v' = a - k v
def drag_accel_cm_s2(cfg, slope_deg: float | None = None) -> float:
    """Net drive a(θ) = (a15 + f)·sinθ/sin15° − f: gravity along the slope minus a constant friction term.
    With a15 = 64, f = 19.2 this reproduces the build agent's 48 / 64 / 80 / 96 cm/s² at 12 / 15 / 18 / 21°."""
    slope = float(cfg.timing.slope_deg if slope_deg is None else slope_deg)
    a15 = float(cfg.timing.drag_accel_cm_s2_at_15deg)
    f = float(getattr(cfg.timing, "drag_friction_cm_s2", 0.0))
    return max((a15 + f) * math.sin(math.radians(slope)) / math.sin(math.radians(REF_SLOPE_DEG)) - f, 0.0)


def drag_terminal_cm_s(cfg, slope_deg: float | None = None) -> float:
    return drag_accel_cm_s2(cfg, slope_deg) / float(cfg.timing.drag_k_per_s)


def drag_speed_at_t(t: float, cfg, slope_deg: float | None = None) -> float:
    k = float(cfg.timing.drag_k_per_s)
    return drag_terminal_cm_s(cfg, slope_deg) * (1.0 - math.exp(-k * max(t, 0.0)))


def drag_distance_at_t(t: float, cfg, slope_deg: float | None = None) -> float:
    k = float(cfg.timing.drag_k_per_s)
    t = max(t, 0.0)
    return drag_terminal_cm_s(cfg, slope_deg) * (t - (1.0 - math.exp(-k * t)) / k)


def drag_time_at_s(s_cm: float, cfg, slope_deg: float | None = None) -> float:
    """Time from release (rest at the top) to reach s_cm. Bisection on the monotonic s(t)."""
    if s_cm <= 0:
        return 0.0
    vt = drag_terminal_cm_s(cfg, slope_deg)
    if vt <= 0:
        return math.inf
    lo, hi = 0.0, s_cm / vt + 1.0 / float(cfg.timing.drag_k_per_s) + 1.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if drag_distance_at_t(mid, cfg, slope_deg) < s_cm:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def calibrate_drag(cfg, zone_to_door_measured_s: float) -> float:
    """Scale `drag_accel_cm_s2_at_15deg` so the drag model reproduces a stopwatch zone-centre→door-start time at the
    current slope (keeps the build agent's shape, takes the magnitude from the bench). Returns the new a15."""
    target = float(zone_to_door_measured_s)
    lo, hi = 5.0, 400.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        cfg.timing.drag_accel_cm_s2_at_15deg = mid
        if zone_to_door_s(cfg, "drag") > target:   # too slow -> more drive
            lo = mid
        else:
            hi = mid
    cfg.timing.drag_accel_cm_s2_at_15deg = round(0.5 * (lo + hi), 3)
    return cfg.timing.drag_accel_cm_s2_at_15deg


# ------------------------------------------------------------------ rolling model (comparison)
def acceleration_cm_s2(slope_deg: float, mu_roll: float) -> float:
    th = math.radians(slope_deg)
    return G_CM_S2 * (math.sin(th) - mu_roll * math.cos(th)) / ROLLING_INERTIA_FACTOR


def speed_from_rest(s_cm: float, slope_deg: float, mu_roll: float) -> float:
    a = acceleration_cm_s2(slope_deg, mu_roll)
    return math.sqrt(max(2.0 * a * max(s_cm, 0.0), 0.0))


def fit_rolling_friction(cfg) -> float:
    """mu_roll such that rigid rolling matches `bean_speed_cm_s` at the zone centre."""
    v = float(cfg.timing.bean_speed_cm_s)
    s = zone_centre_cm(cfg)
    th = math.radians(cfg.timing.slope_deg)
    if s <= 0 or v <= 0:
        return 0.0
    a_needed = v * v / (2.0 * s)
    mu = (math.sin(th) - a_needed * ROLLING_INERTIA_FACTOR / G_CM_S2) / math.cos(th)
    return max(0.0, min(mu, math.tan(th) * 0.999))


# ------------------------------------------------------------------ model-agnostic API
def speed_at_cm(s_cm: float, cfg, model: str | None = None) -> float:
    m = _model(cfg, model)
    if m == "drag":
        return drag_speed_at_t(drag_time_at_s(s_cm, cfg), cfg)
    if m == "rolling":
        return speed_from_rest(s_cm, cfg.timing.slope_deg, fit_rolling_friction(cfg))
    return float(cfg.timing.bean_speed_cm_s)


def travel_time_s(s0_cm: float, s1_cm: float, cfg, model: str | None = None) -> float:
    """Time for a bean to go from s0 to s1 along the chute (0 if s1 <= s0)."""
    if s1_cm <= s0_cm:
        return 0.0
    m = _model(cfg, model)
    if m == "drag":
        return drag_time_at_s(s1_cm, cfg) - drag_time_at_s(s0_cm, cfg)
    if m == "rolling":
        mu = fit_rolling_friction(cfg)
        a = acceleration_cm_s2(cfg.timing.slope_deg, mu)
        if a <= 1e-9:
            return math.inf
        return (speed_from_rest(s1_cm, cfg.timing.slope_deg, mu) - speed_from_rest(s0_cm, cfg.timing.slope_deg, mu)) / a
    return (s1_cm - s0_cm) / float(cfg.timing.bean_speed_cm_s)


def zone_to_door_s(cfg, model: str | None = None) -> float:
    """Zone centre -> door start. Drag model at 15°: ≈0.57 s; measured model: (30 − 12)/35 = 0.514 s."""
    return travel_time_s(zone_centre_cm(cfg), door_start_cm(cfg), cfg, model)


def refresh(cfg, model: str | None = None) -> float:
    """Recompute and store the derived config values: cfg.timing.zone_to_door_s (active speed model) and
    cfg.timing.door_lead_s (gate.settle_ms/1000 + lead_margin_s). Returns zone_to_door_s."""
    cfg.timing.zone_to_door_s = round(zone_to_door_s(cfg, model), 4)
    cfg.timing.door_lead_s = round(door_lead_s(cfg), 4)
    return cfg.timing.zone_to_door_s


# ------------------------------------------------------------------ where in the zone was the bean?
def roi_fraction(blob_pos_px: float, cfg) -> float:
    """0 at the upstream edge of the ROI, 1 at the downstream edge, along cfg.camera.flow_axis ('+x','-x','+y','-y','x','y')."""
    x0, y0, x1, y1 = cfg.camera.zone
    axis = str(cfg.camera.flow_axis).lower()
    lo, hi = (y0, y1) if "y" in axis else (x0, x1)
    f = (float(blob_pos_px) - lo) / max(hi - lo, 1)
    if axis.startswith("-"):
        f = 1.0 - f
    return min(max(f, 0.0), 1.0)


def bean_position_cm(blob_pos_px: float, cfg) -> float:
    """Chute coordinate (cm from the top) of a blob seen at `blob_pos_px` along the flow axis."""
    a, _ = cfg.timing.zone_from_top_cm
    return a + roi_fraction(blob_pos_px, cfg) * zone_length_cm(cfg)


# ------------------------------------------------------------------ the schedule
def door_lead_s(cfg) -> float:
    """How early the OPEN command must go out: the swing time (gate.settle_ms) plus `lead_margin_s`.
    (cfg.timing.door_lead_s is the stored copy of this, written by refresh(); never read it here.)"""
    return float(cfg.gate.settle_ms) / 1000.0 + float(getattr(cfg.timing, "lead_margin_s", 0.0))


def gate_schedule(verdict_t: float, blob_v_px: float, cfg, now_t: float | None = None,
                  model: str | None = None) -> tuple[float, float]:
    """(delay_s, dwell_s) for GateDriver.pulse so the door is open while the bean passes it.

    verdict_t : monotonic time of the verdict (≈ the frame time; the frame age is part of door_lead_s margin).
    blob_v_px : the blob's coordinate along the flow axis (u for 'x' flow, v for 'y' flow), full-frame pixels.
    now_t     : current monotonic time; defaults to verdict_t.

    delay = travel time from where the bean was seen to the door start − door_lead_s(cfg) − time already elapsed,
    never negative. dwell = lead + time for the bean to clear the 6 cm door + margin, at least gate.default_dwell_s.
    """
    now_t = verdict_t if now_t is None else now_t
    s_bean = bean_position_cm(blob_v_px, cfg)
    t_to_door = travel_time_s(s_bean, door_start_cm(cfg), cfg, model)
    lead = door_lead_s(cfg)
    delay = max(0.0, verdict_t + t_to_door - lead - now_t)
    v_door = max(speed_at_cm(door_start_cm(cfg), cfg, model), 1e-6)
    dwell = lead + door_length_cm(cfg) / v_door + float(getattr(cfg.timing, "lead_margin_s", 0.0))
    return delay, max(dwell, float(cfg.gate.default_dwell_s))


def budget_s(cfg, model: str | None = None) -> float:
    """Time left for photo→verdict when the bean is seen at the zone centre (negative = not workable)."""
    return zone_to_door_s(cfg, model) - door_lead_s(cfg)


def table(cfg) -> list[dict]:
    """The reference table for every tilt block, with the drag model (what the panel shows)."""
    rows = []
    for sl in SLOPES_DEG:
        t_zone = drag_time_at_s(zone_centre_cm(cfg), cfg, sl)
        t_door = drag_time_at_s(door_start_cm(cfg), cfg, sl)
        rows.append(dict(slope_deg=sl, a_cm_s2=round(drag_accel_cm_s2(cfg, sl), 1), top_to_zone_s=round(t_zone, 3),
                         zone_to_door_s=round(t_door - t_zone, 3), speed_at_zone_cm_s=round(drag_speed_at_t(t_zone, cfg, sl), 1),
                         speed_at_door_cm_s=round(drag_speed_at_t(t_door, cfg, sl), 1),
                         budget_s=round(t_door - t_zone - door_lead_s(cfg), 3)))
    return rows


def summary(cfg) -> dict:
    """Numbers the panel can show next to the stopwatch tool."""
    return dict(
        speed_model=_model(cfg, None), slope_deg=float(cfg.timing.slope_deg),
        zone_centre_cm=zone_centre_cm(cfg), door_start_cm=door_start_cm(cfg),
        zone_to_door_s=zone_to_door_s(cfg), zone_to_door_measured_s=zone_to_door_s(cfg, "measured"),
        zone_to_door_drag_s=zone_to_door_s(cfg, "drag"),
        speed_at_zone_cm_s=speed_at_cm(zone_centre_cm(cfg), cfg), speed_at_door_cm_s=speed_at_cm(door_start_cm(cfg), cfg),
        door_lead_s=door_lead_s(cfg), budget_s=budget_s(cfg),
        door_open_window_s=gate_schedule(0.0, cfg.camera.zone[0], cfg)[1],
        mu_roll_fitted=fit_rolling_friction(cfg),
    )
