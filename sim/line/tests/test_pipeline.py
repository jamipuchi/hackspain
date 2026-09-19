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
    delay, dwell = fallback_gate_schedule(0.5, cfg)  # bean at 12 cm, door at 30 cm, 35 cm/s, 0.1 s lead
    assert abs(delay - ((30 - 12) / 35 - 0.1)) < 1e-6 and dwell == cfg.gate.default_dwell_s
