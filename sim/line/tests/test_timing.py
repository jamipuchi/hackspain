"""timing.py against the build agent's design numbers: 15°, zone 9–15 cm, door 30–36 cm, ~35 cm/s."""
import math

import pytest

from line import timing
from line.config import LineConfig


@pytest.fixture
def cfg():
    return LineConfig()  # defaults = the design sheet


def test_zone_to_door_measured(cfg):
    # (30 - 12) cm / 35 cm/s
    assert timing.zone_to_door_s(cfg) == pytest.approx(18 / 35, abs=1e-9)
    assert 0.45 < timing.zone_to_door_s(cfg) < 0.6


def test_refresh_writes_config(cfg):
    cfg.timing.zone_to_door_s = 0.0
    v = timing.refresh(cfg)
    assert v == cfg.timing.zone_to_door_s == pytest.approx(0.5143, abs=1e-3)


def test_kinematic_fit_reproduces_measured_speed(cfg):
    mu = timing.fit_rolling_friction(cfg)
    assert 0 < mu < math.tan(math.radians(cfg.timing.slope_deg))
    v_zone = timing.speed_at_cm(timing.zone_centre_cm(cfg), cfg, "kinematic")
    assert v_zone == pytest.approx(cfg.timing.bean_speed_cm_s, rel=1e-6)
    # a bean keeps accelerating: faster at the door than in the zone, so the kinematic transit is shorter
    assert timing.speed_at_cm(30, cfg, "kinematic") > v_zone
    assert timing.zone_to_door_s(cfg, "kinematic") < timing.zone_to_door_s(cfg)
    assert 0.3 < timing.zone_to_door_s(cfg, "kinematic") < 0.55


def test_frictionless_sanity():
    # 15° slope, 12 cm from rest, solid-sphere rolling: v = sqrt(2 g sin(15°) s / 1.4) ≈ 66 cm/s
    v = timing.speed_from_rest(12, 15, 0.0)
    assert v == pytest.approx(math.sqrt(2 * 981 * math.sin(math.radians(15)) * 12 / 1.4), rel=1e-9)
    assert 60 < v < 70


def test_travel_time_is_monotonic_and_zero_backwards(cfg):
    assert timing.travel_time_s(20, 10, cfg) == 0.0
    t1 = timing.travel_time_s(12, 20, cfg, "kinematic")
    t2 = timing.travel_time_s(12, 30, cfg, "kinematic")
    assert 0 < t1 < t2


@pytest.mark.parametrize("axis,px,expected", [
    ("x", 400, 0.0), ("x", 880, 1.0), ("x", 640, 0.5), ("+x", 520, 0.25),
    ("-x", 400, 1.0), ("-x", 880, 0.0),
    ("y", 200, 0.0), ("y", 520, 1.0), ("-y", 200, 1.0),
    ("x", -50, 0.0), ("x", 5000, 1.0),  # clamped
])
def test_roi_fraction(cfg, axis, px, expected):
    cfg.camera.flow_axis = axis
    assert timing.roi_fraction(px, cfg) == pytest.approx(expected)


def test_bean_position_maps_roi_to_chute(cfg):
    assert timing.bean_position_cm(400, cfg) == pytest.approx(9.0)
    assert timing.bean_position_cm(880, cfg) == pytest.approx(15.0)
    assert timing.bean_position_cm(640, cfg) == pytest.approx(12.0)


def test_gate_schedule_centre_of_zone(cfg):
    delay, dwell = timing.gate_schedule(100.0, 640, cfg)
    # zone centre -> door start 0.514 s, open 0.10 s early
    assert delay == pytest.approx(18 / 35 - 0.10, abs=1e-6)
    # dwell covers lead + door (6 cm / 35 cm/s = 0.171 s) + 2 x settle 0.12 s = 0.51 s >= default 0.5 s
    assert dwell == pytest.approx(0.10 + 6 / 35 + 0.24, abs=1e-6)
    assert dwell >= cfg.gate.default_dwell_s


def test_gate_schedule_accounts_for_position_in_roi(cfg):
    d_up, _ = timing.gate_schedule(0.0, 400, cfg)     # bean just entered the zone (9 cm): furthest from the door
    d_mid, _ = timing.gate_schedule(0.0, 640, cfg)
    d_down, _ = timing.gate_schedule(0.0, 880, cfg)   # bean about to leave the zone (15 cm)
    assert d_up > d_mid > d_down
    assert d_up - d_down == pytest.approx(6 / 35, abs=1e-6)  # the 6 cm zone at 35 cm/s


def test_gate_schedule_elapsed_time_and_floor(cfg):
    d0, _ = timing.gate_schedule(10.0, 640, cfg, now_t=10.0)
    d1, _ = timing.gate_schedule(10.0, 640, cfg, now_t=10.2)
    assert d1 == pytest.approx(d0 - 0.2, abs=1e-9)
    late, dwell = timing.gate_schedule(10.0, 880, cfg, now_t=12.0)
    assert late == 0.0 and dwell > 0


def test_gate_schedule_kinematic_model_is_earlier(cfg):
    d_meas, _ = timing.gate_schedule(0.0, 640, cfg)
    d_kin, _ = timing.gate_schedule(0.0, 640, cfg, model="kinematic")
    assert 0 < d_kin < d_meas


def test_summary_is_json_friendly(cfg):
    s = timing.summary(cfg)
    assert set(s) >= {"zone_to_door_s", "mu_roll_fitted", "door_open_window_s"}
    assert all(isinstance(v, (int, float)) and math.isfinite(v) for v in s.values())
