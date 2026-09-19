"""Bean kinematics on the chute and gate scheduling. Pure functions of `cfg.timing` / `cfg.camera` / `cfg.gate`.

Owner: coffee-sim agent. No hardware, no threads, no state.

Geometry (all distances measured along the chute from its top, in cm):

    top ──── zone [zone_from_top_cm] ──── door [door_from_top_cm] ──── end
     0        9 ─────────── 15            30 ─────────── 36            40

Two speed models are offered:

* **measured** (default): the bean moves at `cfg.timing.bean_speed_cm_s`, the value the build agent timed with a
  stopwatch between the zone and the door. Simple, robust, and what the panel updates.
* **kinematic**: a body released from rest on a slope of `slope_deg` with rolling resistance. The resistance
  coefficient is fitted so the kinematic model reproduces the measured speed at the zone centre, so the model
  only adds *how the speed grows* between zone and door (a bean is faster at the door than in the zone).

`gate_schedule` turns a verdict into (delay_s, dwell_s) for `GateDriver.pulse`, accounting for where the bean
was inside the ROI along `cfg.camera.flow_axis` when the frame was taken.
"""
from __future__ import annotations

import math

G_CM_S2 = 981.0
ROLLING_INERTIA_FACTOR = 1.4  # 1 + I/(m r^2): 1.4 for a solid sphere; beans tumble rather than roll cleanly


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


# ------------------------------------------------------------------ kinematic model
def acceleration_cm_s2(slope_deg: float, mu_roll: float) -> float:
    """Along-slope acceleration of a rolling body with rolling-resistance coefficient mu_roll (can be <= 0 => stalls)."""
    th = math.radians(slope_deg)
    return G_CM_S2 * (math.sin(th) - mu_roll * math.cos(th)) / ROLLING_INERTIA_FACTOR


def speed_from_rest(s_cm: float, slope_deg: float, mu_roll: float) -> float:
    """Speed (cm/s) after travelling s_cm from rest at the top of the chute."""
    a = acceleration_cm_s2(slope_deg, mu_roll)
    return math.sqrt(max(2.0 * a * max(s_cm, 0.0), 0.0))


def fit_rolling_friction(cfg) -> float:
    """mu_roll such that the kinematic model matches `bean_speed_cm_s` at the zone centre.

    Frictionless, a bean would reach ~66 cm/s at 12 cm on a 15° slope; the bench measures ~35 cm/s, so most of
    the potential energy goes into tumbling and bouncing on the foam board. We fold all of it into mu_roll."""
    v = float(cfg.timing.bean_speed_cm_s)
    s = zone_centre_cm(cfg)
    th = math.radians(cfg.timing.slope_deg)
    if s <= 0 or v <= 0:
        return 0.0
    a_needed = v * v / (2.0 * s)                       # v^2 = 2 a s
    mu = (math.sin(th) - a_needed * ROLLING_INERTIA_FACTOR / G_CM_S2) / math.cos(th)
    return max(0.0, min(mu, math.tan(th) * 0.999))     # never so much friction that the bean stalls


def speed_at_cm(s_cm: float, cfg, model: str = "measured") -> float:
    if model == "kinematic":
        return speed_from_rest(s_cm, cfg.timing.slope_deg, fit_rolling_friction(cfg))
    return float(cfg.timing.bean_speed_cm_s)


def travel_time_s(s0_cm: float, s1_cm: float, cfg, model: str = "measured") -> float:
    """Time for a bean to go from s0 to s1 along the chute (s1 >= s0)."""
    if s1_cm <= s0_cm:
        return 0.0
    if model == "kinematic":
        a = acceleration_cm_s2(cfg.timing.slope_deg, fit_rolling_friction(cfg))
        if a <= 1e-9:
            return math.inf
        v0 = speed_from_rest(s0_cm, cfg.timing.slope_deg, fit_rolling_friction(cfg))
        v1 = speed_from_rest(s1_cm, cfg.timing.slope_deg, fit_rolling_friction(cfg))
        return (v1 - v0) / a                             # v = v0 + a t
    return (s1_cm - s0_cm) / float(cfg.timing.bean_speed_cm_s)


