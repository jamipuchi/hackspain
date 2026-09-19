"""timing.py against the build agent's numbers (BUILD_ASBUILT.md): 15° (12/15/18/21° blocks), zone 9–15 cm,
door 30–36 cm, drag model v' = a − 1.6 v with a = 64 cm/s² at 15°."""
import math

import pytest

from line import timing
from line.config import LineConfig


@pytest.fixture
def cfg():
    c = LineConfig()
    c.gate.settle_ms = 400          # stock firmware ramp; the arduino agent flips this to 120 with ramp_override
    c.timing.door_lead_s = 0.0
    c.timing.speed_model = "drag"
    return c


# ------------------------------------------------------------------ drag model vs the build agent's table
BUILD_TABLE = {  # slope: (a, top->zone centre, zone centre->door start, speed at zone)
    12: (48, 0.85, 0.70, 22), 15: (64, 0.72, 0.55, 27), 18: (80, 0.64, 0.47, 31), 21: (96, 0.58, 0.42, 34)}


@pytest.mark.parametrize("slope", sorted(BUILD_TABLE))
def test_drag_model_matches_build_table(cfg, slope):
    a, t_zone, t_door, v_zone = BUILD_TABLE[slope]
    cfg.timing.slope_deg = slope
    assert timing.drag_accel_cm_s2(cfg) == pytest.approx(a, abs=0.5)
    assert timing.travel_time_s(0, 12, cfg) == pytest.approx(t_zone, abs=0.03)
    assert timing.zone_to_door_s(cfg) == pytest.approx(t_door, abs=0.03)
    assert timing.speed_at_cm(12, cfg) == pytest.approx(v_zone, abs=2.5)


def test_drag_terminal_speed(cfg):
    assert timing.drag_terminal_cm_s(cfg) == pytest.approx(40.0)
    assert timing.drag_speed_at_t(10.0, cfg) == pytest.approx(40.0, abs=1e-3)
    # s(t) and t(s) are inverses
    for s in (1.0, 12.0, 30.0):
        assert timing.drag_distance_at_t(timing.drag_time_at_s(s, cfg), cfg) == pytest.approx(s, abs=1e-6)


def test_table_covers_all_tilt_blocks(cfg):
    rows = timing.table(cfg)
    assert [r["slope_deg"] for r in rows] == [12, 15, 18, 21]
    assert rows[1]["zone_to_door_s"] == pytest.approx(0.55, abs=0.03)
    assert rows[0]["zone_to_door_s"] > rows[1]["zone_to_door_s"] > rows[2]["zone_to_door_s"] > rows[3]["zone_to_door_s"]
    # with the stock 0.40 s swing, 18° and 21° leave no time to photograph and classify
    assert rows[1]["budget_s"] == pytest.approx(0.55 - 0.40, abs=0.03)
    assert rows[3]["budget_s"] < 0.05


def test_calibrate_drag_folds_in_a_stopwatch(cfg):
    a15 = timing.calibrate_drag(cfg, 0.62)
    assert timing.zone_to_door_s(cfg) == pytest.approx(0.62, abs=1e-3)
    assert a15 < 64.0


# ------------------------------------------------------------------ other models
def test_measured_model(cfg):
    cfg.timing.speed_model = "measured"
    assert timing.zone_to_door_s(cfg) == pytest.approx(18 / 35, abs=1e-9)
    assert timing.speed_at_cm(30, cfg) == 35.0


def test_refresh_writes_active_model(cfg):
    cfg.timing.zone_to_door_s = 0.0
    v = timing.refresh(cfg)
    assert v == cfg.timing.zone_to_door_s == pytest.approx(0.567, abs=0.01)
    assert timing.refresh(cfg, "measured") == pytest.approx(0.5143, abs=1e-3)


def test_rolling_fit_reproduces_measured_speed(cfg):
    mu = timing.fit_rolling_friction(cfg)
    assert 0 < mu < math.tan(math.radians(cfg.timing.slope_deg))
    assert timing.speed_at_cm(12, cfg, "rolling") == pytest.approx(cfg.timing.bean_speed_cm_s, rel=1e-6)
    assert timing.speed_at_cm(30, cfg, "rolling") > timing.speed_at_cm(12, cfg, "rolling")


def test_frictionless_sanity():
    v = timing.speed_from_rest(12, 15, 0.0)
    assert v == pytest.approx(math.sqrt(2 * 981 * math.sin(math.radians(15)) * 12 / 1.4), rel=1e-9)


