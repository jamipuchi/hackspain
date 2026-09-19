"""Closed loop on synthetic frames: one bean → one verdict; a burnt bean → one dry-run gate pulse; never twice per bean."""
import time

import cv2
import numpy as np

from line import _stubs, config
from line.contracts import Frame, now
from line.pipeline import SortingLine, flow_fraction, fallback_gate_schedule
from line.contracts import Blob, ROI


def make(cfg, t, u=None, dark=False):
    img = np.full((cfg.camera.height, cfg.camera.width, 3), 235, np.uint8)
    if u is not None:
        ppm = cfg.camera.px_per_mm
        cv2.ellipse(img, (int(u), (cfg.camera.zone[1] + cfg.camera.zone[3]) // 2), (int(6 * ppm), int(4 * ppm)), 0, 0, 360, (35, 30, 30) if dark else (70, 95, 140), -1)
    return Frame(img, t, int(t * 1000), "test")


def build(cfg):
    cfg.act_on = "suspect"  # these tests exercise sorting behaviour, not bring-up
    cfg.door_policy = "pulse"
    cfg.timing.mode = "model"
    ard = _stubs.StubArduino()
    gate = _stubs.StubGate(ard, cfg, dry_run=True)
    line = SortingLine(cfg, _stubs.StubCamera(cfg), _stubs.StubDetector(cfg), _stubs.StubClassifier(cfg), gate, ard)
    line.enabled = True
    return line, gate


def roll(line, cfg, dark, dt=0.02):
    x0, _, x1, _ = cfg.camera.zone
    t = now()
    verdicts = []
    for u in range(x0 - 60, x1 + 60, 12):  # enters partial, crosses, leaves
        v = line.step(make(cfg, t, u, dark))
        if v:
            verdicts.append(v)
        t += dt
    for _ in range(30):  # empty frames → back to armed
        line.step(make(cfg, t))
        t += dt
    return verdicts


def test_good_bean_one_verdict_no_gate():
    cfg = config.LineConfig()
    line, gate = build(cfg)
    verdicts = roll(line, cfg, dark=False)
    assert len(verdicts) == 1 and not verdicts[0].suspect, [v.reason for v in verdicts]
    assert line.counters["beans"] == 1 and line.counters["good"] == 1 and line.counters["gate_pulses"] == 0
    assert line.state == "armed"


def test_burnt_bean_schedules_one_dry_pulse():
    cfg = config.LineConfig()
    cfg.timing.bean_speed_cm_s = 1000.0  # make the scheduled pulse fire within the test
    line, gate = build(cfg)
    verdicts = roll(line, cfg, dark=True)
    assert len(verdicts) == 1 and verdicts[0].suspect, [v.reason for v in verdicts]
    assert line.counters["gate_pulses"] == 1
    time.sleep(cfg.gate.default_dwell_s + 0.3)
    kinds = [e.split()[0] for _, e in gate.events]
    assert kinds == ["open", "flush"], gate.events
    assert all("(dry)" in e for _, e in gate.events)
    assert line.arduino.log == []  # dry run never touched the Arduino


def test_two_beans_two_verdicts():
    cfg = config.LineConfig()
    line, gate = build(cfg)
    roll(line, cfg, dark=False)
    roll(line, cfg, dark=True)
    assert line.counters["beans"] == 2 and line.counters["good"] == 1 and line.counters["suspect"] == 1


def test_disabled_line_only_previews():
    cfg = config.LineConfig()
    line, gate = build(cfg)
    line.enabled = False
    assert roll(line, cfg, dark=True) == []
    assert line.counters["beans"] == 1 and line.counters["gate_pulses"] == 0
    assert line.latest_jpeg() is not None


def test_flow_fraction_and_schedule():
    cfg = config.LineConfig()
    roi = ROI(100, 0, 300, 100)
    b = Blob(150, 50, (0, 0, 1, 1), 1, False)
    assert abs(flow_fraction(b, roi, "x") - 0.25) < 1e-9
    assert abs(flow_fraction(b, roi, "-x") - 0.75) < 1e-9
    delay, dwell = fallback_gate_schedule(0.5, cfg)  # bean at 12 cm, door at 30 cm, 35 cm/s, lead from cfg (derived by timing.py)
    assert abs(delay - max(0.0, (30 - 12) / 35 - cfg.timing.door_lead_s)) < 1e-6 and dwell == cfg.gate.default_dwell_s


def test_back_to_back_beans_each_get_a_verdict():
    """Second bean enters the zone while the first is still leaving it: two beans, two verdicts."""
    cfg = config.LineConfig()
    line, gate = build(cfg)
    x0, y0, x1, y1 = cfg.camera.zone
    ppm = cfg.camera.px_per_mm
    vmid = (y0 + y1) // 2
    t = now()
    verdicts = []
    for k in range(0, 60):
        u1 = x0 - 60 + k * 12
        u2 = u1 - 220  # trailing bean 220 px behind
        img = np.full((cfg.camera.height, cfg.camera.width, 3), 235, np.uint8)
        for u, dark in ((u1, False), (u2, True)):
            cv2.ellipse(img, (int(u), vmid), (int(6 * ppm), int(4 * ppm)), 0, 0, 360, (35, 30, 30) if dark else (70, 95, 140), -1)
        v = line.step(Frame(img, t, k, "test"))
        if v:
            verdicts.append(v)
        t += 0.02
    assert [v.suspect for v in verdicts] == [False, True], [v.reason for v in verdicts]
    assert line.counters["beans"] == 2


def test_state_policy_moves_only_on_change():
    cfg = config.LineConfig()
    cfg.door_policy = "state"
    line, gate = build(cfg)
    cfg.door_policy = "state"
    roll(line, cfg, dark=True)   # bad → door to the bad side
    roll(line, cfg, dark=True)   # bad again → nothing moves
    roll(line, cfg, dark=False)  # good → door to the good side
    kinds = [e.kind for e in line.events if e.kind in ("door_set", "door_kept")]
    assert kinds == ["door_set", "door_kept", "door_set"], kinds
    assert line.counters["gate_pulses"] == 2
    time.sleep(0.3)
    acts = [e.split()[0] for _, e in gate.events]
    assert acts == ["open", "flush"], gate.events  # no automatic return between beans


def test_static_object_is_ignored_and_real_bean_still_triggers():
    cfg = config.LineConfig()
    cfg.trigger_on = "seen"
    line, gate = build(cfg)
    cfg.act_on = "all"
    x0, y0, x1, y1 = cfg.camera.zone
    ppm = cfg.camera.px_per_mm
    t = now()
    # a dark smudge sits still inside the zone for 2 s
    for k in range(60):
        img = np.full((cfg.camera.height, cfg.camera.width, 3), 235, np.uint8)
        cv2.ellipse(img, (x0 + 60, y0 + 40), (int(5 * ppm), int(3 * ppm)), 0, 0, 360, (40, 40, 40), -1)
        line.step(Frame(img, t, k, "t"))
        t += 0.04
    assert any(e.kind == "static_object" for e in line.events)
    assert line.state == "armed" and line.counters["gate_pulses"] == 0
    # now a bean rolls through while the smudge is still there
    for k in range(40):
        img = np.full((cfg.camera.height, cfg.camera.width, 3), 235, np.uint8)
        cv2.ellipse(img, (x0 + 60, y0 + 40), (int(5 * ppm), int(3 * ppm)), 0, 0, 360, (40, 40, 40), -1)
        cv2.ellipse(img, (x1 + 40 - k * 12, (y0 + y1) // 2 + 60), (int(6 * ppm), int(4 * ppm)), 0, 0, 360, (70, 95, 140), -1)
        line.step(Frame(img, t, 100 + k, "t"))
        t += 0.03
    assert line.counters["gate_pulses"] == 1, line.counters