def zone_to_door_s(cfg, model: str = "measured") -> float:
    """Zone centre -> door start. Default numbers: (30 - 12) / 35 = 0.514 s."""
    return travel_time_s(zone_centre_cm(cfg), door_start_cm(cfg), cfg, model)


def refresh(cfg, model: str = "measured") -> float:
    """Recompute and store cfg.timing.zone_to_door_s from the current speed/geometry. Returns the value."""
    cfg.timing.zone_to_door_s = round(zone_to_door_s(cfg, model), 4)
    return cfg.timing.zone_to_door_s


# ------------------------------------------------------------------ where in the zone was the bean?
def roi_fraction(blob_pos_px: float, cfg) -> float:
    """0 at the upstream edge of the ROI, 1 at the downstream edge, along cfg.camera.flow_axis ('+x','-x','+y','-y' or 'x','y')."""
    x0, y0, x1, y1 = cfg.camera.zone
    axis = str(cfg.camera.flow_axis).lower()
    if "y" in axis:
        lo, hi = y0, y1
    else:
        lo, hi = x0, x1
    span = max(hi - lo, 1)
    f = (float(blob_pos_px) - lo) / span
    if axis.startswith("-"):
        f = 1.0 - f
    return min(max(f, 0.0), 1.0)


def bean_position_cm(blob_pos_px: float, cfg) -> float:
    """Chute coordinate (cm from the top) of a blob seen at `blob_pos_px` along the flow axis."""
    a, _ = cfg.timing.zone_from_top_cm
    return a + roi_fraction(blob_pos_px, cfg) * zone_length_cm(cfg)


# ------------------------------------------------------------------ the schedule
def gate_schedule(verdict_t: float, blob_v_px: float, cfg, now_t: float | None = None,
                  model: str = "measured") -> tuple[float, float]:
    """(delay_s, dwell_s) for GateDriver.pulse so the door is open while the bean passes it.

    verdict_t : monotonic time the verdict was produced (the frame was taken slightly before; the inference
                time is covered by `door_lead_s`).
    blob_v_px : the blob's coordinate along the flow axis (u for 'x' flow, v for 'y' flow), full-frame pixels.
    now_t     : current monotonic time; defaults to verdict_t (delay measured from the verdict).

    delay = time for the bean to reach the door start from where it was seen, minus door_lead_s, minus the time
    already elapsed since the verdict. Never negative. dwell covers lead + door length + servo settle, and never
    less than gate.default_dwell_s.
    """
    now_t = verdict_t if now_t is None else now_t
    s_bean = bean_position_cm(blob_v_px, cfg)
    t_to_door = travel_time_s(s_bean, door_start_cm(cfg), cfg, model)
    t_open = verdict_t + t_to_door - float(cfg.timing.door_lead_s)
    delay = max(0.0, t_open - now_t)
    v_door = speed_at_cm(door_start_cm(cfg), cfg, model)
    dwell = float(cfg.timing.door_lead_s) + door_length_cm(cfg) / max(v_door, 1e-6) + 2 * cfg.gate.settle_ms / 1000.0
    dwell = max(dwell, float(cfg.gate.default_dwell_s))
    return delay, dwell


def summary(cfg) -> dict:
    """Numbers the panel can show next to the stopwatch tool."""
    mu = fit_rolling_friction(cfg)
    return dict(
        zone_centre_cm=zone_centre_cm(cfg), door_start_cm=door_start_cm(cfg),
        bean_speed_cm_s=cfg.timing.bean_speed_cm_s, zone_to_door_s=zone_to_door_s(cfg),
        zone_to_door_kinematic_s=zone_to_door_s(cfg, "kinematic"), mu_roll_fitted=mu,
        speed_at_door_kinematic_cm_s=speed_at_cm(door_start_cm(cfg), cfg, "kinematic"),
        top_to_zone_s=travel_time_s(0, zone_centre_cm(cfg), cfg, "kinematic"),
        door_open_window_s=gate_schedule(0.0, cfg.camera.zone[0], cfg)[1],
    )