def test_travel_time_is_monotonic_and_zero_backwards(cfg):
    for m in ("drag", "measured", "rolling"):
        assert timing.travel_time_s(20, 10, cfg, m) == 0.0
        assert 0 < timing.travel_time_s(12, 20, cfg, m) < timing.travel_time_s(12, 30, cfg, m)


# ------------------------------------------------------------------ ROI -> chute position
@pytest.mark.parametrize("axis,px,expected", [
    ("x", 400, 0.0), ("x", 880, 1.0), ("x", 640, 0.5), ("+x", 520, 0.25),
    ("-x", 400, 1.0), ("-x", 880, 0.0),
    ("y", 200, 0.0), ("y", 520, 1.0), ("-y", 200, 1.0),
    ("x", -50, 0.0), ("x", 5000, 1.0),
])
def test_roi_fraction(cfg, axis, px, expected):
    cfg.camera.flow_axis = axis
    assert timing.roi_fraction(px, cfg) == pytest.approx(expected)


def test_bean_position_maps_roi_to_chute(cfg):
    assert timing.bean_position_cm(400, cfg) == pytest.approx(9.0)
    assert timing.bean_position_cm(880, cfg) == pytest.approx(15.0)
    assert timing.bean_position_cm(640, cfg) == pytest.approx(12.0)


# ------------------------------------------------------------------ gate schedule
def test_lead_comes_from_gate_settle(cfg):
    assert timing.door_lead_s(cfg) == pytest.approx(0.40)
    cfg.gate.settle_ms = 120
    assert timing.door_lead_s(cfg) == pytest.approx(0.12)
    cfg.timing.door_lead_s = 0.05
    assert timing.door_lead_s(cfg) == pytest.approx(0.17)


def test_gate_schedule_centre_of_zone(cfg):
    t_zd = timing.zone_to_door_s(cfg)
    delay, dwell = timing.gate_schedule(100.0, 640, cfg)
    assert delay == pytest.approx(t_zd - 0.40, abs=1e-6)
    v_door = timing.speed_at_cm(30, cfg)
    assert dwell == pytest.approx(max(0.40 + 6 / v_door, cfg.gate.default_dwell_s), abs=1e-6)
    assert dwell >= cfg.gate.default_dwell_s


def test_gate_schedule_fast_door_after_ramp_override(cfg):
    cfg.gate.settle_ms = 120
    delay, _ = timing.gate_schedule(0.0, 640, cfg)
    assert delay == pytest.approx(timing.zone_to_door_s(cfg) - 0.12, abs=1e-6)
    assert 0.40 < delay < 0.50


def test_gate_schedule_accounts_for_position_in_roi(cfg):
    d_up, _ = timing.gate_schedule(0.0, 400, cfg)
    d_mid, _ = timing.gate_schedule(0.0, 640, cfg)
    d_down, _ = timing.gate_schedule(0.0, 880, cfg)
    assert d_up > d_mid > d_down
    assert d_up - d_down == pytest.approx(timing.travel_time_s(9, 15, cfg), abs=1e-6)


def test_gate_schedule_reads_slope(cfg):
    cfg.timing.slope_deg = 12
    d12, _ = timing.gate_schedule(0.0, 640, cfg)
    cfg.timing.slope_deg = 21
    d21, _ = timing.gate_schedule(0.0, 640, cfg)
    assert d12 > d21 and d12 == pytest.approx(0.70 - 0.40, abs=0.03)
    assert d21 < 0.06   # 0.42 s transit, 0.40 s swing: open (almost) at once


def test_gate_schedule_elapsed_time_and_floor(cfg):
    cfg.gate.settle_ms = 120
    d0, _ = timing.gate_schedule(10.0, 640, cfg, now_t=10.0)
    d1, _ = timing.gate_schedule(10.0, 640, cfg, now_t=10.2)
    assert d1 == pytest.approx(d0 - 0.2, abs=1e-9)
    late, dwell = timing.gate_schedule(10.0, 880, cfg, now_t=12.0)
    assert late == 0.0 and dwell > 0


def test_budget(cfg):
    assert timing.budget_s(cfg) == pytest.approx(timing.zone_to_door_s(cfg) - 0.40, abs=1e-9)
    cfg.gate.settle_ms = 120
    assert timing.budget_s(cfg) > 0.4


def test_summary_is_json_friendly(cfg):
    s = timing.summary(cfg)
    assert s["speed_model"] == "drag"
    assert all(isinstance(v, (int, float, str)) for v in s.values())
    assert all(math.isfinite(v) for v in s.values() if isinstance(v, float))
